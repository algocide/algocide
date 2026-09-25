# Beginner Guide: AI-Assisted First Trade on Hyperliquid

This guide is written for an AI assistant helping a user get ready to place a
first Hyperliquid trade quickly. The user brings their own `.env`; do not ask
them to paste private keys into chat.

The goal is to move from `.env` to a verified dry run, then a tiny live or
testnet trade only when the user explicitly approves execution.

This is not a full trading bot. It is the basic wiring.

## AI Assistant Fast Path

If this repo already has the `hyperliquid_trader` framework, use it first. It is
faster and safer than asking the user to create files manually.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

.venv/bin/python -m hyperliquid_trader.cli config
.venv/bin/python -m hyperliquid_trader.cli preflight
.venv/bin/python balance.py
.venv/bin/python -m hyperliquid_trader.cli open --coin BTC --side long --margin-usd 10 --leverage 3
```

Only execute after the user explicitly approves:

```bash
.venv/bin/python -m hyperliquid_trader.cli open --coin BTC --side long --margin-usd 10 --leverage 3 --execute
.venv/bin/python -m hyperliquid_trader.cli positions
.venv/bin/python -m hyperliquid_trader.cli close --coin BTC --execute
.venv/bin/python -m hyperliquid_trader.cli positions
```

For mainnet execution, require all three:

- `USE_TESTNET=false`
- `CONFIRM_MAINNET=true`
- the user explicitly says to execute the trade

If the framework is not present, use the standalone files below to create the
minimum wiring.

## AI Assistant Rules

- Never print or ask the user to paste `PRIVATE_KEY` or `MASTER_PRIVATE_KEY`.
- Treat `ACCOUNT_ADDRESS` as the funded trading account to query.
- Treat `PRIVATE_KEY` as the signing key, often an API wallet key.
- If `ACCOUNT_ADDRESS` is missing, stop and ask the user to add it to `.env`.
- Do not infer the funded account from `API_WALLET_ADDRESS`.
- Redact addresses in chat and logs unless the user explicitly asks to inspect
  them.
- Always verify unified USDC with `spotClearinghouseState`.
- Always dry-run/plan before `--execute`.
- Always verify the close path with `reduce_only=True`.
- After any test trade, verify positions are flat.

## What You Need

- Python 3.10 or newer
- A Hyperliquid account with funds
- A private key for signing orders
- The public address of the account you want to trade

Prefer using a Hyperliquid API wallet instead of your main wallet private key.
An API wallet signs orders for the main account, but when reading account data
you still query the main account address, not the API wallet address.

## Install Dependencies

In a new repo:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install hyperliquid-python-sdk eth-account python-dotenv requests
```

## Create `.env`

Use this shape:

```bash
PRIVATE_KEY=0x_your_private_or_api_wallet_key_here
ACCOUNT_ADDRESS=0x_your_main_hyperliquid_account_address_here
USE_TESTNET=true
```

Set `USE_TESTNET=false` only when you are ready for real mainnet orders.

Important:

- `PRIVATE_KEY` is the key that signs orders.
- `ACCOUNT_ADDRESS` is the account whose balances/positions you want to query.
- If you use an API wallet, `PRIVATE_KEY` is the API wallet key, but
  `ACCOUNT_ADDRESS` is still the main account address.

## File 1: Connect and Read Account State

Create `connect.py`:

```python
import os
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

load_dotenv()

PRIVATE_KEY = os.environ["PRIVATE_KEY"]
ACCOUNT_ADDRESS = os.environ["ACCOUNT_ADDRESS"]
USE_TESTNET = os.environ.get("USE_TESTNET", "true").lower() == "true"

BASE_URL = constants.TESTNET_API_URL if USE_TESTNET else constants.MAINNET_API_URL

wallet = Account.from_key(PRIVATE_KEY)
info = Info(BASE_URL, skip_ws=True)
exchange = Exchange(wallet, BASE_URL, account_address=ACCOUNT_ADDRESS)

def short_address(address):
    return address[:6] + "..." + address[-4:]


print("signer address:", short_address(wallet.address))
print("trading account:", short_address(ACCOUNT_ADDRESS))
print("base url:", BASE_URL)

state = info.user_state(ACCOUNT_ADDRESS)
print({
    "withdrawable": state.get("withdrawable"),
    "open_positions": len(state.get("assetPositions", [])),
    "margin_summary": state.get("marginSummary"),
})
```

