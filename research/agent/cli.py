#!/usr/bin/env python3
"""Operational commands mirroring the videos' "minimal safe workflow": preflight (connect, metadata, unified balance,
positions), flatten (close every position, reduce-only), and the network gate: testnet by default; mainnet requires
USE_TESTNET=false AND CONFIRM_MAINNET=true AND --acknowledge-risk. Keys come only from the environment.
Usage:
  PYTHONPATH=src python3 agent/cli.py preflight [--coin BTC]           # read-only; needs api reachability
  PYTHONPATH=src python3 agent/cli.py flatten --acknowledge-risk         # sends reduce-only closes (key-gated)
  PYTHONPATH=src python3 agent/cli.py gate                               # prints which network the current env selects
"""
from __future__ import annotations
import argparse, os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__)); R = os.path.join(HERE, ".."); sys.path.insert(0, os.path.join(R, "src")); sys.path.insert(0, R)
TESTNET_URL = "https://api.hyperliquid-testnet.xyz"; MAINNET_URL = "https://api.hyperliquid.xyz"


def resolve_network(env: dict | None = None, acknowledge_risk: bool = False) -> tuple[str, str]:
    """Returns (base_url, label). Mainnet only when USE_TESTNET=false, CONFIRM_MAINNET=true and the caller acknowledged
    risk; every other combination resolves to testnet (fail-safe)."""
    env = os.environ if env is None else env
    use_testnet = str(env.get("USE_TESTNET", "true")).lower() != "false"
    confirm = str(env.get("CONFIRM_MAINNET", "false")).lower() == "true"
    if not use_testnet and confirm and acknowledge_risk: return MAINNET_URL, "mainnet"
    return TESTNET_URL, "testnet"


def short(addr: str) -> str: return addr[:6] + "..." + addr[-4:] if addr and len(addr) > 12 else "(unset)"


def preflight(base_url: str, coin: str):
    from hlr.hl_api import HLInfo
    api = HLInfo(base_url, cache_dir=None); addr = os.environ.get("HL_ACCOUNT_ADDRESS")
    out = {"base_url": base_url, "account": short(addr or "")}
    meta, ctxs = api.meta_and_asset_ctxs(None)
    for a, c in zip(meta["universe"], ctxs):
        if a["name"] == coin: out["coin"] = {"coin": coin, "mark": float(c["markPx"]), "sz_decimals": a["szDecimals"], "max_leverage": a["maxLeverage"]}
    if addr:
        st = api.post({"type": "spotClearinghouseState", "user": addr}, weight=2)
        out["unified_balances"] = [{"coin": b["coin"], "total": float(b["total"]), "hold": float(b.get("hold") or 0)} for b in st.get("balances", []) if float(b["total"]) or float(b.get("hold") or 0)]
        ch = api.post({"type": "clearinghouseState", "user": addr}, weight=2)
        out["margin_summary"] = ch.get("marginSummary"); out["open_positions"] = [{"coin": p["position"]["coin"], "szi": p["position"]["szi"], "entryPx": p["position"].get("entryPx"), "unrealizedPnl": p["position"].get("unrealizedPnl")} for p in ch.get("assetPositions", []) if float(p["position"].get("szi", 0))]
    else: out["note"] = "HL_ACCOUNT_ADDRESS not set: account checks skipped"
    return out


def flatten(base_url: str, acknowledge_risk: bool):
    key = os.environ.get("HL_AGENT_PRIVATE_KEY"); addr = os.environ.get("HL_ACCOUNT_ADDRESS")
    if not (key and addr): raise SystemExit("need HL_AGENT_PRIVATE_KEY and HL_ACCOUNT_ADDRESS")
    if not acknowledge_risk: raise SystemExit("pass --acknowledge-risk to send reduce-only closes")
    from eth_account import Account
    from hyperliquid.exchange import Exchange
    from hyperliquid.info import Info
    wallet = Account.from_key(key); info = Info(base_url, skip_ws=True); ex = Exchange(wallet, base_url, account_address=addr)
    st = info.user_state(addr); closed = []
    for p in st.get("assetPositions", []):
        pos = p["position"]
        if float(pos.get("szi", 0)): closed.append({pos["coin"]: str(ex.market_close(pos["coin"]))[:200]})
    after = [p["position"]["coin"] for p in info.user_state(addr).get("assetPositions", []) if float(p["position"].get("szi", 0))]
    return {"closed": closed, "still_open": after}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["preflight", "flatten", "gate"]); ap.add_argument("--coin", default="BTC"); ap.add_argument("--acknowledge-risk", action="store_true"); a = ap.parse_args()
    base_url, label = resolve_network(acknowledge_risk=a.acknowledge_risk)
    if a.cmd == "gate": print(json.dumps({"network": label, "base_url": base_url, "USE_TESTNET": os.environ.get("USE_TESTNET", "(unset -> true)"), "CONFIRM_MAINNET": os.environ.get("CONFIRM_MAINNET", "(unset -> false)")}, indent=1)); return
    if a.cmd == "preflight": print(json.dumps(preflight(base_url, a.coin), indent=1, default=str)); return
    if a.cmd == "flatten": print(json.dumps(flatten(base_url, a.acknowledge_risk), indent=1)); return


if __name__ == "__main__":
    main()
