"""Overnight review: read the day's logs, measure calibration and P&L, propose changes behind an approval gate.

Outputs
  results/agent_review/<date>.md        - the report (Brier score, reliability table, hit rate, P&L, fees, hygiene)
  results/agent_review/<date>.proposals.json - candidate policy changes (TUNABLE keys only) + a rule-judge calibration table
Apply:  PYTHONPATH=src python3 -m hlagent.review --out data/agent --apply <proposals.json> --approve
The apply step refuses anything outside PolicyConfig.TUNABLE and never touches risk limits.

Calibration is directional: for each decision the probability assigned to the predicted direction q = max(p_up, 1-p_up)
is compared with whether that direction was realised. Brier = mean((p_up - y)^2) over resolved non-neutral decisions;
the reference is the 0.5 forecast (Brier 0.25); skill = 1 - Brier / 0.25.
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional
from .policy import KELLY_HARD_CAP, PolicyConfig

BINS = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0001)


def load_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def brier(pairs: list[tuple[float, int]]) -> Optional[float]:
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def reliability(rows: list[dict]) -> list[dict]:
    """Directional reliability table over resolved non-neutral decisions."""
    out = []
    for lo, hi in zip(BINS[:-1], BINS[1:]):
        sel = []
        for r in rows:
            p = r["p_up"]
            q = max(p, 1.0 - p)
            hit = r["y"] if p >= 0.5 else 1 - r["y"]
            if lo <= q < hi:
                sel.append((q, hit))
        n = len(sel)
        out.append({"bin_lo": lo, "bin_hi": min(hi, 1.0), "n": n,
                    "mean_conf": sum(q for q, _ in sel) / n if n else None,
                    "obs_freq": sum(h for _, h in sel) / n if n else None})
    return out


@dataclass
class ReviewResult:
    date: str
    stats: dict
    table: list[dict]
    proposals: dict
    markdown: str
    warnings: list[str] = field(default_factory=list)


def review(out_dir: str, policy: PolicyConfig, date: Optional[str] = None, min_bucket_n: int = 30) -> ReviewResult:
    date = date or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    outcomes = load_jsonl(os.path.join(out_dir, "outcomes.jsonl"))
    decisions = load_jsonl(os.path.join(out_dir, "decisions.jsonl"))
    fills = load_jsonl(os.path.join(out_dir, "fills.jsonl"))
    equity = load_jsonl(os.path.join(out_dir, "equity.jsonl"))
    kills = load_jsonl(os.path.join(out_dir, "events.jsonl"))
    warnings: list[str] = []
    synthetic = [o for o in outcomes if o.get("synthetic")]
    if synthetic:
        warnings.append(f"{len(synthetic)} of {len(outcomes)} outcomes are SYNTHETIC: this report is a plumbing check, not evidence")
    real = [o for o in outcomes if not o.get("synthetic")] or outcomes
    directional = [o for o in real if o["direction"] != "neutral"]
    pairs = [(o["p_up"], o["y"]) for o in directional]
    b = brier(pairs)
    skill = None if b is None else 1.0 - b / 0.25
    hit = sum((o["y"] if o["p_up"] >= 0.5 else 1 - o["y"]) for o in directional) / len(directional) if directional else None
    by_source = defaultdict(list); by_regime = defaultdict(list)
    for o in directional:
        by_source[o["source"]].append((o["p_up"], o["y"])); by_regime[o["regime"]].append((o["p_up"], o["y"]))
    table = reliability(directional)
    closes = [f for f in fills if f["action"] in ("close", "flip")]
    fees = sum(f["fee_usd"] for f in fills)
    realised = sum(f["realised_usd"] for f in fills)
    wins = [f["realised_usd"] for f in closes if f["realised_usd"] > 0]
    losses = [-f["realised_usd"] for f in closes if f["realised_usd"] < 0]
    eq = [e["equity"] for e in equity]
    max_dd = 0.0; peak = eq[0] if eq else 0.0
    for v in eq:
        peak = max(peak, v); max_dd = max(max_dd, (peak - v) / peak * 100 if peak > 0 else 0.0)
    stats = {
        "n_decisions": len(decisions), "n_outcomes": len(outcomes), "n_directional_resolved": len(directional),
        "hit_rate": hit, "brier": b, "brier_ref": 0.25 if b is not None else None, "brier_skill": skill,
        "by_source": {k: {"n": len(v), "brier": brier(v)} for k, v in by_source.items()},
        "by_regime": {k: {"n": len(v), "brier": brier(v)} for k, v in by_regime.items()},
        "n_fills": len(fills), "n_closed_trades": len(closes), "fees_usd": fees, "realised_usd": realised,
        "net_after_fees_usd": realised - fees, "mean_win": sum(wins) / len(wins) if wins else None,
        "mean_loss": sum(losses) / len(losses) if losses else None,
        "equity_start": eq[0] if eq else None, "equity_end": eq[-1] if eq else None, "max_drawdown_pct": max_dd,
        "kills": [k for k in kills if k.get("event") in ("kill",)], "synthetic_share": len(synthetic) / len(outcomes) if outcomes else 0.0,
    }
    # ---- proposals (TUNABLE keys only; the operator applies them)
    proposals: dict = {"date": date, "policy_updates": {}, "reasons": [], "rule_calibration": {}, "do_not_arm_live": True}
    overconfident = [row for row in table if row["n"] >= min_bucket_n and row["obs_freq"] is not None and row["obs_freq"] < row["bin_lo"] - 0.05]
    if overconfident:
        proposals["policy_updates"]["min_confidence"] = round(min(0.95, policy.min_confidence + 0.05), 3)
        proposals["reasons"].append("overconfident bucket(s): " + ", ".join(f"[{r['bin_lo']:.1f},{r['bin_hi']:.1f}) obs {r['obs_freq']:.2f} n={r['n']}" for r in overconfident))
    if wins and losses and len(closes) >= 30:
        proposals["policy_updates"]["payoff_ratio"] = round((sum(wins) / len(wins)) / (sum(losses) / len(losses)), 3)
        proposals["reasons"].append(f"payoff_ratio measured from {len(closes)} closed trades")
    for row in table:
        if row["n"] >= 50:
            proposals["rule_calibration"][f"{row['bin_lo']:.2f}"] = round(row["obs_freq"], 3)
    n_real = len([o for o in directional if not o.get("synthetic")])
    if skill is not None and skill > 0 and n_real >= 200 and (realised - fees) > 0 and not synthetic:
        proposals["do_not_arm_live"] = False
        proposals["reasons"].append(f"positive Brier skill {skill:.3f} on {n_real} real decisions and positive net P&L")
    else:
        proposals["reasons"].append("do not arm live: need Brier skill > 0 on >= 200 real resolved decisions and net P&L > 0 after fees")
    md = _markdown(date, stats, table, proposals, warnings)
    return ReviewResult(date=date, stats=stats, table=table, proposals=proposals, markdown=md, warnings=warnings)


def _fmt(x, nd=3):
    return "n/a" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _markdown(date, stats, table, proposals, warnings) -> str:
    lines = [f"# Overnight review {date}", ""]
    for w in warnings:
        lines.append(f"> WARNING: {w}")
    lines += ["", "## Calibration", "",
              f"* resolved directional decisions: {stats['n_directional_resolved']} of {stats['n_outcomes']} outcomes ({stats['n_decisions']} decisions logged)",
              f"* hit rate: {_fmt(stats['hit_rate'])}",
              f"* Brier: {_fmt(stats['brier'])} (reference 0.250); skill: {_fmt(stats['brier_skill'])}", "",
              "| confidence bin | n | mean confidence | observed frequency |", "|---|---|---|---|"]
    for r in table:
        lines.append(f"| [{r['bin_lo']:.1f}, {r['bin_hi']:.1f}) | {r['n']} | {_fmt(r['mean_conf'])} | {_fmt(r['obs_freq'])} |")
    lines += ["", "### By source", ""] + [f"* {k}: n={v['n']} Brier={_fmt(v['brier'])}" for k, v in stats["by_source"].items()]
    lines += ["", "### By regime", ""] + [f"* {k}: n={v['n']} Brier={_fmt(v['brier'])}" for k, v in stats["by_regime"].items()]
    lines += ["", "## Trading", "",
              f"* fills: {stats['n_fills']}; closed trades: {stats['n_closed_trades']}",
              f"* realised: {_fmt(stats['realised_usd'], 2)} USD; fees: {_fmt(stats['fees_usd'], 2)} USD; net: {_fmt(stats['net_after_fees_usd'], 2)} USD",
              f"* mean win / mean loss: {_fmt(stats['mean_win'], 2)} / {_fmt(stats['mean_loss'], 2)}",
              f"* equity: {_fmt(stats['equity_start'], 2)} -> {_fmt(stats['equity_end'], 2)}; max drawdown {_fmt(stats['max_drawdown_pct'], 2)}%",
              f"* kill events: {len(stats['kills'])}", "",
              "## Proposals (apply only with --approve; risk limits are never proposed)", "",
              "```json", json.dumps(proposals, indent=1), "```", ""]
    return "\n".join(lines)


def write_review(res: ReviewResult, results_dir: str = "results/agent_review") -> tuple[str, str]:
    os.makedirs(results_dir, exist_ok=True)
    md_path = os.path.join(results_dir, f"{res.date}.md")
    pj_path = os.path.join(results_dir, f"{res.date}.proposals.json")
    with open(md_path, "w") as f:
        f.write(res.markdown)
    with open(pj_path, "w") as f:
        json.dump(res.proposals, f, indent=1)
    return md_path, pj_path


def apply_proposal(proposal_path: str, policy_path: str, approve: bool) -> PolicyConfig:
    """Operator gate: refuses without approve=True; refuses non-tunable keys; re-validates the config."""
    if not approve:
        raise PermissionError("proposal not applied: pass approve=True (CLI: --approve)")
    with open(proposal_path) as f:
        prop = json.load(f)
    updates = prop.get("policy_updates") or prop.get("proposed_updates") or {}
    bad = [k for k in updates if k not in PolicyConfig.TUNABLE]
    if bad:
        raise ValueError(f"refusing non-tunable keys: {bad}")
    if "kelly_fraction" in updates and updates["kelly_fraction"] > KELLY_HARD_CAP:
        raise ValueError("kelly_fraction above the hard cap")
    base = PolicyConfig.from_json(policy_path) if os.path.exists(policy_path) else PolicyConfig()
    new = base.with_updates(**updates)
    new.to_json(policy_path)
    with open(policy_path + ".history", "a") as f:
        f.write(json.dumps({"applied": dt.datetime.now(dt.timezone.utc).isoformat(), "from": proposal_path, "updates": updates}) + "\n")
    return new


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/agent")
    ap.add_argument("--policy", default="config/policy.json")
    ap.add_argument("--results", default="results/agent_review")
    ap.add_argument("--date", default=None)
    ap.add_argument("--apply", default=None, help="proposals JSON to apply to the policy file")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--brain", action="store_true", help="also ask the BRAIN for a narrative proposal")
    a = ap.parse_args(argv)
    if a.apply:
        new = apply_proposal(a.apply, a.policy, a.approve)
        print(json.dumps(new.__dict__, indent=1)); return
    policy = PolicyConfig.from_json(a.policy) if os.path.exists(a.policy) else PolicyConfig()
    res = review(a.out, policy, a.date)
    md_path, pj_path = write_review(res, a.results)
    if a.brain:
        from .brain import Brain
        prop = Brain().nightly_review(res.markdown, policy)
        if prop is not None:
            with open(os.path.join(a.results, f"{res.date}.brain.json"), "w") as f:
                json.dump(prop.model_dump(), f, indent=1)
    print(res.markdown)
    print(f"\nwritten {md_path} and {pj_path}")


if __name__ == "__main__":
    main()
