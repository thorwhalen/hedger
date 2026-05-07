"""Performance metrics on a NAV time series.

The default ``BacktestResult.summary()`` only reports Sharpe and max
drawdown. For reflection-cycle reviews we want a fuller scorecard:
Sortino (downside-only deviation), Calmar (annual return / max DD),
Omega, tail ratio, time in market.

If ``empyrical-reloaded`` is installed, we delegate to it for the canonical
implementations. If not, we fall back to a pandas-based implementation
covering the headline metrics only — so that ``performance_summary`` is
still callable in a stripped-down environment.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd


_PERIODS_PER_YEAR_DEFAULT = 252  # daily bars; override via kwarg


def _to_returns(nav: pd.Series) -> pd.Series:
    """NAV series -> simple returns. Drops the first NaN."""
    nav = pd.Series(nav).astype(float).sort_index()
    return nav.pct_change().dropna()


def _fallback_summary(returns: pd.Series, periods_per_year: int) -> dict[str, float]:
    """Stdlib/pandas fallback when empyrical isn't installed."""
    if returns.empty:
        out = {
            k: float("nan")
            for k in (
                "annual_return",
                "annual_volatility",
                "sharpe",
                "sortino",
                "calmar",
                "max_drawdown",
                "skew",
                "kurtosis",
            )
        }
        out["n_observations"] = 0
        return out
    mean = float(returns.mean())
    std = float(returns.std(ddof=1))
    ann_ret = (1 + mean) ** periods_per_year - 1
    ann_vol = std * np.sqrt(periods_per_year)
    downside = returns[returns < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else float("nan")
    nav = (1 + returns).cumprod()
    dd = float((nav / nav.cummax() - 1).min())
    sharpe = ann_ret / ann_vol if ann_vol > 0 else float("nan")
    sortino = (
        ann_ret / (downside_std * np.sqrt(periods_per_year))
        if downside_std and downside_std > 0
        else float("nan")
    )
    calmar = ann_ret / abs(dd) if dd < 0 else float("nan")
    return {
        "annual_return": float(ann_ret),
        "annual_volatility": float(ann_vol),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "calmar": float(calmar),
        "max_drawdown": float(dd),
        "skew": float(returns.skew()),
        "kurtosis": float(returns.kurtosis()),
        "n_observations": int(len(returns)),
    }


def performance_summary(
    nav: pd.Series,
    *,
    periods_per_year: int = _PERIODS_PER_YEAR_DEFAULT,
    use_empyrical: bool | None = None,
) -> dict[str, float]:
    """Return a dict of headline performance metrics for a NAV series.

    Pass ``use_empyrical=False`` to force the pandas fallback even when the
    library is available (useful for reproducibility in tests). Pass
    ``True`` to require empyrical and surface a clear error if missing.
    Default ``None`` uses empyrical if available, else falls back.

    >>> import pandas as pd
    >>> nav = pd.Series([100, 101, 102, 101.5, 103, 104],
    ...     index=pd.date_range('2026-01-01', periods=6, freq='D'))
    >>> s = performance_summary(nav, use_empyrical=False)
    >>> {'sharpe', 'sortino', 'calmar', 'max_drawdown'}.issubset(s)
    True
    >>> s['n_observations']
    5
    """
    returns = _to_returns(nav)
    if use_empyrical is False:
        return _fallback_summary(returns, periods_per_year)
    try:
        from hedger.research._optional import require

        emp = require("empyrical")
    except ImportError:
        if use_empyrical is True:
            raise
        return _fallback_summary(returns, periods_per_year)
    if returns.empty:
        return _fallback_summary(returns, periods_per_year)
    return {
        "annual_return": float(
            emp.annual_return(returns, period="daily", annualization=periods_per_year)
        ),
        "annual_volatility": float(
            emp.annual_volatility(returns, period="daily", annualization=periods_per_year)
        ),
        "sharpe": float(emp.sharpe_ratio(returns, period="daily", annualization=periods_per_year)),
        "sortino": float(
            emp.sortino_ratio(returns, period="daily", annualization=periods_per_year)
        ),
        "calmar": float(emp.calmar_ratio(returns, period="daily", annualization=periods_per_year)),
        "max_drawdown": float(emp.max_drawdown(returns)),
        "omega": float(emp.omega_ratio(returns)),
        "tail_ratio": float(emp.tail_ratio(returns)),
        "skew": float(returns.skew()),
        "kurtosis": float(returns.kurtosis()),
        "n_observations": int(len(returns)),
    }


def compare_strategies(
    navs: Mapping[str, pd.Series],
    *,
    periods_per_year: int = _PERIODS_PER_YEAR_DEFAULT,
) -> pd.DataFrame:
    """Return a DataFrame of metrics x strategies, sorted by Sharpe descending.

    >>> import pandas as pd
    >>> idx = pd.date_range('2026-01-01', periods=10, freq='D')
    >>> navs = {'a': pd.Series(range(100, 110), index=idx),
    ...         'b': pd.Series(range(110, 100, -1), index=idx)}
    >>> df = compare_strategies(navs, periods_per_year=252)
    >>> list(df.columns)
    ['a', 'b']
    """
    rows = {
        name: performance_summary(nav, periods_per_year=periods_per_year)
        for name, nav in navs.items()
    }
    df = pd.DataFrame(rows)
    if "sharpe" in df.index:
        df = df[df.loc["sharpe"].sort_values(ascending=False).index]
    return df
