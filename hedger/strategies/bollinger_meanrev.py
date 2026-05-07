"""Bollinger / z-score mean reversion — univariate contrarian.

Fade closes that have wandered more than ``n_std`` standard deviations from
their rolling mean. Score is the z-score (clipped via ``tanh``) with the sign
flipped to act contrarian.

Catastrophic in trends (the "falling knife" problem); pair with a regime
filter in production. See §2.2 / §3.2 of the strategies report.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from hedger.base import Bar, Signal, Symbol
from hedger.strategies import register


@register("bollinger_meanrev")
def bollinger_meanrev(
    bars: Mapping[Symbol, Iterable[Bar]],
    *,
    context: Mapping[str, Any] | None = None,
    window: int = 20,
    n_std: float = 2.0,
) -> Iterable[Signal]:
    """Fade ±``n_std`` deviations from the rolling mean over ``window`` bars.

    >>> from hedger.base import AssetClass, Bar, Symbol
    >>> from datetime import datetime, timedelta, timezone
    >>> sym = Symbol('OS', AssetClass.EQUITY)
    >>> t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    >>> # 25 flat bars at 100 then a spike to 110 — should fire short.
    >>> hist = [Bar(symbol=sym, ts=t0+timedelta(days=i),
    ...     open=100, high=100.5, low=99.5, close=100+(0.1 if i%2 else -0.1),
    ...     volume=1) for i in range(25)]
    >>> hist.append(Bar(symbol=sym, ts=t0+timedelta(days=25),
    ...     open=100, high=110, low=100, close=110.0, volume=1))
    >>> sigs = list(bollinger_meanrev({sym: hist}))
    >>> sigs[0].score < 0
    True
    """
    for symbol, bar_iter in bars.items():
        w = list(bar_iter)
        if len(w) < window + 1:
            continue
        closes = [b.close for b in w[-window:]]
        mean = sum(closes) / window
        var = sum((c - mean) ** 2 for c in closes) / (window - 1)
        std = math.sqrt(var)
        if std == 0:
            continue
        z = (w[-1].close - mean) / std
        if abs(z) < n_std:
            continue
        score = -math.tanh(z / n_std)
        yield Signal(
            symbol=symbol,
            ts=w[-1].ts,
            score=float(score),
            strategy="bollinger_meanrev",
            meta={
                "z": float(z),
                "mean": float(mean),
                "std": float(std),
                "window": window,
                "n_std": n_std,
            },
        )