Run it:

```bash
.venv/bin/python connect.py
```

If this prints an empty or unexpected account, check that `ACCOUNT_ADDRESS` is
the actual trading account address, not the API wallet address.

## File 2: Get Market Metadata

Before placing orders, you need the current mark price and the size decimals
for the coin.

Create `market.py`:

```python
import os
import requests
from dotenv import load_dotenv
from hyperliquid.utils import constants

load_dotenv()

USE_TESTNET = os.environ.get("USE_TESTNET", "true").lower() == "true"
BASE_URL = constants.TESTNET_API_URL if USE_TESTNET else constants.MAINNET_API_URL
INFO_URL = BASE_URL + "/info"


def post(payload):
    return requests.post(INFO_URL, json=payload, timeout=10).json()


def get_meta(coin):
    meta, contexts = post({"type": "metaAndAssetCtxs"})
    for universe_item, ctx in zip(meta["universe"], contexts):
        if universe_item["name"] == coin:
            return {
                "coin": coin,
                "mark": float(ctx["markPx"]),
                "sz_decimals": int(universe_item["szDecimals"]),
                "max_leverage": int(universe_item["maxLeverage"]),
            }
    raise RuntimeError(f"coin not found: {coin}")


print(get_meta("BTC"))
```

Run it:

```bash
.venv/bin/python market.py
```

## File 3: Read Unified Account Balance

For unified accounts, the usable USDC balance can show up in spot clearinghouse
state even when `user_state` shows `accountValue: 0.0` for perps. To see that
balance, query `spotClearinghouseState` and look for `"coin": "USDC"`.

Create `balance.py`:

```python
import os
import requests
from dotenv import load_dotenv
from hyperliquid.utils import constants

load_dotenv()


def get_account_address():
    account_address = os.environ.get("ACCOUNT_ADDRESS")
    if account_address:
        return account_address, "ACCOUNT_ADDRESS"

    raise RuntimeError("Set ACCOUNT_ADDRESS in .env")


ACCOUNT_ADDRESS, ACCOUNT_ADDRESS_SOURCE = get_account_address()
USE_TESTNET = os.environ.get("USE_TESTNET", "true").lower() == "true"
BASE_URL = constants.TESTNET_API_URL if USE_TESTNET else constants.MAINNET_API_URL
INFO_URL = BASE_URL + "/info"


def post(payload):
    response = requests.post(INFO_URL, json=payload, timeout=10)
    response.raise_for_status()
    return response.json()


def short_address(address):
    return address[:6] + "..." + address[-4:]


state = post({
    "type": "spotClearinghouseState",
    "user": ACCOUNT_ADDRESS,
})

print("network:", "testnet" if USE_TESTNET else "mainnet")
print("account:", short_address(ACCOUNT_ADDRESS))
print("account_source:", ACCOUNT_ADDRESS_SOURCE)

for balance in state.get("balances", []):
    coin = balance["coin"]
    total = float(balance["total"])
    hold = float(balance.get("hold") or 0)
    available = total - hold

    if total or hold:
        print({
            "coin": coin,
            "total": total,
            "hold": hold,
            "available_estimate": available,
        })
```

Run it:

```bash
.venv/bin/python balance.py
```

For unified USDC collateral, look for output like:

```python
{
    "coin": "USDC",
    "total": 100.0,
    "hold": 0.0,
    "available_estimate": 100.0,
}
```

`hold` is the amount locked by open orders or other holds. A simple available
estimate is `total - hold`.

## File 4: Place a Small IOC Order

This example places a small isolated-margin BTC perpetual order.

It uses an IOC limit order. IOC means "immediate or cancel": whatever does not
fill immediately is canceled instead of resting on the book.

