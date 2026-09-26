"""hlagent: BRAIN/REFLEX trading agent scaffold for Hyperliquid (session 2026-09-26).

Layers (never blurred):
  * BRAIN  - slow, deep: research, schema derivation, code, overnight review (Claude via the Anthropic SDK; brain.py).
  * REFLEX - fast, typed: one calibrated decision per candle from a judge (Jev or a rule baseline; judge.py) on a
             deterministic state snapshot (state_engine.py), gated and sized in code (policy.py), checked by a hard
             risk layer the model cannot override (risk.py), executed by a paper or (disarmed by default) live
             executor (execution.py), all orchestrated by loop.py and reviewed nightly by review.py.
"""
__version__ = "0.1.0"
