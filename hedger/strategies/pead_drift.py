"""Post-earnings-announcement drift (PEAD) — event-driven, context-augmented.

Long names that beat earnings (positive standardised unexpected earnings,
``SUE``), short names that missed, hold for ``drift_window_bars`` after the
announcement with a linear time-decay on the score.

Bernard & Thomas 1989 reported ~6% drift over 60 days; later work shows the
effect has decayed to ~4% annualised. See §2.7 / §3.6.

Without ``context['earnings']`` this strategy yields no signals — that data is
not yet plumbed into the hedger context, so the strategy is a graceful no-op
until a fundamentals feed lands.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable, Mapping

from hedger.base import Bar, Signal, Symbol
from hedger.strategies import register


def _bars_since(window: list[Bar], event_ts: datetime) -> int | None:
    """Number of bars whose close is at or after ``event_ts``. ``None`` if pre-event."""
    count = 0
    for b in reversed(window):
        if b.ts < event_ts:
            return count if count > 0 else None
        count += 1
    return count if count > 0 else None


@register("pead_drift")
def pead_drift(
    bars: Mapping[Symbol, Iterable[Bar]],
    *,
    context: Mapping[str, Any] | None = None,
    drift_window_bars: int = 60,
    sue_threshold: float = 1.0,
) -> Iterable[Signal]:
    """Trade post-earnings drift while ``context['earnings']`` carries events.

    ``context['earnings']`` should be a ``{Symbol: list[{ts, sue}]}`` mapping;
    ``sue`` is standardised unexpected earnings (z-score of the surprise).

    >>> from hedger.base import AssetClass, Bar, Symbol
    >>> from datetime import datetime, timedelta, timezone
    >>> sym = Symbol('ER', AssetClass.EQUITY)
    >>> t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    >>> hist = [Bar(symbol=sym, ts=t0+timedelta(days=i),
    ...     open=100, high=100, low=100, close=100, volume=1) for i in range(40)]
    >>> ctx = {'earnings': {sym: [{'ts': t0+timedelta(days=20), 'sue': 3.0}]}}
    >>> sig = list(pead_drift({sym: hist}, context=ctx))[0]
    >>> sig.score > 0
    True
    >>> # No earnings -> no signal
    >>> list(pead_drift({sym: hist}))
    []
    """
    earnings = (context or {}).get("earnings", {})
    if not earnings:
        return
    for symbol, bar_iter in bars.items():
        events = earnings.get(symbol) or earnings.get(str(symbol))
        if not events:
            continue
        last_event = events[-1]
        sue = float(last_event.get("sue", 0.0))
        if abs(sue) < sue_threshold:
            continue
        w = list(bar_iter)
        if not w:
            continue
        bars_since = _bars_since(w, last_event["ts"])
        if bars_since is None or bars_since > drift_window_bars:
            continue
        decay = 1.0 - bars_since / drift_window_bars
        score = math.tanh(sue / 3.0) * decay
        yield Signal(
            symbol=symbol,
            ts=w[-1].ts,
            score=float(score),
            strategy="pead_drift",
            meta={"sue": sue, "bars_since": bars_since,
                  "event_ts": last_event["ts"],
                  "drift_window_bars": drift_window_bars},
        )
