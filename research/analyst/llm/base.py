from __future__ import annotations
import os, json, re, time
from dataclasses import dataclass, field, asdict


@dataclass
class Completion:
    text: str; model: str; provider: str; input_tokens: int; output_tokens: int; stub: bool = False; latency_s: float = 0.0


@dataclass
class Ledger:
    """Token and cost accounting per stage and model. Cost is None for models without a price in the config."""
    prices: dict = field(default_factory=dict); rows: list = field(default_factory=list)

    def add(self, c: Completion, stage: str, symbol: str | None = None):
        self.rows.append({"stage": stage, "symbol": symbol, "model": c.model, "provider": c.provider, "input_tokens": c.input_tokens, "output_tokens": c.output_tokens, "stub": c.stub, "latency_s": round(c.latency_s, 2)})

    def totals(self) -> dict:
        out = {}
        for r in self.rows:
            t = out.setdefault(r["model"], {"calls": 0, "input_tokens": 0, "output_tokens": 0, "stub_calls": 0})
            t["calls"] += 1; t["input_tokens"] += r["input_tokens"]; t["output_tokens"] += r["output_tokens"]; t["stub_calls"] += int(r["stub"])
        for m, t in out.items():
            p = self.prices.get(m); t["cost_usd"] = None if not p else round(t["input_tokens"] / 1e6 * p["input"] + t["output_tokens"] / 1e6 * p["output"], 4)
        return out


class Provider:
    name = "base"
    def complete(self, system: str, user: str, model: str, max_tokens: int, temperature: float = 0.0) -> Completion: raise NotImplementedError


SCHEMA_RE = re.compile(r"OUTPUT_SCHEMA:\s*(\w+)")
STUB_NOTE = "[STUB: no model was called; this field is a placeholder]"
STUB_OUTPUTS = {
    "deep_read": {"quarters": [], "language_unchanged_numbers_moved": [], "key_facts": [], "not_answerable": ["all: stub provider"]},
    "bull": {"thesis": STUB_NOTE, "claims": [], "strength": None, "assumptions": []},
    "bear": {"thesis": STUB_NOTE, "claims": [], "strength": None, "assumptions": []},
    "arbiter": {"claims_checked": [], "checklist": [], "bull_strength": None, "bear_strength": None, "top_contradiction": STUB_NOTE, "what_would_change_conclusion": []},
}


class StubProvider(Provider):
    """Returns schema-shaped JSON marked stub=true. Lets the whole pipeline run offline without pretending."""
    name = "stub"
    def complete(self, system, user, model, max_tokens, temperature=0.0) -> Completion:
        m = SCHEMA_RE.search(user); schema = m.group(1) if m else "unknown"
        out = dict(STUB_OUTPUTS.get(schema, {})); out.update({"stub": True, "note": STUB_NOTE, "schema": schema})
        text = json.dumps(out)
        return Completion(text=text, model=f"stub/{model}", provider="stub", input_tokens=len(system + user) // 4, output_tokens=len(text) // 4, stub=True)


class AnthropicProvider(Provider):
    """Messages API. Constructed only with live=True and the key present in the named env var; the key is held on the
    instance and never printed, logged or written."""
    name = "anthropic"
    def __init__(self, api_key_env: str = "ANTHROPIC_API_KEY", base_url_env: str = "ANTHROPIC_BASE_URL", live: bool = False, timeout: float = 180.0, post=None):
        if not live: raise RuntimeError("live LLM calls are disabled; pass --live-llm to enable (costs money, needs your key)")
        key = os.environ.get(api_key_env)
        if not key: raise RuntimeError(f"{api_key_env} is not set; export it in your shell, never write it into the repo")
        if post is None:
            import requests; post = requests.post
        self._key = key; self._post = post; self.base_url = os.environ.get(base_url_env, "https://api.anthropic.com").rstrip("/"); self.timeout = timeout

    def __repr__(self): return "AnthropicProvider(key=<redacted>)"

    def complete(self, system, user, model, max_tokens, temperature=0.0) -> Completion:
        body = {"model": model, "max_tokens": int(max_tokens), "temperature": float(temperature), "system": system, "messages": [{"role": "user", "content": user}]}
        t0 = time.time()
        r = self._post(self.base_url + "/v1/messages", headers={"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, json=body, timeout=self.timeout)
        if r.status_code >= 400: raise RuntimeError(f"anthropic http {r.status_code}: {r.text[:300]}")
        j = r.json(); text = "".join(b.get("text", "") for b in j.get("content", []) if b.get("type") == "text"); u = j.get("usage", {})
        return Completion(text=text, model=j.get("model", model), provider="anthropic", input_tokens=int(u.get("input_tokens", 0)), output_tokens=int(u.get("output_tokens", 0)), latency_s=time.time() - t0)


def make_provider(cfg_llm: dict, live: bool = False, post=None) -> Provider:
    name = cfg_llm.get("provider", "stub")
    if name == "stub" or not live: return StubProvider()
    if name == "anthropic": return AnthropicProvider(cfg_llm["api_key_env"], cfg_llm.get("base_url_env", "ANTHROPIC_BASE_URL"), live=live, timeout=cfg_llm.get("timeout_s", 180), post=post)
    raise ValueError(f"unknown provider {name}")


def parse_json(text: str) -> dict:
    s = text.strip(); s = re.sub(r"^```(?:json)?\s*", "", s); s = re.sub(r"\s*```$", "", s)
    try: return json.loads(s)
    except Exception: pass
    a, b = s.find("{"), s.rfind("}")
    if a >= 0 and b > a:
        try: return json.loads(s[a:b + 1])
        except Exception: pass
    return {"parse_error": True, "raw": text[:2000]}
