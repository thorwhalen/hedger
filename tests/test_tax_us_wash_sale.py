"""Tests for the US wash-sale tax policy."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hedger.base import AssetClass, Decision, Fill, Position, Side, Symbol, utc_now
from hedger.tax import get_policy
from hedger.tax.policies import USWashSalePolicy


_SYM = Symbol("AAPL", AssetClass.EQUITY)


def _decision(weight: float, ts: datetime | None = None) -> Decision:
    return Decision(
        symbol=_SYM,
        ts=ts or datetime(2026, 5, 5, tzinfo=timezone.utc),
        target_weight=weight,
        rationale="test",
    )


def _fill(side: Side, qty: float, price: float, ts: datetime) -> Fill:
    return Fill(order_id="x", symbol=_SYM, side=side, qty=qty, price=price,
                fee=0.0, ts=ts, venue="alpaca")


def test_long_decision_passes_through_unchanged():
    """The wash-sale rule only restricts sells; positive weights are untouched."""
    p = USWashSalePolicy()
    d = _decision(0.05)  # long
    assert p(d, positions={}, history={}) is d


def test_no_position_passes_through():
    """Can't trigger a wash sale on a position you don't hold."""
    p = USWashSalePolicy()
    d = _decision(-0.05)  # would-be sell
    assert p(d, positions={}, history={}) is d


def test_no_history_passes_through():
    """Without any prior fills, can't have a recent buy to trigger the rule."""
    p = USWashSalePolicy()
    d = _decision(-0.05)
    pos = {_SYM: Position(symbol=_SYM, qty=10, avg_price=100)}
    assert p(d, positions=pos, history={}) is d


def test_loss_sell_with_recent_buy_is_vetoed():
    """A sell that closes a loss with a buy in the 31-day window is vetoed."""
    p = USWashSalePolicy(window_days=31)
    pos = {_SYM: Position(symbol=_SYM, qty=10, avg_price=200)}
    decision_ts = datetime(2026, 5, 5, tzinfo=timezone.utc)
    history = {_SYM: [
        _fill(Side.BUY, 5, 200, decision_ts - timedelta(days=10)),  # recent buy
        _fill(Side.SELL, 5, 150, decision_ts - timedelta(hours=1)),  # last px = 150 < avg 200 = loss
    ]}
    d = _decision(-0.05, ts=decision_ts)
    assert p(d, positions=pos, history=history) is None


def test_loss_sell_without_recent_buy_passes():
    """Last buy outside the 31-day window: no wash sale."""
    p = USWashSalePolicy(window_days=31)
    pos = {_SYM: Position(symbol=_SYM, qty=10, avg_price=200)}
    decision_ts = datetime(2026, 5, 5, tzinfo=timezone.utc)
    history = {_SYM: [
        _fill(Side.BUY, 10, 200, decision_ts - timedelta(days=60)),  # outside window
        _fill(Side.SELL, 5, 150, decision_ts - timedelta(hours=1)),
    ]}
    d = _decision(-0.05, ts=decision_ts)
    out = p(d, positions=pos, history=history)
    assert out is d  # not vetoed


def test_gain_sell_passes_through():
    """A sell at a gain doesn't create a wash-sale loss to disallow."""
    p = USWashSalePolicy(window_days=31)
    pos = {_SYM: Position(symbol=_SYM, qty=10, avg_price=100)}
    decision_ts = datetime(2026, 5, 5, tzinfo=timezone.utc)
    history = {_SYM: [
        _fill(Side.BUY, 5, 100, decision_ts - timedelta(days=10)),  # recent buy
        _fill(Side.SELL, 5, 150, decision_ts - timedelta(hours=1)),  # last px > avg = gain
    ]}
    d = _decision(-0.05, ts=decision_ts)
    out = p(d, positions=pos, history=history)
    assert out is d


def test_registered_under_us_wash_sale():
    p = get_policy("us_wash_sale")
    assert isinstance(p, USWashSalePolicy)
