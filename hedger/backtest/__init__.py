"""Backtesting: simple bar-by-bar parity engine; vectorbt hook; param sweep."""

from hedger.backtest.engine import BacktestResult, backtest_simple, backtest_vectorbt
from hedger.backtest.sweep import param_sweep

__all__ = ["backtest_simple", "backtest_vectorbt", "BacktestResult", "param_sweep"]
