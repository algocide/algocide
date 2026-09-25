"""Fee, spread, slippage and funding assumptions with provenance.

Verified from real API responses committed by third parties (see DATA_AUDIT.md):
  * xyz equities: growthMode='enabled', deployerFeeScale='1.0' (meta fixtures, Aug-Sep 2026; Tohshi snapshot 2026-09-25).
  * docs.trade.xyz fees page (search snippet, Jan 2026 update): under growth mode "a new user trading $1,000 of any XYZ
    asset will pay ~9 cents as a taker and less than ~3 cents as a maker" -> taker 0.009%, maker 0.003% all-in.
  * Standard (non-growth) HIP-3 fees: 2x validator perps -> 0.09% taker / 0.03% maker (docs, search snippet).
  * Native perps base tier: taker 0.045%, maker 0.015% (docs, search snippet; matches prior session's cost model).
Spreads: xyz impact-price half-spreads from the Freedom fixture snapshot (metaAndAssetCtxs xyz, captured 2026-09-02
per that repo's docs) and BTC/ETH top-of-book from hyperdata's L2 snapshot (2026-07-17). These are SNAPSHOTS, not
histories; the adverse scenario doubles them.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    name: str
    taker_fee: float          # fraction of notional per side
    half_spread_bps: float    # per side
    slippage_bps: float       # per side
    adverse_stop_bps: float   # extra adverse move on stop fills (gap-through), per stop exit
    funding: bool = True

    def side_cost(self, notional: float) -> float:
        return notional * (self.taker_fee + (self.half_spread_bps + self.slippage_bps) / 1e4)


XYZ_GROWTH_TAKER = 0.00009
XYZ_STANDARD_TAKER = 0.0009
NATIVE_TAKER = 0.00045

# Half-spreads (bps) per market from the snapshots named above. Missing symbols default to the max observed.
IMPACT_HALF_SPREAD_BPS = {  # from Freedom fixture impactPxs: (ask-bid)/mid/2 * 1e4
    "xyz:NVDA": 0.67, "xyz:TSLA": 0.65, "xyz:AAPL": 1.14, "xyz:META": 0.83, "xyz:GOOGL": 0.68, "xyz:MU": 0.35,
    "xyz:SNDK": 0.33, "xyz:HOOD": 1.12, "xyz:CRCL": 1.48, "xyz:INTC": 0.96, "xyz:MSFT": 1.0, "xyz:AMZN": 1.0,
    "xyz:AMD": 1.0, "xyz:SOXL": 2.39, "xyz:ORCL": 1.0, "xyz:AVGO": 1.0, "xyz:MSTR": 2.28, "xyz:DELL": 5.0,
    "BTC": 0.08, "ETH": 0.27,
}


def base_model(symbol: str, regime: str = "base") -> CostModel:
    hs = IMPACT_HALF_SPREAD_BPS.get(symbol, 2.5)
    fee = NATIVE_TAKER if ":" not in symbol else XYZ_GROWTH_TAKER
    if regime == "base":
        return CostModel("base", fee, hs, 1.0, 2.0)
    if regime == "adverse":
        return CostModel("adverse", fee, 2 * hs, 2.0, 5.0)
    if regime == "standard_fee":   # xyz without growth mode
        return CostModel("standard_fee", XYZ_STANDARD_TAKER if ":" in symbol else fee, hs, 1.0, 2.0)
    if regime == "zero":
        return CostModel("zero", 0.0, 0.0, 0.0, 0.0, funding=False)
    raise ValueError(regime)