Create `place_order.py`:

```python
import math
import os
import requests
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

load_dotenv()

PRIVATE_KEY = os.environ["PRIVATE_KEY"]
ACCOUNT_ADDRESS = os.environ["ACCOUNT_ADDRESS"]
USE_TESTNET = os.environ.get("USE_TESTNET", "true").lower() == "true"

BASE_URL = constants.TESTNET_API_URL if USE_TESTNET else constants.MAINNET_API_URL
INFO_URL = BASE_URL + "/info"

wallet = Account.from_key(PRIVATE_KEY)
exchange = Exchange(wallet, BASE_URL, account_address=ACCOUNT_ADDRESS)


def post(payload):
    return requests.post(INFO_URL, json=payload, timeout=10).json()


def get_meta(coin):
    meta, contexts = post({"type": "metaAndAssetCtxs"})
    for universe_item, ctx in zip(meta["universe"], contexts):
        if universe_item["name"] == coin:
            return {
                "mark": float(ctx["markPx"]),
                "sz_decimals": int(universe_item["szDecimals"]),
                "max_leverage": int(universe_item["maxLeverage"]),
            }
    raise RuntimeError(f"coin not found: {coin}")


def round_size(size, sz_decimals):
    factor = 10 ** sz_decimals
    return math.floor(abs(size) * factor) / factor


def slippage_price(mark, is_buy, slippage_pct=0.003):
    # Buy a little above mark, sell a little below mark, to improve fill chance.
    px = mark * (1 + slippage_pct if is_buy else 1 - slippage_pct)
    if px >= 10000:
        return round(px)
    if px >= 1000:
        return round(px, 1)
    if px >= 100:
        return round(px, 2)
    if px >= 10:
        return round(px, 3)
    return round(px, 4)


coin = "BTC"
is_buy = True       # True = long/buy, False = short/sell
leverage = 3
margin_usd = 10.0  # small test size

meta = get_meta(coin)
if leverage > meta["max_leverage"]:
    raise RuntimeError(f"{coin} max leverage is {meta['max_leverage']}")

notional = margin_usd * leverage
size = round_size(notional / meta["mark"], meta["sz_decimals"])
price = slippage_price(meta["mark"], is_buy)

print("setting leverage...")
print(exchange.update_leverage(leverage, coin, is_cross=False))

print(f"placing {'BUY' if is_buy else 'SELL'} {size} {coin} @ {price}")
result = exchange.order(
    coin,
    is_buy,
    size,
    price,
    {"limit": {"tif": "Ioc"}},
)
print(result)
```

Run it:

```bash
.venv/bin/python place_order.py
```

Read the result carefully. A successful response may be `filled` or `resting`
depending on order type. With IOC, unfilled quantity should be canceled.

## File 5: Check Positions

Create `positions.py`:

```python
import os
from dotenv import load_dotenv
from hyperliquid.info import Info
from hyperliquid.utils import constants

load_dotenv()

ACCOUNT_ADDRESS = os.environ["ACCOUNT_ADDRESS"]
USE_TESTNET = os.environ.get("USE_TESTNET", "true").lower() == "true"
BASE_URL = constants.TESTNET_API_URL if USE_TESTNET else constants.MAINNET_API_URL

info = Info(BASE_URL, skip_ws=True)
state = info.user_state(ACCOUNT_ADDRESS)

for item in state.get("assetPositions", []):
    pos = item.get("position", {})
    szi = float(pos.get("szi", 0))
    if szi:
        print({
            "coin": pos["coin"],
            "szi": szi,
            "side": "long" if szi > 0 else "short",
            "entryPx": pos.get("entryPx"),
            "unrealizedPnl": pos.get("unrealizedPnl"),
            "liquidationPx": pos.get("liquidationPx"),
        })
```

Run it:

```bash
.venv/bin/python positions.py
```

## File 6: Close a Position With Reduce-Only

Use reduce-only when closing. Reduce-only prevents the order from accidentally
opening a new opposite position.

Create `close_position.py`:

