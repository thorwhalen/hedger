"""News-embedding strategy — consumes pre-computed features from the mall.

This is a thin plug-in: it reads scores from
``context["mall"]["features:news_embed_v1"]`` (a
``Mapping[(symbol_str, session_date), float]``) and emits one Signal per
symbol whose latest bar matches a stored session.

Following the cost-discipline pattern in the ``data-pipeline`` skill,
**embedding and inference happen elsewhere** (in :mod:`newsmood`) and are
written into the mall by a separate daily job. The strategy itself is
cheap, deterministic, and side-effect-free.

If no feature store is wired up, the strategy emits no signals — safe to
register and leave inert.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping

from hedger.base import Bar, Signal, Symbol
from hedger.strategies import register


FEATURE_KEY = "features:news_embed_v1"


def _session_date(bar: Bar) -> date:
    """Calendar date of a bar's timestamp (in its own tz, or UTC if naive)."""
    ts = bar.ts
    if ts.tzinfo is None:
        return ts.date()
    return ts.date()


def _score_to_unit_range(score: float, *, scale: float = 1.0) -> float:
    """Squash an arbitrary score to ``[-1, 1]`` via ``tanh(score / scale)``.

    Models predict returns (small numbers near zero), so we need to expand
    them onto the signal scale before passing to position sizing.
    """
    import math

    if not math.isfinite(score):
        return 0.0
    return math.tanh(score / max(scale, 1e-9))


@register("news_embed")
def news_embed(
    bars: Mapping[Symbol, Iterable[Bar]],
    *,
    context: Mapping[str, Any] | None = None,
    feature_key: str = FEATURE_KEY,
    score_scale: float = 0.01,
) -> Iterable[Signal]:
    """Long if forecast > 0, short if < 0, sized by ``tanh(score/scale)``.

    Parameters
    ----------
    bars
        ``{Symbol: Iterable[Bar]}`` as supplied by the runner.
    context
        Runner context. Must contain ``mall`` for this strategy to do anything.
    feature_key
        Key in the mall where the per-(symbol, session) score lives.
    score_scale
        Scale used to squash raw return predictions into ``[-1, 1]`` via
        ``tanh``. Default ``0.01`` ≈ 1% return → score ≈ 0.76.
    """
    context = context or {}
    mall = context.get("mall") if isinstance(context, Mapping) else None
    if mall is None:
        return
    try:
        features = mall[feature_key]
    except KeyError:
        return

    for symbol, bar_iter in bars.items():
        bar_list = list(bar_iter)
        if not bar_list:
            continue
        last = bar_list[-1]
        sess = _session_date(last)
        # Look up via (symbol_str, session); fall back to ticker-only key.
        key_strs = (
            (str(symbol), sess),
            (symbol.ticker, sess),
            f"{symbol.ticker}:{sess.isoformat()}",
        )
        raw_score: float | None = None
        for k in key_strs:
            try:
                raw_score = float(features[k])
            except (KeyError, TypeError, ValueError):
                continue
            else:
                break
        if raw_score is None:
            continue
        signal_score = _score_to_unit_range(raw_score, scale=score_scale)
        yield Signal(
            symbol=symbol,
            ts=last.ts,
            score=signal_score,
            strategy="news_embed",
            meta={
                "raw_score": raw_score,
                "feature_key": feature_key,
                "session": sess.isoformat(),
                "score_scale": score_scale,
            },
        )
