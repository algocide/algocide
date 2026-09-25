"""Decision layer. Every decider returns a list of Decision objects in the SAME schema the videos' agents use
(asset, action buy/sell/hold/close, allocation_usd, tp_price, sl_price, rationale, confidence).

RuleConsensusDecider (default): a deterministic version of the 'indicator consensus' the reference agents feed to the
LLM. It is the version that can be backtested through the tested engine, so it is the version whose evidence we report.
LLMDecider (optional, off by default): Anthropic Messages API with a strict JSON schema, 'skeptic' instructions and the
hard limits in the prompt. It is NOT evaluated in this research (no key, paid calls disallowed); wiring only.
VerifierGate: deterministic skeptic that checks geometry (stop side, reward:risk) before the risk gate."""
from __future__ import annotations
import json, os
from dataclasses import dataclass, asdict, field


@dataclass
class Decision:
    asset: str
    action: str                 # buy | sell | hold | close
    allocation_usd: float = 0.0  # notional wish; the risk gate decides the real size
    tp_price: float | None = None
    sl_price: float | None = None
    rationale: str = ""
    confidence: float = 0.5
    decider: str = ""
    def to_dict(self): return asdict(self)


class RuleConsensusDecider:
    """Score = trend_tf direction (±1) + sign(ema20-ema50) on the decision tf (±1) + MACD histogram sign (±1)
    + RSI regime (+1 if 40<=RSI<=rsi_hi for longs / −1 if rsi_lo<=RSI<=60 for shorts, else 0).
    buy when score >= threshold and RSI < rsi_hi and price above ema20; sell (short) mirror. Exits: opposite score <= −threshold,
    max_hold_bars, or the stop/target (sl_atr / tp_atr multiples of ATR). Hold otherwise."""
    name = "rule_consensus"
    def __init__(self, score_threshold=2, rsi_lo=30, rsi_hi=70, sl_atr=2.0, tp_atr=4.0, max_hold_bars=32, tf_trend="1h", require_trend_tf=True, **kw):
        self.p = dict(score_threshold=score_threshold, rsi_lo=rsi_lo, rsi_hi=rsi_hi, sl_atr=sl_atr, tp_atr=tp_atr, max_hold_bars=max_hold_bars, tf_trend=tf_trend, require_trend_tf=require_trend_tf)

    def score(self, d: dict) -> tuple[int, dict]:
        p = self.p; parts = {}
        # higher-timeframe trend when available, else the decision-timeframe EMA20/EMA50 sign (same fallback as the backtest adapter)
        parts["trend_tf"] = d["trend_tf"]["direction"] if d.get("trend_tf") else (1 if d["ema20_vs_ema50_pct"] > 0 else -1)
        parts["ema"] = 1 if d["ema20_vs_ema50_pct"] > 0 else -1
        parts["macd"] = 1 if d["macd_hist"] > 0 else -1
        r = d.get("rsi14"); parts["rsi"] = 0
        if r is not None:
            if 40 <= r <= p["rsi_hi"]: parts["rsi"] = 1
            elif p["rsi_lo"] <= r <= 60: parts["rsi"] = -1
        return sum(parts.values()), parts

    def decide(self, digests: dict[str, dict], account: dict) -> list[Decision]:
        out = []; p = self.p; pos = account.get("position")
        for sym, d in digests.items():
            if not d.get("ok"): out.append(Decision(sym, "hold", rationale=d.get("reason", "no data"), decider=self.name)); continue
            s, parts = self.score(d); px = d["price"]; a = d["atr"]; r = d.get("rsi14") or 50.0
            if pos and pos["sym"] == sym:
                if (pos["side"] == "long" and s <= -p["score_threshold"]) or (pos["side"] == "short" and s >= p["score_threshold"]):
                    out.append(Decision(sym, "close", rationale=f"consensus flipped: score={s} {parts}", confidence=0.6, decider=self.name)); continue
                if pos.get("bars_held", 0) >= p["max_hold_bars"]:
                    out.append(Decision(sym, "close", rationale=f"max hold {p['max_hold_bars']} bars", confidence=0.5, decider=self.name)); continue
                out.append(Decision(sym, "hold", rationale=f"in position, score={s}", decider=self.name)); continue
            if pos: out.append(Decision(sym, "hold", rationale="another position open", decider=self.name)); continue
            if s >= p["score_threshold"] and r < p["rsi_hi"] and px > d["ema20"]:
                out.append(Decision(sym, "buy", allocation_usd=0.0, tp_price=px + p["tp_atr"] * a, sl_price=px - p["sl_atr"] * a, rationale=f"long consensus score={s} {parts}", confidence=min(0.9, 0.5 + 0.1 * s), decider=self.name))
            elif s <= -p["score_threshold"] and r > p["rsi_lo"] and px < d["ema20"]:
                out.append(Decision(sym, "sell", allocation_usd=0.0, tp_price=px - p["tp_atr"] * a, sl_price=px + p["sl_atr"] * a, rationale=f"short consensus score={s} {parts}", confidence=min(0.9, 0.5 - 0.1 * s), decider=self.name))
            else:
                out.append(Decision(sym, "hold", rationale=f"no consensus: score={s} {parts}", decider=self.name))
        return out


