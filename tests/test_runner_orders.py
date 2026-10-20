"""Runner-level order computation: open orders and the per-asset cap on crypto."""

from __future__ import annotations

import tempfile
from datetime import timedelta

import pytest

from hedger.base import AssetClass, Bar, Order, Position, Side, Signal, Symbol, utc_now
from hedger.config import Config, DataConfig
from hedger.data.stores import mall
from hedger.execution.brokers import PaperBroker
from hedger.live.runner import Runner

BTC = Symbol("BTC/USD", AssetClass.CRYPTO)
NAV = 100_000.0
PRICE = 50_000.0


class _Source:
    name = "alpaca"

    def bars(self, symbol, *, start, end, timeframe):
        cur = start.replace(minute=0, second=0, microsecond=0)
        while cur <= end:
            yield Bar(symbol=symbol, ts=cur, open=PRICE, high=PRICE, low=PRICE,
                      close=PRICE, volume=1.0)
            cur += timedelta(hours=1)


def _bullish(bars, **_):
    for sym in bars:
        yield Signal(symbol=sym, ts=utc_now(), score=1.0, strategy="always_long")


class _Broker(PaperBroker):
    """PaperBroker that records submissions instead of filling them."""

    def __init__(self, held_qty=0.0, open_orders=()):
        super().__init__(price_fn=lambda s: PRICE)
        self.cash = NAV - held_qty * PRICE
        if held_qty:
            self._positions[BTC] = Position(symbol=BTC, qty=held_qty)
        self._pending = list(open_orders)
        self.submitted: list[Order] = []

    def submit(self, order):
        self.submitted.append(order)
        return "id"

    def open_orders(self):
        return list(self._pending)


def _runner(broker):
    cfg = Config(universe=("BTC/USD",), timeframe="1h", data=DataConfig(primary="alpaca", timeframe="1h"))
    return Runner(config=cfg, strategy=_bullish, broker=broker, source=_Source(),
                  mall=mall(tempfile.mkdtemp()))


def test_held_btc_is_sold_back_down_to_the_per_asset_cap():
    """1.12 BTC (~56% of NAV) against a 10% cap: sell down to 0.2 BTC."""
    broker = _Broker(held_qty=1.12)
    _runner(broker).tick()
    assert len(broker.submitted) == 1
    order = broker.submitted[0]
    assert order.symbol == BTC and order.side is Side.SELL
    cap_qty = 0.10 * NAV / PRICE
    assert order.qty == pytest.approx(1.12 - cap_qty, rel=1e-2)


def test_an_unfilled_sell_is_not_recomputed_next_tick():
    """The sell-down is already working at the broker, so nothing new is sent."""
    working = Order(symbol=BTC, side=Side.SELL, qty=0.92)
    broker = _Broker(held_qty=1.12, open_orders=[working])
    _runner(broker).tick()
    assert broker.submitted == []


def test_a_broker_without_open_orders_still_works():
    broker = _Broker(held_qty=0.0)
    broker.open_orders = None  # type: ignore[assignment]  # as if absent
    runner = _runner(broker)
    assert runner.pending_qty() == {}
