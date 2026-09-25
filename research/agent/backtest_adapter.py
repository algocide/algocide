"""Run the RuleConsensusDecider inside the tested research engine (hlr2.backtest.run) as a Strategy, so its evidence is
produced under the same protocol as every other trial (development / validation / sealed holdout, cost regimes,
$100 account rules). The digest is computed bar by bar from completed bars only (identical code path to the loop)."""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."); sys.path.insert(0, os.path.join(R, "src")); sys.path.insert(0, R)
from hlr2.strategies import Base, Signal
from hlr2.data import tf_view
from hlr2.indicators import ema, atr
from agent.digest import rsi, macd
from agent.decide import RuleConsensusDecider


class RuleConsensusStrategy(Base):
    """Vectorised, causal reimplementation of RuleConsensusDecider.decide() for the engine (same score, same exits)."""
    name = "agent_rule_consensus"
    params = {"score_threshold": 2, "rsi_lo": 30, "rsi_hi": 70, "sl_atr": 2.0, "tp_atr": 4.0, "max_hold_bars": 32, "tf_trend": "1h", "require_trend_tf": True}

    def prep_symbol(self, panel, s, st):
        p = self.params; d = panel.inst[s]; c = d.c.values.astype(float); h = d.h.values.astype(float); l = d.l.values.astype(float)
        v = {"c": c, "e20": ema(c, 20), "e50": ema(c, 50), "atr": atr(h, l, c, 14, sampled=st["sampled"]), "rsi": rsi(c, 14)}
        _, _, v["hist"] = macd(c)
        if p["tf_trend"] and p["tf_trend"] != ("15m" if panel.bar_minutes == 15 else "1h"):
            t = tf_view(panel, s, p["tf_trend"], st["align"]); te20, te50 = ema(t["c"], 20), ema(t["c"], 50)
            tdir = np.sign(te20 - te50); tdir[~np.isfinite(te50)] = 0
            v["trend"] = np.array([tdir[m] if m >= 0 else 0 for m in t["map"]])
        else:
            v["trend"] = np.sign(v["e20"] - v["e50"]); v["trend"][~np.isfinite(v["e50"])] = 0
        return v

    def _score(self, v, i):
        p = self.params
        if p["require_trend_tf"] and v["trend"][i] == 0: return 0
        parts = [int(v["trend"][i]), 1 if v["e20"][i] > v["e50"][i] else -1, 1 if v["hist"][i] > 0 else -1]
        r = v["rsi"][i]
        if np.isfinite(r):
            if 40 <= r <= p["rsi_hi"]: parts.append(1)
            elif p["rsi_lo"] <= r <= 60: parts.append(-1)
        return sum(parts)

    def signal(self, s, k, st):
        v = st[s]; p = self.params
        if k < 60 or not np.isfinite(v["e50"][k]) or not np.isfinite(v["atr"][k]) or not np.isfinite(v["rsi"][k]): return None
        sc = self._score(v, k); px = v["c"][k]; a = v["atr"][k]; r = v["rsi"][k]
        if sc >= p["score_threshold"] and r < p["rsi_hi"] and px > v["e20"][k]: return Signal(1, px - p["sl_atr"] * a, px + p["tp_atr"] * a, self.describe(), {"i": k})
        if sc <= -p["score_threshold"] and r > p["rsi_lo"] and px < v["e20"][k]: return Signal(-1, px + p["sl_atr"] * a, px - p["tp_atr"] * a, self.describe(), {"i": k})
        return None

    def exit(self, s, k, pos, st):
        v = st[s]; p = self.params
        if k - pos["state"]["i"] >= p["max_hold_bars"]: return "time"
        sc = self._score(v, k)
        if (pos["dir"] == 1 and sc <= -p["score_threshold"]) or (pos["dir"] == -1 and sc >= p["score_threshold"]): return "consensus_flip"
        return None
