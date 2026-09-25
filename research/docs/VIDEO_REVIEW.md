# VIDEO_REVIEW.md — what could be retrieved of the two videos (2026-09-25)

Method: YouTube's watch pages, media, caption endpoints and every transcript mirror are egress-blocked here, but the
InnerTube `next` endpoint on `youtubei.googleapis.com` answered. It yields title, channel, date, view count, full
description with link targets, chapters and top comments. The `player` and `get_transcript` endpoints refused this
address ("Sign in to confirm you're not a bot" / precondition failed), so **no spoken content was obtained**; chapter
titles, descriptions, comments and the creators' own repositories are the evidence. Raw comments: `docs/video_review/`.

## Video 1 — https://www.youtube.com/watch?v=aI34O-ZA0VY
**"How to Actually BUILD a CLAUDE TRADING BOT (10 Minutes)"** — channel *Torin* (Torin Hofmann, 78.3K subscribers),
premiered 2026-04-30, 73,809 views. Description: "Build a fully automated trading bot in 10 minutes using Claude Code —
no coding background required. Free testnet, real exchange (Hyperliquid), real positions, zero financial risk. Next
week: I race 4 LLMs (Claude, ChatGPT, Grok, Hermes) through this exact framework." The rest is promotion (Discord
community "TorOS", Axiom, NordVPN affiliate links) and a disclaimer. No chapters, no repository.
Comments (20 top): viewers ask for "the prompts with the rules for the trading bot" (5 likes), praise the testnet demo
over "another backtest screenshot", and note "it's easy to set it up, the hard part is developing a strategy that
works" / "everyone can build a bot, but they won't be profitable". One viewer requests fees, slippage and a 10-year,
1,000-trade backtest before believing anything.
Follow-up (same channel, 2026-05-14, 16.8K views): **"Can CLAUDE Make the MOST MONEY in 24 hours?"** — "I gave Claude,
ChatGPT, Grok, and Nous Hermes the same prompt, $1,000, and 24 hours"; chapters: The Prompt, Claude, ChatGPT, Grok,
Nous Hermes, Key Points, 24 Hours, Key Takeaways. A single 24-hour race is an anecdote, not evidence of edge (one
commenter: "you just dipped a toe in and threw dice"). Earlier video (2026-04-15): Claude Code + TradingView MCP as a
"rule-checker for MY strategy … not an AI trading bot".
Reading: video 1 shows the *mechanics* (Claude Code writes a Hyperliquid testnet bot from a rules prompt). It presents
no strategy evidence; its safety message is "testnet first".

## Video 2 — https://www.youtube.com/watch?v=ZN0gkZw-2ks
**"Agentic AI Trading For Beginners: A New Money Making Era Is Here"** — channel *All About AI* (Kristian Fagerlie,
226K subscribers), 2026-06-04, 15,888 views. Chapters: 00:00 Intro · 05:48 Hyperliquid Setup · 11:18 Data Collection
For The AI Agent · 16:00 Finding a Trading Strategy · 19:20 AI Agentic Trading Setup · 23:11 Trading (profit) ·
27:11 Conclusion. Links: Hyperliquid referral, Polymarket referral, **github.com/AllAboutAI-YT/agentic-ai-trading-for-beginners**,
allabtai.com/agentic-ai-trading (companion guide; site blocked here, content known from search snippets).
The repository (commit 2026-06-04 "Redact account output in beginner guide") contains one file, `beginner.md`
(copied to `docs/video_review/allaboutai_beginner_guide.md`): an AI-assistant-facing wiring guide — API wallet key +
main account address in `.env`, `USE_TESTNET=true` by default, `connect.py`, `market.py` (mark/szDecimals/maxLeverage
from `metaAndAssetCtxs`), `balance.py` (unified USDC via `spotClearinghouseState`), `place_order.py` (isolated 3x, $10
margin, IOC limit at mark ±0.3%), `positions.py`, `close_position.py` (reduce-only IOC), a mainnet gate
(`USE_TESTNET=false` + `CONFIRM_MAINNET=true` + explicit user approval), a "minimal safe workflow" and common mistakes.
It says: "This is not a full trading bot. It is the basic wiring."
Companion guide (search snippets): the heartbeat is "the recurring loop where the agent wakes up, reads position and
market state, decides whether to hold, hedge, scale, exit, or switch strategy, then sleeps"; "short runs are dominated
by variance … treat every early result as a systems test, not proof of edge"; start small, hard loss limits, log every
trade. Comments: "watching the system rethink its own strategy was way more interesting than the final P&L"; several
sceptics ("test at least 24h in paper mode before real money").
Follow-ups: 2026-06-19 "My First Winning Agentic AI Trading Strategy On Polymarket" (chapters: avoiding fees and
slippage, understanding market making, fair value and AI modeling, data collection and results, Monte Carlo and
overfitting) — a fair-value-vs-orderbook approach on Polymarket's 5-minute BTC up/down markets, i.e. **not a
Hyperliquid directional strategy**; 2026-06-22 "Agentic AI Trading On Hyperliquid For Beginners (Codex 5.5)" — a
two-tier Codex loop (sub-agent packages positions into a JSON digest; main agent judges P&L against a goal every
30 s); 2026-05-20 "Building a Hyperliquid AI Agent Trader From Scratch".
Reading: video 2 teaches an architecture (wiring → data → strategy search → agent loop → live test) and is explicit
that early P&L is variance. The one strategy the creator later calls "winning" is on Polymarket.

## Consequences for the system in `research/agent/`
* Architecture already matches video 2 (digest → decide → verify → risk → venue → journal). Added from the videos'
  safe workflow: testnet switch, mainnet confirmation gate, `preflight` and `flatten` commands (`agent/cli.py`).
* Neither video supplies a strategy with evidence. The deterministic core was tested (AGENT_SYSTEM.md §4): no edge
  demonstrated. The LLM decider remains the untested claim; it can only be judged prospectively in paper/testnet mode.
