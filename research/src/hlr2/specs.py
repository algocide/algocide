"""Contract specifications used for sizing/rounding. Values come from real API responses committed by third parties:
meta (dex='xyz') fixture in github.com/buggatidealership/Freedom (captured 2026-09-02 per its docs/data-sources.md) and the
2026-09-25 07:30 UTC snapshot in github.com/Tohshi-memo/HyperLiquid-Bot-test (asset_universe_latest.json).
Tick rule (docs 'Tick and lot size', via search snippet + Chainstack mirror): price has <= 5 significant figures and
<= (6 - szDecimals) decimals for perps; size rounded to szDecimals; minimum order value $10.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Spec:
    symbol: str
    sz_decimals: int
    max_leverage: int
    only_isolated: bool = False
    growth_mode: bool = True
    min_order_usd: float = 10.0
    collateral: str = "USDC"


SPECS = {
    "xyz:META": Spec("xyz:META", 3, 20), "xyz:NVDA": Spec("xyz:NVDA", 3, 20), "xyz:TSLA": Spec("xyz:TSLA", 3, 20),
    "xyz:AAPL": Spec("xyz:AAPL", 3, 20), "xyz:GOOGL": Spec("xyz:GOOGL", 3, 20), "xyz:MSFT": Spec("xyz:MSFT", 3, 20),
    "xyz:AMZN": Spec("xyz:AMZN", 3, 20), "xyz:MU": Spec("xyz:MU", 3, 10), "xyz:SNDK": Spec("xyz:SNDK", 3, 10),
    "xyz:CRCL": Spec("xyz:CRCL", 3, 10, True), "xyz:INTC": Spec("xyz:INTC", 2, 10, True), "xyz:AMD": Spec("xyz:AMD", 3, 10, True),
    "xyz:HOOD": Spec("xyz:HOOD", 3, 10, True), "xyz:ORCL": Spec("xyz:ORCL", 3, 10, True), "xyz:AVGO": Spec("xyz:AVGO", 2, 10, True),
    "xyz:MSTR": Spec("xyz:MSTR", 3, 10, True, growth_mode=False), "xyz:SOXL": Spec("xyz:SOXL", 2, 10, True), "xyz:NBIS": Spec("xyz:NBIS", 2, 10, True),
    "BTC": Spec("BTC", 5, 40), "ETH": Spec("ETH", 4, 25),
}


def round_size(qty: float, sz_decimals: int) -> float:
    f = 10 ** sz_decimals
    return math.floor(qty * f + 1e-9) / f


def round_price(px: float, sz_decimals: int) -> float:
    if px <= 0 or not math.isfinite(px): return px
    max_dec = 6 - sz_decimals
    sig = 5 - int(math.floor(math.log10(px))) - 1          # decimals allowed by the 5-significant-figure rule
    dec = max(0, min(max_dec, sig))
    return round(px, dec)
