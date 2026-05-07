"""Test that Runner.fetch_window uses the BarStore cache."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from typing import Iterable

import pytest

from hedger.base import AssetClass, Bar, Symbol, utc_now
from hedger.config import Config, DataConfig
from hedger.data.stores import BarStore, mall


class _FakeSource:
    """Counts bars() calls and returns a fixed sequence per call."""
    name = "alpaca"  # so the runner applies the 20-min trim path

    def __init__(self):
        self.calls: list[dict] = []

    def bars(self, symbol: Symbol, *, start: datetime, end: datetime,
             timeframe: str) -> Iterable[Bar]:
        self.calls.append({"start": start, "end": end, "tf": timeframe})
        # Generate hourly bars over the requested window.
        cur = start.replace(minute=0, second=0, microsecond=0)
        while cur <= end:
            yield Bar(symbol=symbol, ts=cur,
                      open=100.0, high=101.0, low=99.0, close=100.5, volume=1.0)
            cur += timedelta(hours=1)


def _runner_with(mall_obj):
    from hedger.execution.brokers import PaperBroker
    from hedger.live.runner import Runner
    from hedger.strategies.sma_crossover import sma_crossover  # registers
    cfg = Config(
        universe=("AAPL",),
        timeframe="1h",
        data=DataConfig(primary="alpaca", timeframe="1h"),
    )
    src = _FakeSource()
    broker = PaperBroker(price_fn=lambda s: 100.0)
    runner = Runner(config=cfg, strategy=sma_crossover,
                    broker=broker, source=src, mall=mall_obj)
    return runner, src


def test_first_fetch_calls_source_and_caches():
    m = mall(tempfile.mkdtemp())
    runner, src = _runner_with(m)
    sym = Symbol("AAPL", AssetClass.EQUITY)
    bars = runner.fetch_window(sym)
    assert bars
    assert len(src.calls) == 1
    # Second fetch should hit the cache, fetch only a thin tail.
    first_window = src.calls[0]["end"] - src.calls[0]["start"]
    runner.fetch_window(sym)
    assert len(src.calls) == 2
    second_window = src.calls[1]["end"] - src.calls[1]["start"]
    # The second fetch window should be MUCH smaller than the first.
    assert second_window < first_window / 10, (
        f"expected gap-fill, got {second_window} vs first {first_window}"
    )


def test_cache_persists_across_runners():
    """A new Runner with the same mall should reuse the cache."""
    root = tempfile.mkdtemp()
    m1 = mall(root)
    runner1, src1 = _runner_with(m1)
    sym = Symbol("AAPL", AssetClass.EQUITY)
    runner1.fetch_window(sym)
    initial_call_window = src1.calls[0]["end"] - src1.calls[0]["start"]

    # Fresh mall pointing at same root, fresh runner with fresh source.
    m2 = mall(root)
    runner2, src2 = _runner_with(m2)
    runner2.fetch_window(sym)
    assert len(src2.calls) == 1
    second_call_window = src2.calls[0]["end"] - src2.calls[0]["start"]
    assert second_call_window < initial_call_window / 10


def test_bar_store_write_and_read_roundtrip():
    sym = Symbol("AAPL", AssetClass.EQUITY)
    bars = [
        Bar(symbol=sym, ts=datetime(2026, 1, 1, h, tzinfo=timezone.utc),
            open=1, high=2, low=0.5, close=1.5, volume=10)
        for h in range(5)
    ]
    store = BarStore(tempfile.mkdtemp())
    n = store.write_bars(bars, timeframe="1h")
    assert n == 5
    back = store.read_bars(sym, "1h")
    assert len(back) == 5
    assert back[0].ts < back[-1].ts
    assert all(b.symbol == sym for b in back)
