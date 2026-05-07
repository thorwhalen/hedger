"""Tests for Runner.reconcile() position drift detection."""

from __future__ import annotations

import tempfile
from typing import Iterable, Mapping

import pytest

from hedger.base import AssetClass, Bar, Fill, Position, Symbol, utc_now
from hedger.config import Config, DataConfig
from hedger.data.stores import mall


class _StaticBroker:
    """Minimal Broker stand-in: the positions / nav we tell it to report."""
    name = "static"

    def __init__(self, positions: dict[Symbol, Position], nav: float = 100_000.0):
        self._positions = positions
        self._nav = nav

    def submit(self, order):  # pragma: no cover
        return "id"

    def cancel(self, order_id):  # pragma: no cover
        pass

    def fills(self):  # pragma: no cover
        return iter(())

    def positions(self):
        return dict(self._positions)

    def nav(self):
        return self._nav


def _make_runner(broker):
    from hedger.live.runner import Runner
    from hedger.strategies.sma_crossover import sma_crossover  # registers
    cfg = Config(universe=("AAPL",), timeframe="1h",
                 data=DataConfig(primary="alpaca", timeframe="1h"))
    m = mall(tempfile.mkdtemp())
    runner = Runner(config=cfg, strategy=sma_crossover,
                    broker=broker, source=None, mall=m)  # type: ignore[arg-type]
    return runner, m


def test_first_reconcile_no_drift():
    sym = Symbol("AAPL", AssetClass.EQUITY)
    broker = _StaticBroker({sym: Position(symbol=sym, qty=10, avg_price=100)})
    runner, m = _make_runner(broker)
    res = runner.reconcile(snapshot_label="t0")
    assert res["n_drifts"] == 0
    assert res["n_positions"] == 1
    # Snapshot was persisted.
    assert len(m["positions"]) == 1


def test_reconcile_detects_qty_change():
    sym = Symbol("AAPL", AssetClass.EQUITY)
    broker = _StaticBroker({sym: Position(symbol=sym, qty=10, avg_price=100)})
    runner, _ = _make_runner(broker)
    runner.reconcile(snapshot_label="t0")
    # Broker reports a different qty next time.
    broker._positions[sym] = Position(symbol=sym, qty=15, avg_price=100)
    res = runner.reconcile(snapshot_label="t1")
    assert res["n_drifts"] == 1
    assert res["drifts"][0]["broker_qty"] == 15
    assert res["drifts"][0]["prev_qty"] == 10


def test_reconcile_detects_disappeared_symbol():
    """If the journal had a position the broker no longer sees, flag it."""
    sym_a = Symbol("AAPL", AssetClass.EQUITY)
    sym_b = Symbol("MSFT", AssetClass.EQUITY)
    broker = _StaticBroker({
        sym_a: Position(symbol=sym_a, qty=10, avg_price=100),
        sym_b: Position(symbol=sym_b, qty=5, avg_price=200),
    })
    runner, _ = _make_runner(broker)
    runner.reconcile(snapshot_label="t0")
    # Drop MSFT from broker view.
    broker._positions = {sym_a: Position(symbol=sym_a, qty=10, avg_price=100)}
    res = runner.reconcile(snapshot_label="t1")
    drift_symbols = {d["symbol"] for d in res["drifts"]}
    assert "default:MSFT" in drift_symbols


def test_reconcile_safe_with_no_positions_slot():
    """Test malls (a plain dict without positions key) shouldn't crash."""
    from hedger.live.runner import Runner
    from hedger.strategies.sma_crossover import sma_crossover  # noqa: F401
    sym = Symbol("AAPL", AssetClass.EQUITY)
    broker = _StaticBroker({sym: Position(symbol=sym, qty=10, avg_price=100)})
    cfg = Config(universe=("AAPL",), timeframe="1h",
                 data=DataConfig(primary="alpaca", timeframe="1h"))
    runner = Runner(config=cfg, strategy=sma_crossover, broker=broker,
                    source=None, mall={})  # type: ignore[arg-type]
    res = runner.reconcile(snapshot_label="bare")
    assert res["n_drifts"] == 0  # nothing to compare against
