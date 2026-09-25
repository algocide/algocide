"""Configuration for the analyst (defaults + optional JSON overrides + env for secrets). Paths are relative to research/."""
from __future__ import annotations
import json, os

R = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # research/
ROOT = os.path.normpath(os.path.join(R, ".."))                                          # repo root

DEFAULT = {
    "dex": "xyz",
    "paths": {"warehouse": "data/warehouse", "results": "results/phase4",
              "rwa_meta": "../data/derived/rwa_meta.json", "stock_daily": "../data/derived/stock_daily.parquet",
              "provenance": "../data/derived/provenance.json"},
    "universe": {"min_rows": 200},
    "signals": {"vol_window": 20, "ret_days": 5, "hist_window": 250, "min_history": 60, "anomaly_threshold": 2.0,
                "quiet_threshold": 1.0, "first_trigger_gap": 5, "horizons": [5, 20, 60], "label_horizon": 20,
                "label_pct": 0.05, "momentum_days": 60, "insider_window_days": 90, "catalyst_window_days": 3,
                "institutional_min_change": 0.10},
    "priority": {"high_anomaly": 3.0, "medium_anomaly": 2.0},
    "funnel": {"max_rules": 20, "max_classifier": 8, "max_deep_read": 5, "max_reasoning": 3},
    "llm": {"provider": "stub", "api_key_env": "ANTHROPIC_API_KEY", "base_url_env": "ANTHROPIC_BASE_URL",
            "deep_read_model": "claude-sonnet-5", "reasoning_model": "claude-opus-5-5",
            "max_tokens_deep_read": 4000, "max_tokens_reasoning": 6000, "temperature": 0.0, "timeout_s": 180,
            "prices_per_million": {"kimi-k3": {"input": 3.0, "output": 15.0},                       # as stated in the article
                                   "gpt-6-astra": {"input": 10.0, "output": 50.0, "cached_input": 1.0},  # as stated in the article
                                   "claude-sonnet-5": None, "claude-opus-5-5": None},                # fill from your provider's price page
            "funnel_tokens": {"deep_read": {"input": 300_000, "output": 8_000}, "reasoning": {"input": 500_000, "output": 20_000}}},
    "edgar": {"user_agent_env": "SEC_USER_AGENT", "data_url": "https://data.sec.gov", "www_url": "https://www.sec.gov",
              "max_requests_per_sec": 8, "filers": [], "form4_lookback_days": 365, "text_lookback_quarters": 8,
              "max_text_chars": 150_000, "forms_index": ["8-K", "10-Q", "10-K", "6-K", "20-F", "4", "13F-HR"]},
    "hyperliquid": {"base_url": "https://api.hyperliquid.xyz", "account_address_env": "HL_ACCOUNT_ADDRESS", "candle_days": 400},
    "portfolio": {"hhi_flag": 0.25, "avg_corr_flag": 0.6, "single_contribution_share_flag": 0.7, "corr_window": 60},
    "report": {"language_check": True, "top_events": 12},
}


def load_config(path: str | None = None) -> dict:
    cfg = json.loads(json.dumps(DEFAULT))
    if path and os.path.exists(path):
        for k, v in json.load(open(path)).items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict): cfg[k].update(v)
            else: cfg[k] = v
    return cfg


def resolve(cfg: dict, key: str) -> str:
    p = cfg["paths"][key]
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(R, p))
