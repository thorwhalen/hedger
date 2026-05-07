"""Pairs / cointegration trading — multivariate, relative-value.

For each ``(sym_a, sym_b, beta)`` triple supplied via ``context['pairs']``,
form the spread ``a - beta * b``, z-score it over ``lookback`` bars, and trade
the cheap leg long / rich leg short when ``|z| >= entry_z``.

The cointegration test that produces ``beta`` is **not** in this strategy —
the runner screens pairs offline (Engle-Granger, Johansen) and injects the
selected pairs through context, keeping the strategy pure. See §3.4.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from hedger.base import Bar, Signal, Symbol
from hedger.strategies import register


def _stats(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    return mean, math.sqrt(var)


@register("pairs_zscore")
def pairs_zscore(
    bars: Mapping[Symbol, Iterable[Bar]],
    *,
    context: Mapping[str, Any] | None = None,
    lookback: int = 120,
    entry_z: float = 2.0,
) -> Iterable[Signal]:
    """Trade z-score of the cointegrated spread for each pair in context.

    ``context['pairs']`` is an iterable of ``(Symbol, Symbol, float)`` triples;
    the float is the hedge ratio ``beta`` from offline screening.

    >>> from hedger.base import AssetClass, Bar, Symbol
    >>> from datetime import datetime, timedelta, timezone
    >>> a, b = Symbol('A', AssetClass.EQUITY), Symbol('B', AssetClass.EQUITY)
    >>> t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    >>> def mk(sym, closes):
    ...     return [Bar(symbol=sym, ts=t0+timedelta(days=i),
    ...         open=c, high=c, low=c, close=c, volume=1) for i, c in enumerate(closes)]
    >>> base = [100 + (i % 5) * 0.1 for i in range(150)]
    >>> bars = {a: mk(a, base + [105.0]), b: mk(b, base + [100.0])}
    >>> ctx = {'pairs': [(a, b, 1.0)]}
    >>> sigs = list(pairs_zscore(bars, context=ctx, lookback=140, entry_z=1.5))
    >>> [(str(s.symbol), s.score < 0) for s in sigs] == [('default:A', True), ('default:B', False)]
    True
    """
    pairs = (context or {}).get("pairs", [])
    for triple in pairs:
        sym_a, sym_b, beta = triple
        wa = list(bars.get(sym_a, []))
        wb = list(bars.get(sym_b, []))
        if len(wa) < lookback or len(wb) < lookback:
            continue
        spread = [wa[-lookback + i].close - beta * wb[-lookback + i].close for i in range(lookback)]
        mean, std = _stats(spread)
        if std == 0:
            continue
        z = (spread[-1] - mean) / std
        if abs(z) < entry_z:
            continue
        score_a = -math.tanh(z / entry_z)
        score_b = -score_a
        ts = wa[-1].ts
        yield Signal(
            symbol=sym_a,
            ts=ts,
            score=float(score_a),
            strategy="pairs_zscore",
            meta={
                "z": float(z),
                "pair": (sym_a.ticker, sym_b.ticker),
                "leg": "A",
                "beta": float(beta),
                "lookback": lookback,
            },
        )
        yield Signal(
            symbol=sym_b,
            ts=ts,
            score=float(score_b),
            strategy="pairs_zscore",
            meta={
                "z": float(z),
                "pair": (sym_a.ticker, sym_b.ticker),
                "leg": "B",
                "beta": float(beta),
                "lookback": lookback,
            },
        )
