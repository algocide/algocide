"""BRAIN: the slow, deep layer. Never in the hot path; every call is rate-limited and fails closed.

Two jobs:
  * escalate()        - when the reflex judge is unsure (confidence < 0.60) or calls `crisis`, re-read the state and
                        the recent decision history and return hold / flatten / resume. It can only choose among those
                        three; it cannot touch a limit, a size, or a threshold.
  * nightly_review()  - read the overnight review report and propose changes to the TUNABLE policy keys only. The
                        proposal is written to disk and applied only through review.apply_proposal(..., approve=True).

Model: the prompt that started this work names Claude Opus 5.5, so the default is `claude-opus-5-5`; override with
HLAGENT_BRAIN_MODEL (e.g. `claude-opus-5`) if that model is not enabled on the account. Thinking is always on for
Opus 5.5, so depth is controlled with output_config.effort. Responses are parsed into typed pydantic models through
client.messages.parse(); a `refusal` stop reason is treated as "no verdict".
"""
from __future__ import annotations
import json
import os
import time
from typing import Callable, Literal, Optional
from pydantic import BaseModel, Field
from .policy import KELLY_HARD_CAP, PolicyConfig
from .schema import Decision, StateVector
from .setups import SetupSpec

DEFAULT_MODEL = os.environ.get("HLAGENT_BRAIN_MODEL", "claude-opus-5-5")


class BrainVerdict(BaseModel):
    action: Literal["hold", "flatten", "resume"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class ReviewProposal(BaseModel):
    summary: str
    proposed_updates: dict[str, float] = Field(default_factory=dict)   # PolicyConfig.TUNABLE keys only
    do_not_arm_live: bool = True
    reasons: list[str] = Field(default_factory=list)


SYSTEM_ESCALATION = (
    "You are the slow, deliberate layer of a two-layer trading agent on Hyperliquid perpetuals. The fast layer judged "
    "the current state with low confidence or called it a crisis, so new entries are paused. Read the state, the "
    "decision and the recent history and answer with exactly one action: 'hold' (keep the pause, keep any position), "
    "'flatten' (close the position now), or 'resume' (lift the pause). You cannot change any limit, size or threshold. "
    "Costs matter: a taker round trip is roughly 9 bps plus spread. When in doubt, hold. Give a short rationale."
)

SYSTEM_REVIEW = (
    "You are reviewing one day of a paper-trading agent. You may propose numeric changes ONLY to these policy keys: "
    f"{', '.join(PolicyConfig.TUNABLE)}. kelly_fraction may never exceed {KELLY_HARD_CAP}. You may not propose changes "
    "to risk limits. Base every proposal on the numbers in the report; if the sample is too small to conclude anything, "
    "propose nothing and say so. Set do_not_arm_live=true unless the report shows positive Brier skill on at least 200 "
    "resolved non-synthetic decisions and positive net P&L after fees."
)


class Brain:
    def __init__(self, model: str = DEFAULT_MODEL, client=None, effort_escalation: str = "high",
                 effort_review: str = "xhigh", max_calls_per_hour: int = 6, log: Optional[Callable] = None):
        self.model = model
        self._client = client
        self.effort_escalation = effort_escalation
        self.effort_review = effort_review
        self.max_calls_per_hour = max_calls_per_hour
        self.log = log or (lambda rec: None)
        self._calls: list[float] = []

    @property
    def client(self):
        if self._client is None:
            import anthropic   # credentials resolve from ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login`
            self._client = anthropic.Anthropic()
        return self._client

    def _rate_ok(self) -> bool:
        now = time.time()
        self._calls = [t for t in self._calls if now - t < 3600]
        if len(self._calls) >= self.max_calls_per_hour:
            return False
        self._calls.append(now)
        return True

    def _parse(self, system: str, user: str, output_format, effort: str, max_tokens: int):
        """One structured call; returns the parsed object or None (refusal / error). Errors are caught most-specific
        first so a 4xx is never retried as if it were transient."""
        try:
            import anthropic
            try:
                resp = self.client.messages.parse(model=self.model, max_tokens=max_tokens, system=system,
                                                  messages=[{"role": "user", "content": user}],
                                                  output_format=output_format, output_config={"effort": effort})
            except anthropic.BadRequestError as e:
                self.log({"kind": "brain_error", "error": f"bad request: {e.message}"}); return None
            except anthropic.AuthenticationError:
                self.log({"kind": "brain_error", "error": "authentication"}); return None
            except anthropic.RateLimitError:
                self.log({"kind": "brain_error", "error": "rate limited"}); return None
            except anthropic.APIStatusError as e:
                self.log({"kind": "brain_error", "error": f"status {e.status_code}"}); return None
            except anthropic.APIConnectionError:
                self.log({"kind": "brain_error", "error": "connection"}); return None
        except Exception as e:  # SDK missing, credentials missing, parse failure: fail closed
            self.log({"kind": "brain_error", "error": repr(e)[:300]}); return None
        if getattr(resp, "stop_reason", None) == "refusal":
            self.log({"kind": "brain_error", "error": "refusal", "category": getattr(getattr(resp, "stop_details", None), "category", None)})
            return None
        self.log({"kind": "brain_call", "model": self.model, "effort": effort, "usage": getattr(resp, "usage", None) and resp.usage.model_dump()})
        return resp.parsed_output

    def escalate(self, state: StateVector, decision: Decision, recent: list[dict], setup: Optional[SetupSpec]) -> Optional[BrainVerdict]:
        if not self._rate_ok():
            self.log({"kind": "brain_error", "error": "escalation rate limit; holding"})
            return None
        payload = {
            "state": state.to_prompt(),
            "decision": decision.model_dump(mode="json"),
            "setup": {"id": setup.id, "name": setup.name, "thesis": setup.thesis, "bear_case": setup.bear_case,
                      "bias": setup.bias.value} if setup else None,
            "recent_decisions": recent[-20:],
        }
        return self._parse(SYSTEM_ESCALATION, json.dumps(payload, default=str), BrainVerdict, self.effort_escalation, 4000)

    def nightly_review(self, report_md: str, policy: PolicyConfig) -> Optional[ReviewProposal]:
        if not self._rate_ok():
            return None
        user = f"Current policy: {json.dumps(policy.__dict__)}\n\nReport:\n{report_md}"
        prop = self._parse(SYSTEM_REVIEW, user, ReviewProposal, self.effort_review, 16000)
        if prop is None:
            return None
        clean = {k: float(v) for k, v in prop.proposed_updates.items() if k in PolicyConfig.TUNABLE}
        if "kelly_fraction" in clean:
            clean["kelly_fraction"] = min(clean["kelly_fraction"], KELLY_HARD_CAP)
        return prop.model_copy(update={"proposed_updates": clean})
