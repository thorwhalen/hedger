"""Smoke test of the backtest engine with synthetic prices."""

from datetime import datetime, timedelta, timezone

from hedger.backtest import backtest_simple
from hedger.base import AssetClass, Bar, Symbol
from hedger.strategies import get
from hedger.strategies.sma_crossover import sma_crossover  # noqa: F401  (registers)


def _synth_bars(n: int = 120) -> list[Bar]:
    sym = Symbol("FAKE", AssetClass.EQUITY)
    bars = []
    px = 100.0
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(n):
        # gentle uptrend with noise the SMAs can latch onto
        trend = i * 0.1
        wiggle = (1 if i % 7 < 4 else -1) * 0.5
        c = 100.0 + trend + wiggle
        bars.append(Bar(
            symbol=sym, ts=t0 + timedelta(days=i),
            open=c - 0.1, high=c + 0.2, low=c - 0.3, close=c, volume=10_000,
        ))
    return bars


def test_backtest_runs_and_reports_nav():
    sym = Symbol("FAKE", AssetClass.EQUITY)
    bars = {sym: _synth_bars(120)}
    res = backtest_simple(get("sma_crossover"), bars, fee_bps=2, slippage_bps=1)
    summary = res.summary()
    assert "final_nav" in summary
    assert summary["final_nav"] > 0
    # We don't assert profitability — just that the engine runs end-to-end.
