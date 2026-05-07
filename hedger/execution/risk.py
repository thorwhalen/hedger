"""Risk and compliance middleware.

Pattern: each middleware is a `Decision -> Decision | None`. Returning None
vetoes the decision (with logging). Compose with `compose_middleware([...])`.

Why a function pipeline rather than a Risk class? Because (a) middlewares
compose freely with `Tax` and `Compliance` ones, (b) reflection can prepend
a new check by editing the chain in config rather than touching the runner.
"""

from __future__ import annotations

from typing import Callable, Iterable, Mapping

from hedger.base import Decision, Position, Symbol
from hedger.config import RiskConfig
from hedger.util import get_logger

log = get_logger("hedger.risk")


def compose_middleware(
    middlewares: Iterable[Callable[[Decision], Decision | None]],
) -> Callable[[Decision], Decision | None]:
    """Run middlewares left-to-right; if any returns None, the chain stops.

    >>> noop = lambda d: d
    >>> compose_middleware([noop, noop])(None)  # type: ignore
    """
    middlewares = list(middlewares)

    def chain(decision: Decision) -> Decision | None:
        d = decision
        for mw in middlewares:
            d = mw(d)
            if d is None:
                return None
        return d
    return chain


def cap_position_weight(max_weight: float) -> Callable[[Decision], Decision | None]:
    """Clip target weight in absolute value."""
    def mw(d: Decision) -> Decision | None:
        if abs(d.target_weight) > max_weight:
            log.info("clip", symbol=str(d.symbol),
                     orig=d.target_weight, capped=max_weight)
            return Decision(
                symbol=d.symbol, ts=d.ts,
                target_weight=max_weight if d.target_weight > 0 else -max_weight,
                rationale=d.rationale + f" [capped to {max_weight}]",
                risk_budget=d.risk_budget, meta=d.meta,
            )
        return d
    return mw


def block_when_loss_exceeds(
    max_daily_loss: float,
    *,
    nav_today_open: Callable[[], float],
    nav_now: Callable[[], float],
) -> Callable[[Decision], Decision | None]:
    """Veto all decisions if today's drawdown > max_daily_loss."""
    def mw(d: Decision) -> Decision | None:
        opened = nav_today_open()
        if opened <= 0:
            return d
        loss = (nav_now() - opened) / opened
        if loss < -abs(max_daily_loss):
            log.warning("circuit_breaker", loss=loss, threshold=max_daily_loss)
            return None
        return d
    return mw


def cap_gross_exposure(
    max_gross: float,
    *,
    other_targets: Callable[[Symbol], Mapping[Symbol, float]],
) -> Callable[[Decision], Decision | None]:
    """Veto if the new decision would push gross |weights| sum past max_gross."""
    def mw(d: Decision) -> Decision | None:
        others = other_targets(d.symbol)
        gross = sum(abs(w) for s, w in others.items() if s != d.symbol)
        if gross + abs(d.target_weight) > max_gross:
            log.info("gross_exposure_block", proposed=d.target_weight, gross=gross)
            return None
        return d
    return mw


def default_risk_middleware(
    cfg: RiskConfig,
    *,
    nav_today_open: Callable[[], float],
    nav_now: Callable[[], float],
    other_targets: Callable[[Symbol], Mapping[Symbol, float]] = lambda s: {},
) -> Callable[[Decision], Decision | None]:
    """Build the standard chain from a RiskConfig.

    Order matters: cap weights first (cheap), then gross, then circuit-breaker.
    """
    return compose_middleware([
        cap_position_weight(cfg.max_position_weight),
        cap_gross_exposure(cfg.max_gross_exposure, other_targets=other_targets),
        block_when_loss_exceeds(cfg.max_daily_loss,
                                nav_today_open=nav_today_open, nav_now=nav_now),
    ])


__all__ = [
    "compose_middleware",
    "cap_position_weight",
    "cap_gross_exposure",
    "block_when_loss_exceeds",
    "default_risk_middleware",
]
