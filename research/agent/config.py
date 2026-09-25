"""Configuration (JSON file + env). The LLM/decider sees the limits but cannot change them."""
from __future__ import annotations
import json, os
from dataclasses import dataclass, field, asdict

DEFAULT = {
    "universe": ["BTC", "ETH"],
    "interval": "1h",                  # decision cadence = bar interval (decisions on completed bars only)
    "decider": "rule_consensus",       # rule_consensus | llm
    "decider_params": {"tf_trend": "1h", "score_threshold": 3, "rsi_lo": 30, "rsi_hi": 70, "sl_atr": 2.0, "tp_atr": 4.0, "max_hold_bars": 32},   # most consistent backtest config (dev +6.6 / val +4.2 on BTC/ETH 24/7; PF ~1.1; UNPROVEN)
    "session": "24x7",                 # 24x7 | us_regular (stock perps must use us_regular)
    "account": {"start_equity": 100.0, "risk_per_trade_usd": 1.0, "max_gross_leverage": 2.0, "max_positions": 1,
                 "drawdown_pause_usd": 10.0, "daily_loss_halt_usd": 3.0, "drawdown_kill_usd": 20.0,
                 "max_trades_per_day": 6, "cooldown_bars": 1, "min_reward_risk": 1.5, "max_position_age_bars": 96},
    "costs": {"regime": "base"},
    "paths": {"state": "results/agent/state.json", "journal": "results/agent/journal.jsonl", "kill_file": "results/agent/KILL"},
    "llm": {"provider": "anthropic", "model": "claude-sonnet-5", "max_tokens": 800, "temperature": 0.0, "api_key_env": "ANTHROPIC_API_KEY"},
    "live": {"enabled": False, "private_key_env": "HL_AGENT_PRIVATE_KEY", "account_address_env": "HL_ACCOUNT_ADDRESS", "base_url": "https://api.hyperliquid.xyz"},
}


def load_config(path: str | None = None) -> dict:
    cfg = json.loads(json.dumps(DEFAULT))
    if path and os.path.exists(path):
        user = json.load(open(path))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict): cfg[k].update(v)
            else: cfg[k] = v
    return cfg
