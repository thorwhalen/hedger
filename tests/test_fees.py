"""Tests for journalling broker fee activities into mall["fees"].

Alpaca reports fees only as account activities (``CFEE`` for crypto, ``FEE``
for regulatory), never on the order, so fills carry ``fee=0.0`` and the fee
journal is the one place the cost is recorded.
"""

from __future__ import annotations

import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

from hedger.config import Config, DataConfig
from hedger.data.stores import mall
from hedger.execution.brokers import AlpacaBroker

# Shapes copied from real paper-account responses (ids shortened).
_CFEE_ASSET = {
    "id": "20261102::e551",
    "activity_type": "CFEE",
    "date": "2026-11-02",
    "created_at": "2026-11-01T00:34:56.068547Z",
    "net_amount": "0",
    "description": "Coin Pair Transaction Fee (Non USD)",
    "symbol": "BTCUSD",
    "qty": "-0.0000025",
    "price": "77590.01",
    "currency": "USD",
}
_CFEE_USD = {
    "id": "20261104::abfd",
    "activity_type": "CFEE",
    "date": "2026-11-04",
    "created_at": "2026-11-04T06:34:57.049191Z",
    "net_amount": "-0.05",
    "description": "Coin Pair Transaction Fee (USD)",
    "currency": "USD",
}


def _broker(monkeypatch, activities):
    client = MagicMock()
    client.get.return_value = activities
    monkeypatch.setitem(
        sys.modules, "alpaca.trading.client", SimpleNamespace(TradingClient=lambda *a, **k: client)
    )
    return AlpacaBroker(api_key="x", secret="y", paper=True), client


class _FeeBroker:
    """Runner-facing stand-in exposing only what journal_fees uses."""

    name = "fee-stub"

    def __init__(self, batches):
        self._batches = list(batches)

    def fees(self):
        batch = self._batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return iter(batch)


def _runner(broker):
    from hedger.live.runner import Runner
    from hedger.strategies.sma_crossover import sma_crossover

    cfg = Config(
        universe=("AAPL",), timeframe="1h", data=DataConfig(primary="alpaca", timeframe="1h")
    )
    m = mall(tempfile.mkdtemp())
    return Runner(config=cfg, strategy=sma_crossover, broker=broker, source=None, mall=m), m


def test_alpaca_fees_queries_fee_activities_and_normalises(monkeypatch):
    broker, client = _broker(monkeypatch, [_CFEE_ASSET, _CFEE_USD])
    rows = list(broker.fees())

    path, params = client.get.call_args[0]
    assert path == "/account/activities"
    assert params["activity_types"] == "CFEE,FEE"
    assert "after" in params

    asset, usd = rows
    assert asset["symbol"] == "default:BTC/USD"
    assert asset["qty"] == -0.0000025
    assert abs(asset["usd"] - (-0.0000025 * 77590.01)) < 1e-12
    assert usd["symbol"] is None and usd["usd"] == -0.05


def test_journal_fees_dedupes_overlapping_windows():
    first = [dict(id="a", usd=-0.1), dict(id="b", usd=-0.2)]
    second = [dict(id="b", usd=-0.2), dict(id="c", usd=-0.3)]  # 'b' seen again
    runner, m = _runner(_FeeBroker([first, second]))

    assert runner.journal_fees() == 2
    assert runner.journal_fees() == 1
    assert sorted(m["fees"]) == ["a", "b", "c"]
    # 'b' was appended to the file once only.
    lines = m["fees"].path.read_text().splitlines()
    assert len(lines) == 3


def test_journal_fees_is_fail_soft_and_skips_brokers_without_fees():
    runner, m = _runner(_FeeBroker([RuntimeError("activities endpoint down")]))
    assert runner.journal_fees() == 0
    assert len(m["fees"]) == 0

    no_fees = SimpleNamespace(name="paper")  # e.g. PaperBroker: fees live on fills
    runner, m = _runner(no_fees)
    assert runner.journal_fees() == 0
