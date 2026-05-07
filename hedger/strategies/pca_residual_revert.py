"""PCA residual mean reversion — Avellaneda & Lee (2010) statistical arbitrage.

Run PCA on the universe's recent return matrix, residualise each name against
the top ``n_factors`` factors, and trade the cumulative residual back to its
mean. Score is ``-tanh(z / entry_z)`` of the residual z-score (contrarian).

Original paper reported Sharpe ~1.4 pre-2003 decaying to ~0.9 post-2003;
modern post-cost reality on liquid US equities sits at 0.3-0.6. See §3.5.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

import numpy as np

from hedger.base import Bar, Signal, Symbol
from hedger.strategies import register


def _aligned_returns(
    bars: Mapping[Symbol, Iterable[Bar]],
    lookback: int,
) -> tuple[list[Symbol], np.ndarray] | None:
    """Build a (lookback, n_symbols) return matrix from the trailing window.

    Skips symbols with too-short history. Returns ``None`` if fewer than two
    symbols have a full ``lookback + 1`` bars.
    """
    universe: list[Symbol] = []
    cols: list[np.ndarray] = []
    for sym, bar_iter in bars.items():
        w = list(bar_iter)
        if len(w) < lookback + 1:
            continue
        closes = np.array([b.close for b in w[-(lookback + 1):]], dtype=float)
        if (closes <= 0).any():
            continue
        rets = np.diff(closes) / closes[:-1]
        universe.append(sym)
        cols.append(rets)
    if len(universe) < 2:
        return None
    return universe, np.column_stack(cols)


@register("pca_residual_revert")
def pca_residual_revert(
    bars: Mapping[Symbol, Iterable[Bar]],
    *,
    context: Mapping[str, Any] | None = None,
    lookback: int = 60,
    n_factors: int = 5,
    entry_z: float = 1.25,
) -> Iterable[Signal]:
    """Avellaneda-Lee PCA residual stat-arb across the supplied universe.

    >>> from hedger.base import AssetClass, Bar, Symbol
    >>> from datetime import datetime, timedelta, timezone
    >>> import random
    >>> random.seed(0)
    >>> t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    >>> # Three co-moving names; one with an idiosyncratic spike at the end.
    >>> def mk(name, drift, spike=0.0):
    ...     s = Symbol(name, AssetClass.EQUITY); px = 100.0; out = []
    ...     for i in range(80):
    ...         px *= 1 + drift + random.gauss(0, 0.001)
    ...         out.append(Bar(symbol=s, ts=t0+timedelta(days=i),
    ...             open=px, high=px, low=px, close=px, volume=1))
    ...     out[-1] = Bar(symbol=s, ts=out[-1].ts, open=out[-1].open,
    ...         high=out[-1].high, low=out[-1].low,
    ...         close=out[-1].close * (1 + spike), volume=1)
    ...     return s, out
    >>> bars = dict([mk('A', 0.001), mk('B', 0.001), mk('C', 0.001, spike=0.05)])
    >>> sigs = list(pca_residual_revert(bars, lookback=60, n_factors=2,
    ...     entry_z=0.5))
    >>> any(s.symbol.ticker == 'C' and s.score < 0 for s in sigs)
    True
    """
    aligned = _aligned_returns(bars, lookback)
    if aligned is None:
        return
    universe, rets = aligned
    n_factors_eff = min(n_factors, rets.shape[1])
    cov = np.cov(rets, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(cov)
    top = eigvecs[:, -n_factors_eff:]
    factor_rets = rets @ top
    last_ts_by_sym: dict[Symbol, Any] = {
        sym: list(bars[sym])[-1].ts for sym in universe
    }
    for i, sym in enumerate(universe):
        beta, *_ = np.linalg.lstsq(factor_rets, rets[:, i], rcond=None)
        resid = rets[:, i] - factor_rets @ beta
        cum = np.cumsum(resid)
        mean = float(cum.mean())
        std = float(cum.std(ddof=1))
        if std == 0:
            continue
        z = (float(cum[-1]) - mean) / std
        if abs(z) < entry_z:
            continue
        score = -math.tanh(z / entry_z)
        yield Signal(
            symbol=sym,
            ts=last_ts_by_sym[sym],
            score=float(score),
            strategy="pca_residual_revert",
            meta={"z": float(z), "n_factors": n_factors_eff,
                  "lookback": lookback, "entry_z": entry_z},
        )
