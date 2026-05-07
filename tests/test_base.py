"""Tests for hedger.base."""

from datetime import datetime, timezone

from hedger.base import (
    AssetClass,
    Bar,
    Fill,
    Position,
    Side,
    Symbol,
    utc_now,
)


def test_symbol_str():
    s = Symbol("AAPL", AssetClass.EQUITY)
    assert "AAPL" in str(s)


def test_position_buy_then_sell_realizes_pnl():
    sym = Symbol("AAPL", AssetClass.EQUITY)
    pos = Position(symbol=sym)
    buy = Fill(order_id="1", symbol=sym, side=Side.BUY, qty=10, price=100.0,
               fee=0.0, ts=utc_now(), venue="paper")
    pos.apply(buy)
    assert pos.qty == 10
    assert pos.avg_price == 100.0

    sell = Fill(order_id="2", symbol=sym, side=Side.SELL, qty=10, price=110.0,
                fee=0.0, ts=utc_now(), venue="paper")
    pos.apply(sell)
    assert pos.qty == 0
    assert pos.realized_pnl > 0


def test_bar_constructs_cleanly():
    b = Bar(symbol=Symbol("AAPL", AssetClass.EQUITY),
            ts=datetime(2026, 1, 2, tzinfo=timezone.utc),
            open=1, high=2, low=0.5, close=1.5, volume=100)
    assert b.close == 1.5
    assert b.symbol.ticker == "AAPL"
