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


def _journal_fill(m, *, run_id, order_id, side, qty, symbol="default:AAPL"):
    m["fills"][(run_id, order_id)] = {
        "order_id": order_id, "symbol": symbol, "side": side, "qty": qty,
        "price": 100.0, "fee": 0.0, "ts": utc_now().isoformat(), "venue": "test", "meta": {},
    }


def test_reconcile_nets_out_own_fills():
    """The bot's own journalled fills are not drift."""
    sym = Symbol("AAPL", AssetClass.EQUITY)
    broker = _StaticBroker({sym: Position(symbol=sym, qty=10, avg_price=100)})
    runner, m = _make_runner(broker)
    runner.reconcile(snapshot_label="t0")
    _journal_fill(m, run_id="r", order_id="o1", side="buy", qty=5)
    broker._positions[sym] = Position(symbol=sym, qty=15, avg_price=100)
    res = runner.reconcile(snapshot_label="t1")
    assert res["n_drifts"] == 0
    # Selling out completely is explained too (disappeared-symbol branch).
    _journal_fill(m, run_id="r", order_id="o2", side="sell", qty=15)
    broker._positions = {}
    assert runner.reconcile(snapshot_label="t2")["n_drifts"] == 0


def test_reconcile_reports_unexplained_residual():
    """A fee taken in kind (crypto buys) shows as the unexplained part only."""
    sym = Symbol("AAPL", AssetClass.EQUITY)
    broker = _StaticBroker({sym: Position(symbol=sym, qty=10, avg_price=100)})
    runner, m = _make_runner(broker)
    runner.reconcile(snapshot_label="t0")
    _journal_fill(m, run_id="r", order_id="o1", side="buy", qty=5)
    broker._positions[sym] = Position(symbol=sym, qty=14.99, avg_price=100)
    (d,) = runner.reconcile(snapshot_label="t1")["drifts"]
    assert d["explained_qty"] == 5
    assert d["unexplained_qty"] == pytest.approx(-0.01)
    assert m["drifts"][next(iter(m["drifts"]))]["unexplained_qty"] == pytest.approx(-0.01)


def test_reconcile_counts_each_fill_once():
    """A fill already in the previous snapshot, or journalled twice, counts once."""
    sym = Symbol("AAPL", AssetClass.EQUITY)
    broker = _StaticBroker({sym: Position(symbol=sym, qty=10, avg_price=100)})
    runner, m = _make_runner(broker)
    runner.reconcile(snapshot_label="t0")
    _journal_fill(m, run_id="r1", order_id="o1", side="buy", qty=5)
    _journal_fill(m, run_id="r2", order_id="o1", side="buy", qty=5)  # restart dup
    broker._positions[sym] = Position(symbol=sym, qty=15, avg_price=100)
    assert runner.reconcile(snapshot_label="t1")["n_drifts"] == 0
    # Next tick: nothing traded, the o1 fill must not be re-applied.
    assert runner.reconcile(snapshot_label="t2")["n_drifts"] == 0
