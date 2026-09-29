#!/usr/bin/env python3
"""Build the control panel (results/eth15/control_panel.html) from the logged results: every survivor's bets are
embedded as win/loss sequences so bankroll paths are recomputed in the browser for any Kelly multiplier, payout and fee.

Usage: python3 experiments/eth15/build_panel.py
"""
import json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lib  # noqa: E402

RES = "results/eth15"
HERE = os.path.dirname(os.path.abspath(__file__))


def deltas(idx):
    out, prev = [], 0
    for i in idx:
        out.append(i - prev)
        prev = i
    return out


def describe(fam, p):
    if fam == "rsi":
        lo = p["lo"]
        return f"RSI(3) of 15-minute closes below {lo}: bet Up. Above {100 - lo}: bet Down."
    if fam == "streak":
        return f"After {p['n']} or more candles in the same direction: bet the other way."
    if fam == "funding":
        return f"On the first candle after a funding settlement, when the rate is beyond {p['k']} bps: bet with its sign."
    if fam in ("logit", "lgbm"):
        name = "Logistic regression" if fam == "logit" else "LightGBM"
        t = p["threshold"]
        return f"{name} on 24 features: bet Up when P(up) > {t:.2f}, Down when P(up) < {1 - t:.2f}."
    return f"{fam} {p}"


def ledger_rows():
    rows = []
    for line in open("docs/eth15/ledger.md"):
        m = re.match(r"^\| (\d+) \| ([^|]+) \| (.+) \| (.+) \|\s*$", line)
        if m:
            rows.append({"n": int(m.group(1)), "time": m.group(2).strip(), "item": m.group(3).strip(),
                         "decision": m.group(4).strip()})
    return rows


def main():
    H = json.load(open(os.path.join(RES, "holdout_results.json")))
    B = json.load(open(os.path.join(RES, "bets.json")))["bets"]
    split = json.load(open(os.path.join(RES, "split.json")))
    srcs = json.load(open(os.path.join(RES, "posthoc_sources.json")))
    lat = json.load(open(os.path.join(RES, "posthoc_latency.json")))
    log = [json.loads(l) for l in open(os.path.join(RES, "research_log.jsonl"))]
    meta = H["meta"]
    strategies = []
    for r in H["results"]:
        b = B[r["id"]]
        strategies.append({
            "id": r["id"], "family": r["family"], "params": r["params"], "rule": describe(r["family"], r["params"]),
            "research_n": r["research_n"], "research_wr": r["research_win_rate"],
            "hold_n": r["holdout_n"], "hold_wins": r["holdout_wins"], "hold_wr": r["holdout_win_rate"],
            "ci": r["wilson95"], "p": r["p_value_vs_break_even"], "passes": r["passes_57"],
            "above_be": r["above_break_even"], "half_kelly": r["half_kelly_fraction"],
            "h1": r["holdout_h1"], "h2": r["holdout_h2"],
            "hold": {"d": deltas(b["hold_idx"]), "w": b["hold_win"], "s": b["hold_dir"]},
            "res": {"d": deltas(b["res_idx"]), "w": b["res_win"]},
        })
    L = [{"id": r["id"], "fam": r["family"], "params": json.dumps(r["params"], separators=(",", ":")),
          "n": r["n"], "wr": r["win_rate"], "h1": r["wr_h1"], "h2": r["wr_h2"], "surv": r["survivor"],
          "kind": r["kind"]} for r in log]
    data = {"meta": meta, "split": split, "strategies": strategies, "log": L, "sources": srcs, "latency": lat,
            "ledger": ledger_rows(), "break_even": lib.BREAK_EVEN, "bar": lib.BAR, "min_bets": lib.MIN_BETS,
            "hold_t0": int(__import__("pandas").Timestamp(split["holdout_from"]).value // 10**6),
            "res_t0": int(__import__("pandas").Timestamp(split["research_from"]).value // 10**6)}
    html = open(os.path.join(HERE, "panel_template.html")).read()
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    out = html.replace("__DATA__", blob)
    path = os.path.join(RES, "control_panel.html")
    open(path, "w").write(out)
    print(path, f"{len(out) / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