class LLMDecider:
    """Anthropic Messages API decider (optional). Requires the key named by cfg['llm']['api_key_env']. Never called in
    the research runs. The prompt states the hard limits and the JSON schema; outputs are validated by VerifierGate."""
    name = "llm"
    SYSTEM = ("You are a sceptical quantitative trader operating a Hyperliquid perpetuals paper account under hard risk limits you cannot change. "
              "You receive a JSON digest of completed-bar indicators per asset and the account state. Decide, per asset, one of buy/sell/hold/close. "
              "Rules: never trade without a stop; stop must be on the correct side of the price and within 5% of it; take-profit must give reward:risk >= min_reward_risk; "
              "prefer hold when signals conflict; do not flip solely on funding or RSI extremes; respect the session flags. "
              "Answer ONLY with a JSON object {\"decisions\": [{asset, action, allocation_usd, tp_price, sl_price, rationale, confidence}]}.")
    def __init__(self, cfg: dict):
        self.cfg = cfg["llm"]; self.limits = cfg["account"]
    def decide(self, digests, account):
        import requests
        key = os.environ.get(self.cfg["api_key_env"])
        if not key: raise RuntimeError(f"LLM decider needs {self.cfg['api_key_env']}")
        body = {"model": self.cfg["model"], "max_tokens": self.cfg["max_tokens"], "temperature": self.cfg["temperature"], "system": self.SYSTEM,
                "messages": [{"role": "user", "content": json.dumps({"digests": digests, "account": account, "hard_limits": self.limits}, default=str)}]}
        r = requests.post(os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages", headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json=body, timeout=60)
        r.raise_for_status(); text = "".join(b.get("text", "") for b in r.json().get("content", []))
        start, end = text.find("{"), text.rfind("}"); data = json.loads(text[start: end + 1])
        out = []
        for d in data.get("decisions", []):
            out.append(Decision(str(d.get("asset")), str(d.get("action", "hold")).lower(), float(d.get("allocation_usd") or 0), d.get("tp_price"), d.get("sl_price"), str(d.get("rationale", ""))[:500], float(d.get("confidence") or 0.5), self.name))
        return out


class VerifierGate:
    """Deterministic skeptic: rejects risk-adding decisions with bad geometry. Risk-reducing actions always pass."""
    def __init__(self, min_reward_risk: float = 1.5, max_stop_pct: float = 5.0):
        self.min_rr = min_reward_risk; self.max_stop_pct = max_stop_pct
    def check(self, dec: Decision, price: float) -> tuple[bool, str]:
        if dec.action in ("hold", "close"): return True, "ok"
        if dec.action not in ("buy", "sell"): return False, f"unknown action {dec.action}"
        if dec.sl_price is None or not (dec.sl_price > 0): return False, "no stop"
        dr = 1 if dec.action == "buy" else -1
        if dr * (price - dec.sl_price) <= 0: return False, "stop on wrong side"
        if abs(price - dec.sl_price) / price * 100 > self.max_stop_pct: return False, "stop too far"
        if dec.tp_price is not None:
            if dr * (dec.tp_price - price) <= 0: return False, "target on wrong side"
            rr = abs(dec.tp_price - price) / abs(price - dec.sl_price)
            if rr < self.min_rr: return False, f"reward:risk {rr:.2f} < {self.min_rr}"
        return True, "ok"
