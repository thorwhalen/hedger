"""LLM-driven news/sentiment strategy.

Architecture:
  recent bars + recent news (injected via `context['news']`) -> Claude
    -> structured JSON: {symbol, score in [-1,1], rationale}
    -> Signal

Cost discipline:
  * One call per *batch of symbols*, not per symbol — pack the universe.
  * Cache by (date, symbol_set, news_hash) so reruns are free.
  * Prefer Haiku for routine scoring; Opus only when reflection escalates.

The actual API call is wrapped in a thin function so tests can monkey-patch
it. The strategy itself is pure given a `score_fn`.
"""

from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from typing import Any, Iterable, Mapping

from hedger.base import Bar, Signal, Symbol, utc_now
from hedger.strategies import register


def _default_score_fn(prompt: str, *, model: str = "claude-haiku-4-5-20251001") -> str:
    """Call Claude via the Anthropic SDK. Return raw text content."""
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
        system=(
            "You are a quantitative analyst scoring stocks for short-term "
            "directional moves based on supplied news and price context. "
            "Reply ONLY with strict JSON: a list of "
            "{symbol, score, rationale}. score in [-1, 1]."
        ),
    )
    return "".join(b.text for b in msg.content if hasattr(b, "text"))


@lru_cache(maxsize=4096)
def _cached_score(prompt_hash: str, prompt: str, model: str) -> str:
    return _default_score_fn(prompt, model=model)


@register("llm_news")
def llm_news(
    bars: Mapping[Symbol, Iterable[Bar]],
    *,
    context: Mapping[str, Any] | None = None,
    model: str = "claude-haiku-4-5-20251001",
    score_fn=None,
) -> Iterable[Signal]:
    """Score each symbol using recent bars + news headlines from context.

    `context['news']` should be a {symbol_str: list[str]} mapping. If empty
    for a symbol, we skip it (no opinion is the right opinion).
    """
    score_fn = score_fn or (
        lambda p, m=model: _cached_score(hashlib.sha256(p.encode()).hexdigest(), p, m)
    )
    news = (context or {}).get("news", {})
    items = []
    for symbol, bar_iter in bars.items():
        bar_list = list(bar_iter)[-30:]
        headlines = list(news.get(str(symbol), []))[:8]
        if not headlines:
            continue
        items.append(
            {
                "symbol": str(symbol),
                "recent_closes": [b.close for b in bar_list],
                "headlines": headlines,
            }
        )
    if not items:
        return
    prompt = (
        "For each symbol below, output a directional score in [-1, 1] for "
        "the next bar, with a one-sentence rationale.\n\n" + json.dumps(items, indent=2)
    )
    text = score_fn(prompt)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Be lenient: extract the first json array we can find.
        start, end = text.find("["), text.rfind("]")
        parsed = json.loads(text[start : end + 1]) if start >= 0 < end else []

    by_symbol = {str(s): s for s in bars.keys()}
    for entry in parsed:
        sym = by_symbol.get(entry.get("symbol"))
        if sym is None:
            continue
        score = float(entry.get("score", 0.0))
        score = max(-1.0, min(1.0, score))
        yield Signal(
            symbol=sym,
            ts=utc_now(),
            score=score,
            strategy="llm_news",
            meta={"rationale": entry.get("rationale", ""), "model": model},
        )
