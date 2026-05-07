"""Donchian channel breakout — univariate trend / time-series momentum.

Classic CTA-style breakout: long when close pierces the rolling-`fast` high,
short when it pierces the rolling-`fast` low. Score is the breakout magnitude
in ATR units squashed by ``tanh`` so it stays in ``[-1, 1]``.

Realistic single-name single-server Sharpe before fees: 0.3–0.7 — see
``misc/docs/Trading Strategies for the hedger Framework.md`` §2.1 / §3.1.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from hedger.base import Bar, Signal, Symbol
from hedger.strategies import register


def _atr(bars: list[Bar]) -> float:
    """Average true range over the supplied window. Zero on degenerate input."""
    if len(bars) < 2:
        return 0.0
    trs: list[float] = []
    prev_close = bars[0].close
    for b in bars[1:]:
        tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        trs.append(tr)
        prev_close = b.close
    return sum(trs) / len(trs) if trs else 0.0


@register("donchian_breakout")
def donchian_breakout(
    bars: Mapping[Symbol, Iterable[Bar]],
    *,
    context: Mapping[str, Any] | None = None,
    fast: int = 20,
    slow: int = 55,
    atr_window: int = 14,
) -> Iterable[Signal]:
    """Long on upper-channel break, short on lower-channel break.

    >>> from hedger.base import AssetClass, Bar, Symbol
    >>> from datetime import datetime, timedelta, timezone
    >>> sym = Symbol('UP', AssetClass.EQUITY)
    >>> t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    >>> # 59 flat bars at 100, then a clear breakout to 110.
    >>> hist = [Bar(symbol=sym, ts=t0+timedelta(days=i),
    ...     open=100, high=100.5, low=99.5, close=100, volume=1) for i in range(59)]
    >>> hist.append(Bar(symbol=sym, ts=t0+timedelta(days=59),
    ...     open=100, high=110, low=100, close=110, volume=1))
    >>> sigs = list(donchian_breakout({sym: hist}))
    >>> sigs[0].score > 0
    True
    """
    for symbol, bar_iter in bars.items():
        w = list(bar_iter)
        if len(w) < slow + 1 or len(w) < atr_window + 1:
            continue
        upper = max(b.high for b in w[-fast:-1])
        lower = min(b.low for b in w[-fast:-1])
        atr = _atr(w[-(atr_window + 1) :])
        if atr <= 0:
            continue
        c = w[-1].close
        if c > upper:
            score = math.tanh((c - upper) / atr)
        elif c < lower:
            score = -math.tanh((lower - c) / atr)
        else:
            continue
        yield Signal(
            symbol=symbol,
            ts=w[-1].ts,
            score=float(score),
            strategy="donchian_breakout",
            meta={
                "upper": float(upper),
                "lower": float(lower),
                "atr": float(atr),
                "fast": fast,
                "slow": slow,
                "atr_window": atr_window,
            },
        )
