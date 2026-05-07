"""Research / analysis utilities for reflection mode.

This package wraps the optional research stack — statsmodels, empyrical,
pyfolio, alphalens, quantstats — behind thin facades so that:

* hedger core works without these libraries installed; importing
  ``hedger.research`` is always cheap.
* The reflection cycle has a stable, narrow surface to call into:
  ``performance_summary``, ``find_cointegrated_pairs``, ``signal_ic``,
  ``html_tearsheet``.
* Each facade emits an informative ImportError pointing at the
  ``hedger[research]`` extra when its backing library is missing.

Install with ``pip install -e .[research]`` (from this repo).
"""

from __future__ import annotations

from hedger.research.cointegration import find_cointegrated_pairs
from hedger.research.factors import signal_ic
from hedger.research.metrics import performance_summary
from hedger.research.tearsheet import html_tearsheet

__all__ = [
    "performance_summary",
    "find_cointegrated_pairs",
    "signal_ic",
    "html_tearsheet",
]
