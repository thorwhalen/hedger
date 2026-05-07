"""Tests for the parameter sweep helper."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hedger.backtest.sweep import param_sweep
from hedger.base import AssetClass, Bar, Symbol
from hedger.strategies.sma_crossover import sma_crossover  # registers


def _synth_bars(n=120, start_px=100.0, drift=0.1):
    sym = Symbol("FAKE", AssetClass.EQUITY)
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return {sym: [
        Bar(symbol=sym, ts=t0 + timedelta(days=i),
            open=start_px + i*drift - 0.1,
            high=start_px + i*drift + 0.2,
            low=start_px + i*drift - 0.3,
            close=start_px + i*drift,
            volume=10_000)
        for i in range(n)
    ]}


def test_sweep_runs_full_grid():
    bars = _synth_bars()
    df = param_sweep(sma_crossover,
                     {"fast": [10, 20], "slow": [40, 60]},
                     bars)
    assert len(df) == 4
    assert {"fast", "slow"}.issubset(df.columns)
    assert {"final_nav", "sharpe", "max_drawdown", "n_trades"}.issubset(df.columns)


def test_sweep_results_sorted_by_sharpe():
    bars = _synth_bars()
    df = param_sweep(sma_crossover,
                     {"fast": [5, 10, 15], "slow": [30, 50]},
                     bars)
    sharpes = df["sharpe"].dropna().tolist()
    assert sharpes == sorted(sharpes, reverse=True)


def test_sweep_with_threading():
    bars = _synth_bars()
    df = param_sweep(sma_crossover,
                     {"fast": [10, 20], "slow": [40, 60]},
                     bars,
                     max_workers=2)
    assert len(df) == 4


def test_sweep_single_param():
    bars = _synth_bars()
    df = param_sweep(sma_crossover, {"fast": [10]}, bars)
    assert len(df) == 1
