"""Parameter sweeps over the simple backtester.

The existing ``backtest_simple`` runs one configuration at a time. This
module wraps it for cartesian-product parameter sweeps — useful when the
reflection cycle wants to ask "what's the best (fast, slow) for SMA on
this universe?".

Implementation note: this is *not* vectorised. ``backtest_vectorbt``
remains the right home for a true vector-engine backend, but writing
that requires re-expressing each strategy as vector operations on price
series. ``param_sweep`` is the pragmatic in-between — a sequential
sweep using the engine we already have, parallelisable via threads.

>>> from hedger.backtest.sweep import param_sweep  # smoke import
"""

from __future__ import annotations

import itertools
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from hedger.backtest.engine import backtest_simple
from hedger.base import Bar, Symbol
from hedger.execution.sizing import equal_weight_sizer


def _product(grid: Mapping[str, Iterable[Any]]) -> Iterable[dict]:
    keys = list(grid)
    for combo in itertools.product(*(list(grid[k]) for k in keys)):
        yield dict(zip(keys, combo))


def param_sweep(
    strategy: Callable,
    param_grid: Mapping[str, Iterable[Any]],
    bars: Mapping[Symbol, list[Bar]],
    *,
    sizer=equal_weight_sizer,
    starting_cash: float = 100_000.0,
    fee_bps: float = 5.0,
    slippage_bps: float = 2.0,
    max_workers: int = 1,
) -> pd.DataFrame:
    """Run ``backtest_simple`` over every param combination, return one row each.

    The returned DataFrame has one row per combo with columns:
    ``final_nav``, ``sharpe``, ``max_drawdown``, ``n_trades``, plus the
    sweep parameters themselves. Sorted by ``sharpe`` descending so the
    top of the frame is the best config.

    >>> from datetime import datetime, timedelta, timezone
    >>> from hedger.base import AssetClass, Bar, Symbol
    >>> from hedger.strategies.sma_crossover import sma_crossover
    >>> sym = Symbol('FAKE', AssetClass.EQUITY)
    >>> t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    >>> bars = {sym: [Bar(symbol=sym, ts=t0 + timedelta(days=i),
    ...               open=100+i, high=100+i+1, low=100+i-1, close=100+i,
    ...               volume=1) for i in range(80)]}
    >>> df = param_sweep(sma_crossover, {'fast': [5, 10], 'slow': [20, 30]}, bars)
    >>> set(df.columns) >= {'fast', 'slow', 'sharpe', 'final_nav'}
    True
    >>> len(df) == 4
    True
    """
    grid = list(_product(param_grid))

    def _run_one(combo: dict) -> dict:
        strat_partial = partial(strategy, **combo)
        # functools.partial lacks .__name__; backtest_simple doesn't read it,
        # but inject anyway for tracebacks/log lines.
        try:
            strat_partial.__name__ = (  # type: ignore[attr-defined]
                getattr(strategy, "__name__", "strategy")
                + "?"
                + ",".join(f"{k}={v}" for k, v in combo.items())
            )
        except (AttributeError, TypeError):
            pass
        res = backtest_simple(
            strat_partial,
            bars,
            sizer=sizer,
            starting_cash=starting_cash,
            fee_bps=fee_bps,
            slippage_bps=slippage_bps,
        )
        return {**combo, **res.summary()}

    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            rows = list(ex.map(_run_one, grid))
    else:
        rows = [_run_one(c) for c in grid]

    df = pd.DataFrame(rows)
    if "sharpe" in df.columns:
        df = df.sort_values("sharpe", ascending=False, na_position="last")
    return df.reset_index(drop=True)


__all__ = ["param_sweep"]
