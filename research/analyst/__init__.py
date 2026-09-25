"""Phase 4: the AI market analyst from docs/PHASE4_ARTICLE_DIGEST.md, built for this repo's constraints.

Layers: warehouse (typed tables with provenance) -> signals (plain code) -> funnel (deterministic rules, composite
rank) -> llm stages (deep read, bull/bear/arbiter; stub offline, Claude live with the user's key) -> report
(what changed, source, what contradicts it; no scores, no buy/sell language) -> human.

Read-only everywhere: Hyperliquid `info` endpoints, SEC EDGAR public files. No order code exists in this package."""
