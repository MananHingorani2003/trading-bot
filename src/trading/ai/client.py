"""Thin Anthropic client wrapper with token + cost tracking.

If ANTHROPIC_API_KEY is not set, `available` is False — callers must check
and fall back to rules-only behavior. We never raise on a missing key.

Per-million-token prices (USD). Update when Anthropic changes pricing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from trading.settings import settings

# Model IDs — keep in sync with environment context
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"
OPUS = "claude-opus-4-7"

# USD per 1M tokens. Indicative; verify on anthropic.com before live.
PRICING = {
    HAIKU:  {"in": 1.00,  "out": 5.00},
    SONNET: {"in": 3.00,  "out": 15.00},
    OPUS:   {"in": 15.00, "out": 75.00},
}


@dataclass
class AIResponse:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float


class AIClient:
    def __init__(self) -> None:
        self._client = None
        if settings.anthropic_api_key:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            except ImportError:
                # `anthropic` is in [live] extra; absence = degrade silently
                pass

    @property
    def available(self) -> bool:
        return self._client is not None

    def message(
        self, *, model: str, system: str, user: str,
        max_tokens: int = 1024, temperature: float = 0.2,
    ) -> AIResponse:
        if self._client is None:
            raise RuntimeError("AI client not available — set ANTHROPIC_API_KEY and install [live]")
        r = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in r.content if getattr(b, "type", None) == "text")
        usage = r.usage
        ti, to = usage.input_tokens, usage.output_tokens
        price = PRICING.get(model, PRICING[SONNET])
        cost = (ti * price["in"] + to * price["out"]) / 1_000_000
        return AIResponse(text=text, model=model, tokens_in=ti, tokens_out=to, cost_usd=cost)


def parse_json(text: str) -> dict | list:
    """Extract the first JSON object/array from a model response.

    Models occasionally wrap JSON in prose or code fences; strip both.
    Prefers `{` over `[` so an object embedded in a sentence still parses
    as an object rather than getting beaten to it by a stray list.
    """
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.rsplit("```", 1)[0].strip()
    # Try object first, then array
    for opener, closer in (("{", "}"), ("[", "]")):
        i = t.find(opener)
        if i == -1:
            continue
        depth = 0
        for j in range(i, len(t)):
            if t[j] == opener:
                depth += 1
            elif t[j] == closer:
                depth -= 1
                if depth == 0:
                    return json.loads(t[i:j + 1])
    raise ValueError(f"no JSON found in: {text[:200]}")


def _find_all_objects(text: str) -> list[dict]:
    """Return every top-level JSON object found in text, in order."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
        t = t.rsplit("```", 1)[0].strip()
    objs: list[dict] = []
    i = 0
    while i < len(t):
        start = t.find("{", i)
        if start == -1:
            break
        depth = 0
        end_found = False
        for j in range(start, len(t)):
            if t[j] == "{":
                depth += 1
            elif t[j] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(t[start:j + 1])
                        if isinstance(obj, dict):
                            objs.append(obj)
                    except json.JSONDecodeError:
                        pass
                    i = j + 1
                    end_found = True
                    break
        if not end_found:
            break
    return objs


def parse_json_object(text: str, *, required_keys: list[str] | None = None) -> dict:
    """Find a JSON object in text.

    If `required_keys` is given, return the first object that has at least one
    of them — so a stray `{}` or `{example: "TCS"}` inside model prose doesn't
    fool us into thinking we got the real response.

    Falls back to `parse_json` for [list-wrapped] shapes.
    """
    objs = _find_all_objects(text)
    if objs:
        if required_keys:
            for o in objs:
                if any(k in o for k in required_keys):
                    return o
        return objs[0]

    # Maybe the response is a JSON array — pick the first dict in it
    try:
        d = parse_json(text)
    except Exception as e:                      # noqa: BLE001
        raise ValueError(f"no JSON object in: {text[:200]}") from e
    if isinstance(d, list):
        for item in d:
            if isinstance(item, dict):
                return item
    if isinstance(d, dict):
        return d
    raise ValueError(f"expected JSON object, got {type(d).__name__}")
