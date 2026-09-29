#!/usr/bin/env python3
"""Render markdown tables from results/pine for docs/pine/report.md: top 10, robustness and trade detail from the
audit, coverage by script, and the secondary questions.

Usage: python3 experiments/pine/report_tables.py [results/pine] > /tmp/tables.md
"""
import json, math, os, sys
import pandas as pd

R = sys.argv[1] if len(sys.argv) > 1 else "results/pine"
STATUS_ORDER = ["not_run_bar_cap", "parse_error", "unsupported", "compile_error", "crash", "memory_limit", "timeout",
                "runtime_error", "ok"]


def isnan(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


def pct(x, nd=0):
    return "n/a" if isnan(x) else f"{100 * x:.{nd}f}%"


def num(x, nd=2):
    return "n/a" if isnan(x) else f"{x:.{nd}f}"


def tf_label(ms):
    ms = int(ms)
    for u, n in ((86_400_000, "d"), (3_600_000, "h"), (60_000, "m")):
        if ms % u == 0:
            return f"{ms // u}{n}"
    return str(ms)


def short_share(d):
    nl, ns = d.get("net_long"), d.get("net_short")
    if nl is None or ns is None or (nl + ns) == 0:
        return "n/a"
    return "none" if ns == 0 and d.get("long_share") == 1.0 else pct(ns / (nl + ns))


def top_table(top):
    out = ["| # | Script (vault file) | TF | Published | OOS days | OOS Sharpe BTC / ETH | Buy & hold Sharpe BTC / ETH | "
           "OOS CAGR BTC / ETH | Max DD BTC / ETH | Trades BTC / ETH | Deflated Sharpe (null) BTC / ETH |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, r in enumerate(top.itertuples(), 1):
        lm = pd.to_datetime(r.last_modified, unit="ms") if isinstance(r.last_modified, (int, float)) else pd.to_datetime(r.last_modified)
        out.append(f"| {k} | `{r.file}` | {tf_label(r.tf_ms)} | {str(lm)[:10]} | {int(r.oos_days)} | "
                   f"{num(r.oos_sharpe_btc)} / {num(r.oos_sharpe_eth)} | {num(r.bh_sharpe_btc)} / {num(r.bh_sharpe_eth)} | "
                   f"{pct(r.oos_cagr_btc)} / {pct(r.oos_cagr_eth)} | {pct(r.oos_mdd_btc)} / {pct(r.oos_mdd_eth)} | "
                   f"{int(r.oos_trades_btc)} / {int(r.oos_trades_eth)} | "
                   f"{num(getattr(r, 'dsr_null_btc', float('nan')))} / {num(getattr(r, 'dsr_null_eth', float('nan')))} |")
    return "\n".join(out)


def audit_tables(top, audit):
    A = {a["file"]: a for a in audit}
    rob = ["| # | Script | Beats B&H Sharpe on both | PSR BTC / ETH | Alpha/yr BTC / ETH | Beta BTC / ETH | "
           "SOL OOS Sharpe | OOS Sharpe at 2x costs BTC / ETH | Full 2021-26 Sharpe BTC / ETH |",
           "|---|---|---|---|---|---|---|---|---|"]
    det = ["| # | Script | Long share of OOS trades | Short side's share of net | Top-5 trades' share of net | "
           "Median hold (h) | Win rate | Static flags |",
           "|---|---|---|---|---|---|---|---|"]
    for k, r in enumerate(top.itertuples(), 1):
        a = A.get(r.file)
        beats = (r.oos_sharpe_btc > r.bh_sharpe_btc) and (r.oos_sharpe_eth > r.bh_sharpe_eth)
        if a is None:
            rob.append(f"| {k} | {r.name if hasattr(r, 'name') else r.file} | {'yes' if beats else 'no'} | n/a | n/a | n/a | n/a | n/a | n/a |")
            continue
        g = lambda key, part, m: (a.get(key) or {}).get(part, {}) and (a.get(key) or {}).get(part, {}).get(m)
        rob.append(f"| {k} | {a['name'][:60]} | {'yes' if beats else 'no'} | "
                   f"{num(getattr(r, 'psr_btc', float('nan')))} / {num(getattr(r, 'psr_eth', float('nan')))} | "
                   f"{pct(r.alpha_btc)} / {pct(r.alpha_eth)} | {num(r.beta_btc)} / {num(r.beta_eth)} | "
                   f"{num(g('SOLUSDT_fee7bps', 'oos', 'sharpe'))} | "
                   f"{num(g('BTCUSDT_fee14bps', 'oos', 'sharpe'))} / {num(g('ETHUSDT_fee14bps', 'oos', 'sharpe'))} | "
                   f"{num(r.full_sharpe_btc)} / {num(r.full_sharpe_eth)} |")
        d = (a.get("BTCUSDT_fee7bps") or {}).get("oos_trades_detail") or {}
        flags = [f for f, v in a.get("static", {}).items() if v]
        det.append(f"| {k} | {a['name'][:60]} | {pct(d.get('long_share'))} | "
                   f"{short_share(d)} | {pct(d.get('top5_share_of_net'))} | "
                   f"{num(d.get('median_hold_h'), 1)} | {pct(d.get('win_rate'))} | {', '.join(flags) or 'none'} |")
    return "\n".join(rob), "\n".join(det)


def coverage(M):
    st = M.pivot_table(index="file", columns="symbol", values="status", aggfunc="first")
    rank = {s: i for i, s in enumerate(STATUS_ORDER)}

    def worst(row):
        vals = [v for v in row.values if isinstance(v, str)]
        return min(vals, key=lambda v: rank.get(v, -1)) if vals else "missing"
    w = st.apply(worst, axis=1).value_counts()
    out = ["| Outcome (worst of BTC and ETH) | Scripts |", "|---|---|"]
    for s in STATUS_ORDER + [x for x in w.index if x not in STATUS_ORDER]:
        if s in w:
            out.append(f"| {s} | {int(w[s])} |")
    out.append(f"| total | {int(w.sum())} |")
    return "\n".join(out)


def main():
    ft = os.path.join(R, "final_top10.json")
    top = pd.DataFrame(json.load(open(ft))["top"]) if os.path.exists(ft) else pd.read_json(os.path.join(R, "top10.json"))
    summ = json.load(open(os.path.join(R, "summary.json")))
    M = pd.read_parquet(os.path.join(R, "metrics.parquet"))
    print("## Top 10\n")
    print(top_table(top))
    ap = os.path.join(R, "audit", "audit.json")
    if os.path.exists(ap):
        rob, det = audit_tables(top, json.load(open(ap)))
        print("\n## Robustness\n")
        print(rob)
        print("\n## Trade detail (BTCUSDT, OOS)\n")
        print(det)
    print("\n## Coverage\n")
    print(coverage(M))
    print("\n## Summary\n")
    print("```json")
    print(json.dumps(summ, indent=1))
    print("```")


if __name__ == "__main__":
    main()
