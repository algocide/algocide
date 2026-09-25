"""Daily engine tests: no future information, next-open fills, stop-first sequencing and gap fills, funding sign, account sizing/caps."""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from hlr2.daily import run_daily, DailyCosts, STRATEGIES, features


def synth(n=400, seed=1, drift=0.0, start="2024-01-01"):
    rng = np.random.default_rng(seed); r = rng.normal(drift, 0.02, n); c = 100 * np.exp(np.cumsum(r)); o = c * np.exp(rng.normal(0, 0.004, n))
    w = np.abs(rng.normal(0, 0.006, n)) * c; h = np.maximum(o, c) + w; l = np.minimum(o, c) - w
    return pd.DataFrame({"date": pd.bdate_range(start, periods=n), "o": o, "h": h, "l": l, "c": c, "v": 1.0})


def test_no_future_info_and_next_open():
    p1 = {"A": synth(seed=1, drift=0.001), "B": synth(seed=2, drift=0.001)}; p2 = {k: v.copy() for k, v in p1.items()}
    K = 300
    for s in p2: p2[s].loc[K + 1:, ["o", "h", "l", "c"]] *= 1.3          # perturb the future
    for name in ["MR-RSI2(th=10)", "MR-BB(k=2.0)", "BO(20)", "MOM-RS(60,top5)"]:
        t1 = run_daily(p1, name, mode="portfolio", costs=DailyCosts(0, 0, 0, 0, 0), end=p1["A"].date.iloc[K])
        t2 = run_daily(p2, name, mode="portfolio", costs=DailyCosts(0, 0, 0, 0, 0), end=p2["A"].date.iloc[K])
        assert len(t1) == len(t2) and (len(t1) == 0 or np.allclose(t1.net_pnl.values, t2.net_pnl.values)), name
        if len(t1):
            f = features(p1[t1.sym.iat[0]]); i = t1.entry_i.iat[0]; assert abs(t1.entry_px.iat[0] - f["o"][i]) < 1e-9   # entry at the open of the fill day
    print("test_no_future_info_and_next_open ok")


def test_stop_first_and_gap():
    n = 260; i_sig = 230; c = 100 * 1.003 ** np.arange(n)                    # steady uptrend: price far above SMA200
    c[228] = c[227] * 0.97; c[229] = c[228] * 0.97; c[230] = c[229] * 0.97   # three down closes -> RSI(2) = 0
    c[231:] = c[230]
    o = c.copy(); h = c * 1.002; l = c * 0.998
    d = pd.DataFrame({"date": pd.bdate_range("2024-01-01", periods=n), "o": o, "h": h, "l": l, "c": c, "v": 1.0}); f = features(d)
    assert f["rsi2"][i_sig] < 10 and f["c"][i_sig] > f["sma200"][i_sig], "setup failed"
    stop = f["c"][i_sig] - 2 * f["atr"][i_sig]
    d.loc[i_sig + 1, ["o", "h", "l", "c"]] = [stop * 0.9, stop * 0.92, stop * 0.85, stop * 0.9]        # entry day gaps through the stop
    t = run_daily({"A": d}, "MR-RSI2(th=10)", mode="portfolio", costs=DailyCosts(0, 0, 0, 0, 0), start=d.date.iloc[200], end=d.date.iloc[240])
    tr = t[t.entry_i == i_sig + 1]; assert len(tr) == 1, t[["entry_i", "reason", "exit_px"]]
    assert tr.reason.iat[0] == "stop" and abs(tr.exit_px.iat[0] - stop * 0.9) < 1e-9, tr[["reason", "exit_px"]]
    # stop touched intraday (not at the open) fills at the stop price
    d.loc[i_sig + 1, ["o", "h", "l", "c"]] = [c[i_sig], c[i_sig] * 1.01, stop * 0.99, c[i_sig]]
    t = run_daily({"A": d}, "MR-RSI2(th=10)", mode="portfolio", costs=DailyCosts(0, 0, 0, 0, 0), start=d.date.iloc[200], end=d.date.iloc[240])
    tr = t[t.entry_i == i_sig + 1]; assert len(tr) == 1 and tr.reason.iat[0] == "stop" and abs(tr.exit_px.iat[0] - stop) < 1e-9, tr[["reason", "exit_px"]]
    print("test_stop_first_and_gap ok")


def test_funding_and_account_sizing():
    c = DailyCosts(funding_bps_per_day=10.0); t0, t1 = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-11")
    assert abs(c.funding("X", 1, 1000.0, t0, t1) - (-10.0 / 1e4 * 10 * 1000.0)) < 1e-9 and c.funding("X", -1, 1000.0, t0, t1) > 0   # longs pay, shorts receive
    p = {"A": synth(seed=5, drift=0.002), "B": synth(seed=6, drift=0.002)}
    t = run_daily(p, "BO(20)", mode="account", costs=DailyCosts(), specs={"A": (3, 10.0), "B": (3, 10.0)})
    if len(t):
        assert (t.notional <= 200 + 1e-6).all(), "gross leverage cap"; assert (t.qty.round(3) == t.qty).all(), "rounding"
        for i in range(1, len(t)): assert pd.Timestamp(t.entry_ts.iat[i]) >= pd.Timestamp(t.exit_ts.iat[i - 1]), "single position"
    print("test_funding_and_account_sizing ok", len(t))


if __name__ == "__main__":
    test_no_future_info_and_next_open(); test_stop_first_and_gap(); test_funding_and_account_sizing(); print("ALL DAILY TESTS PASSED")
