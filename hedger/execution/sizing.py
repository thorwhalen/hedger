"""Sizers: turn a stream of Signals into Decisions (target weights).

Pluggable so that the reflection loop can A/B different sizers without
touching strategies.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from hedger.base import Decision, Position, Signal, Symbol


def equal_weight_sizer(
    signals: Iterable[Signal],
    *,
    positions: Mapping[Symbol, Position],
    nav: float,
    max_weight: float = 0.10,
) -> Iterable[Decision]:
    """Allocate equal weight to all symbols with non-zero score, capped.

    Negative scores produce short positions if your venue allows shorts.
    """
    sigs = list(signals)
    actives = [s for s in sigs if s.score != 0]
    if not actives:
        return
    w = min(max_weight, 1.0 / len(actives))
    for s in actives:
        yield Decision(
            symbol=s.symbol,
            ts=s.ts,
            target_weight=w if s.score > 0 else -w,
            rationale=f"equal_weight ({s.strategy})",
            meta={"raw_score": s.score},
        )


def kelly_capped_sizer(
    signals: Iterable[Signal],
    *,
    positions: Mapping[Symbol, Position],
    nav: float,
    fraction: float = 0.25,  # fractional Kelly — full Kelly is famously brutal
    max_weight: float = 0.10,
) -> Iterable[Decision]:
    """Treat |score| as edge; allocate fractional-Kelly weight, capped.

    This is intentionally rough — for a real Kelly you need vol estimates.
    Use it as a baseline that the reflection loop can later replace with
    a proper covariance-aware optimizer.
    """
    for s in signals:
        edge = abs(s.score)
        w = min(max_weight, fraction * edge)
        if s.score < 0:
            w = -w
        yield Decision(
            symbol=s.symbol,
            ts=s.ts,
            target_weight=w,
            rationale=f"kelly({fraction})*|score|",
            meta={"raw_score": s.score, "edge": edge},
        )


__all__ = ["equal_weight_sizer", "kelly_capped_sizer"]
