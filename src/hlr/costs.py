"""Cost model. All rates are fractions of notional per side unless stated.

Sources (see docs/venue_verification.md for citations):
  * Hyperliquid base tier (14d weighted volume <= $5M): taker 0.045%, maker 0.015%.
  * HIP-3 xyz (trade.xyz) standard: deployer fee share set so all-in fees are 2x validator-operated perps
    (docs.trade.xyz/perp-mechanics/fees, via search snippet 2026-09-24) -> taker 0.09%, maker 0.03%.
  * Growth mode (if enabled on an asset): all-in fees reduced by >= 90% -> taker ~0.009%, maker ~0.003%.
    Whether a given xyz asset is in growth mode could NOT be verified from this environment; both regimes are reported.
  * Other HIP-3 dexes (hyna, para, mkts): fee share not verified; assume the same 2x as xyz (sensitivity: 1x).
  * Spot (for perp-vs-spot carry): assumed same base schedule as perps (0.045%/0.015%); flagged as assumption.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class FeeRegime:
    name: str
    taker: float
    maker: float
    note: str = ""


FEE_REGIMES = {
    "native_base": FeeRegime("native_base", 0.00045, 0.00015, "Hyperliquid validator-operated perps, base tier"),
    "hip3_standard": FeeRegime("hip3_standard", 0.00090, 0.00030, "HIP-3 (xyz) standard = 2x native base"),
    "hip3_growth": FeeRegime("hip3_growth", 0.00009, 0.00003, "HIP-3 growth mode (>=90% reduction) -- unverified per asset"),
    "zero": FeeRegime("zero", 0.0, 0.0, "diagnostic only"),
}

RISK_FREE = 0.0403  # ^IRX 13-week T-bill yield recorded by upstream RWA meta on 2026-09-23 (opportunity cost of hedge capital)


def round_trip_cost_bps(regime: FeeRegime, half_spread_bps: float, impact_bps: float = 0.5, maker: bool = False) -> float:
    """Round-trip cost in bps of notional: 2 sides x (fee + half-spread crossed + impact). Maker: no spread crossing."""
    fee = (regime.maker if maker else regime.taker) * 1e4
    cross = 0.0 if maker else half_spread_bps
    return 2.0 * (fee + cross + impact_bps)
