"""Pair selection by cointegration.

The ``pairs_zscore`` strategy expects ``context['pairs']`` to be a list of
``(Symbol, Symbol, beta)`` triples — but where do those come from?

This module screens a universe by Engle–Granger cointegration:
    1. For each ordered pair (a, b), regress closes(a) ~ closes(b) over a
       lookback window and run an Augmented Dickey–Fuller test on the
       residuals.
    2. Keep pairs with p < ``pvalue_threshold``.
    3. Return the OLS hedge ratio ``beta`` to plug into ``context['pairs']``.

Hidden cost: O(n²) pairs in the universe size. Cap the universe before
calling this from the reflection cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from hedger.base import Bar, Symbol
from hedger.research._optional import require


@dataclass(frozen=True, slots=True)
class CointegrationResult:
    """Result of an Engle–Granger test on one ordered pair."""

    a: Symbol
    b: Symbol
    beta: float
    pvalue: float
    n_observations: int

    def as_pair_triple(self) -> tuple[Symbol, Symbol, float]:
        """Shape the pairs_zscore strategy expects in ``context['pairs']``."""
        return (self.a, self.b, self.beta)


def _close_series(window: Iterable[Bar]) -> np.ndarray:
    return np.asarray([b.close for b in window], dtype=float)


def find_cointegrated_pairs(
    bars: Mapping[Symbol, Iterable[Bar]],
    *,
    lookback: int = 250,
    pvalue_threshold: float = 0.05,
    min_observations: int | None = None,
) -> list[CointegrationResult]:
    """Return ordered pairs whose Engle–Granger p-value is below the threshold.

    The hedge ratio ``beta`` is the OLS slope of ``a ~ b`` over the same
    window used for the test. Pairs are returned sorted by ascending p-value.

    Requires ``statsmodels`` (in the ``hedger[research]`` extra).
    """
    sm = require("statsmodels.api", extra="research")
    coint = require("statsmodels.tsa.stattools", extra="research").coint
    min_obs = min_observations or max(50, lookback // 2)

    series_by_sym: dict[Symbol, np.ndarray] = {}
    for sym, bar_iter in bars.items():
        closes = _close_series(bar_iter)
        if len(closes) >= min_obs:
            series_by_sym[sym] = closes[-lookback:] if len(closes) > lookback else closes

    out: list[CointegrationResult] = []
    syms = list(series_by_sym)
    for i in range(len(syms)):
        for j in range(len(syms)):
            if i == j:
                continue
            a, b = syms[i], syms[j]
            xa, xb = series_by_sym[a], series_by_sym[b]
            n = min(len(xa), len(xb))
            if n < min_obs:
                continue
            xa, xb = xa[-n:], xb[-n:]
            if (xa <= 0).any() or (xb <= 0).any():
                continue
            try:
                _stat, pvalue, _crit = coint(xa, xb)
            except Exception:
                continue
            if pvalue >= pvalue_threshold:
                continue
            X = sm.add_constant(xb)
            beta_hat = float(sm.OLS(xa, X).fit().params[1])
            out.append(
                CointegrationResult(
                    a=a,
                    b=b,
                    beta=beta_hat,
                    pvalue=float(pvalue),
                    n_observations=n,
                )
            )
    out.sort(key=lambda r: r.pvalue)
    return out
