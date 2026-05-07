"""Tests for risk middleware composition."""

from datetime import datetime, timezone

from hedger.base import AssetClass, Decision, Symbol
from hedger.execution.risk import (
    block_when_loss_exceeds,
    cap_position_weight,
    compose_middleware,
)


def _decision(weight: float) -> Decision:
    return Decision(
        symbol=Symbol("AAPL", AssetClass.EQUITY),
        ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        target_weight=weight,
        rationale="test",
    )


def test_cap_position_weight_clips_long():
    mw = cap_position_weight(0.05)
    out = mw(_decision(0.20))
    assert out is not None
    assert out.target_weight == 0.05


def test_cap_position_weight_clips_short():
    mw = cap_position_weight(0.05)
    out = mw(_decision(-0.50))
    assert out is not None
    assert out.target_weight == -0.05


def test_loss_breaker_vetoes():
    mw = block_when_loss_exceeds(
        max_daily_loss=0.02,
        nav_today_open=lambda: 100_000.0,
        nav_now=lambda: 95_000.0,  # -5% loss
    )
    assert mw(_decision(0.05)) is None


def test_compose_short_circuits():
    chain = compose_middleware([
        cap_position_weight(0.10),
        lambda d: None,            # second mw vetoes
        lambda d: _decision(0.99), # never reached
    ])
    assert chain(_decision(0.05)) is None