```python
import math
import os
import requests
from dotenv import load_dotenv
from eth_account import Account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

load_dotenv()

PRIVATE_KEY = os.environ["PRIVATE_KEY"]
ACCOUNT_ADDRESS = os.environ["ACCOUNT_ADDRESS"]
USE_TESTNET = os.environ.get("USE_TESTNET", "true").lower() == "true"

BASE_URL = constants.TESTNET_API_URL if USE_TESTNET else constants.MAINNET_API_URL
INFO_URL = BASE_URL + "/info"

wallet = Account.from_key(PRIVATE_KEY)
info = Info(BASE_URL, skip_ws=True)
exchange = Exchange(wallet, BASE_URL, account_address=ACCOUNT_ADDRESS)


def post(payload):
    return requests.post(INFO_URL, json=payload, timeout=10).json()


def get_mark(coin):
    mids = post({"type": "allMids"})
    return float(mids[coin])


def close_price(mark, is_buy, slippage_pct=0.003):
    px = mark * (1 + slippage_pct if is_buy else 1 - slippage_pct)
    if px >= 10000:
        return round(px)
    if px >= 1000:
        return round(px, 1)
    if px >= 100:
        return round(px, 2)
    if px >= 10:
        return round(px, 3)
    return round(px, 4)


coin = "BTC"
state = info.user_state(ACCOUNT_ADDRESS)

position = None
for item in state.get("assetPositions", []):
    pos = item.get("position", {})
    if pos.get("coin") == coin and float(pos.get("szi", 0)):
        position = pos
        break

if not position:
    raise SystemExit(f"no open {coin} position")

szi = float(position["szi"])
close_size = abs(szi)
is_buy = szi < 0  # short closes with buy; long closes with sell
mark = get_mark(coin)
price = close_price(mark, is_buy)

print(f"closing {coin}: {'BUY' if is_buy else 'SELL'} {close_size} @ {price}")
result = exchange.order(
    coin,
    is_buy,
    close_size,
    price,
    {"limit": {"tif": "Ioc"}},
    reduce_only=True,
)
print(result)
```

Run it:

```bash
.venv/bin/python close_position.py
```

## Mainnet vs Testnet

The official docs use mainnet examples at:

```text
https://api.hyperliquid.xyz
```

Testnet uses:

```text
https://api.hyperliquid-testnet.xyz
```

Keep `USE_TESTNET=true` until you have verified the full open/check/close flow.

## Common Beginner Mistakes

- Querying account data with the API wallet address instead of the main account
  address.
- Looking only at `user_state` and missing unified-account USDC in
  `spotClearinghouseState`.
- Forgetting `reduce_only=True` when closing.
- Using a size with too many decimals.
- Using a price with too many significant figures.
- Sending a marketable order as `Gtc` when you meant `Ioc`.
- Reusing one API wallet across multiple high-speed trading processes and
  causing nonce collisions.
- Trading mainnet before testing the close path.

## What the Order Arguments Mean

This call:

```python
exchange.order(
    coin,
    is_buy,
    size,
    price,
    {"limit": {"tif": "Ioc"}},
    reduce_only=False,
)
```

Means:

- `coin`: market name, for example `"BTC"`
- `is_buy`: `True` buys/longs, `False` sells/shorts
- `size`: base asset size, not dollars
- `price`: limit price
- `{"limit": {"tif": "Ioc"}}`: immediate-or-cancel limit order
- `reduce_only`: when `True`, the order can only reduce an existing position

## Minimal Safe Workflow

Use this order every time:

1. Run `connect.py`.
2. Run `market.py`.
3. Run `balance.py` and confirm unified USDC is visible.
4. Place a tiny test order on testnet.
5. Run `positions.py`.
6. Close it with `close_position.py`.
7. Run `positions.py` again and confirm flat.
8. Only then consider mainnet.

## Official References

- Hyperliquid API docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
- Official Python SDK: https://github.com/hyperliquid-dex/hyperliquid-python-sdk
- Exchange endpoint / order fields: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint
- Tick and lot size: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/tick-and-lot-size
- Nonces and API wallets: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets
