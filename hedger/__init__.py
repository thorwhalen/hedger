"""hedger — self-improving algorithmic trading bot.

Public surface (everything in __all__ is what users should rely on):

    Config, load_config        : configuration SSOT
    mall                       : the default {name -> store} mapping
    make_runner, run_scheduler : live trading entry points
    backtest_simple            : in-process backtester (parity with live)
    reflect                    : trigger an overnight reflection cycle now
    available_strategies, register_strategy
    check_requirements         : tell the user what env they're missing

Quick start:

    >>> import hedger
    >>> 'backtest_simple' in hedger.__all__
    True
    >>> 'make_runner' in hedger.__all__
    True
"""

from hedger.backtest import BacktestResult, backtest_simple
from hedger.base import (
    AssetClass,
    Bar,
    Decision,
    Fill,
    Order,
    Position,
    Side,
    Signal,
    Symbol,
)
from hedger.config import Config, load_config
from hedger.data import mall
from hedger.execution import default_risk_middleware, equal_weight_sizer, kelly_capped_sizer
from hedger.live import make_runner, run_scheduler
from hedger.reflection import reflect
from hedger.strategies import available as available_strategies
from hedger.strategies import register as register_strategy
from hedger.util import check_requirements, get_logger

__all__ = [
    # types
    "AssetClass",
    "Bar",
    "Decision",
    "Fill",
    "Order",
    "Position",
    "Side",
    "Signal",
    "Symbol",
    # config + storage
    "Config",
    "load_config",
    "mall",
    # entry points
    "make_runner",
    "run_scheduler",
    "backtest_simple",
    "BacktestResult",
    "reflect",
    # extension
    "available_strategies",
    "register_strategy",
    "default_risk_middleware",
    "equal_weight_sizer",
    "kelly_capped_sizer",
    # ux
    "check_requirements",
    "get_logger",
]
