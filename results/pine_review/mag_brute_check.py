#!/usr/bin/env python3
"""Magnifier skip logic (_next_hot/_advance_trails) vs walking every minute (mag_brute=True) on real scripts that use
trailing stops, brackets and stop entries. Any difference in fills or equity would be a skip-logic bug.
Usage: PYTHONPATH=src python3 results/pine_review/mag_brute_check.py"""
import sys, time
import pandas as pd
sys.path.insert(0, "src")
from pinebt.engine import CompiledScript, Runner

END = 1790640000000
idx = pd.read_parquet("data/pine/index.parquet").set_index("file")
CASES = [("Bollinger-Bands-Breakout-short-term-trend-following-Strategy.md", 86_400_000),
         ("Pivot-Based-Volume-Weighted-Breakout-Reversal-Strategy.md", 86_400_000),
         ("MACD-Moving-Average-Crossover-Trend-Following-Strategy-with-Trailing-Stop-Loss.md", 86_400_000)]
# a synthetic but dense case: trailing stop + stop entries + brackets on 4h bars
SYN = ("//@version=5\nstrategy('t', pyramiding=2)\nf = ta.ema(close, 8)\ns = ta.ema(close, 21)\n"
       "if ta.crossover(f, s)\n    strategy.entry('L', strategy.long, stop=high + 20)\n"
       "if ta.crossunder(f, s)\n    strategy.entry('S', strategy.short, stop=low - 20)\n"
       "strategy.exit('XL', 'L', loss=4000, trail_points=1500, trail_offset=50)\n"
       "strategy.exit('XS', 'S', profit=6000, loss=3000, trail_points=1000, trail_offset=30)\n")
for f, tf in CASES + [("<synthetic trail/stop-entry script>", 4 * 3_600_000)]:
    if f.startswith("<"):
        src = SYN
    else:
        if f not in idx.index:
            print("missing", f); continue
        src = idx.loc[f, "source"]
    cs = CompiledScript(src)
    out = []
    for brute in (False, True):
        t0 = time.time()
        r = Runner(cs, "BTCUSDT", tf, end_ms=END, max_bars=200000 if tf >= 86_400_000 else 6000, fee=0.0007,
                   magnify=True, mag_brute=brute, time_limit=1800).run()
        out.append((r, time.time() - t0))
    (a, ta), (b, tb) = out
    print(f"{f[:70]}: trades {len(a['closed'])}/{len(b['closed'])}, identical fills {a['closed'] == b['closed']}, "
          f"identical equity {a['equity'] == b['equity']}, minutes walked {a['minutes_walked']} vs {b['minutes_walked']}, "
          f"{ta:.1f}s vs {tb:.1f}s")
