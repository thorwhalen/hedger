"""Factor / signal-quality diagnostics.

Information Coefficient (IC) is the Spearman-rank correlation between a
signal at time ``t`` and the forward return realised between ``t`` and
``t + horizon`` bars. A positive, stable IC is the cleanest evidence that
a signal has predictive power *before* sizing and execution costs muddy
the picture — this is what alphalens reports in detail.

This module provides:

* ``signal_ic`` — a one-shot summary of the IC distribution across time.
* ``alphalens_clean_data`` — a thin adapter that turns hedger Signals + Bars
  into the (factor MultiIndex, prices DataFrame) shape that alphalens
  expects, so the reflection cycle can drop into a full alphalens tear
  sheet without rewriting the plumbing.
"""

from __future__ import annotations

from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from hedger.base import Bar, Signal, Symbol
from hedger.research._optional import require


def _signals_to_frame(signals: Iterable[Signal]) -> pd.DataFrame:
    rows = [
        {"date": s.ts, "symbol": str(s.symbol), "score": float(s.score), "strategy": s.strategy}
        for s in signals
    ]
    if not rows:
        return pd.DataFrame(columns=["date", "symbol", "score", "strategy"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df


def _closes_to_frame(bars: Mapping[Symbol, Iterable[Bar]]) -> pd.DataFrame:
    rows = []
    for sym, series in bars.items():
        for b in series:
            rows.append({"date": b.ts, "symbol": str(sym), "close": float(b.close)})
    if not rows:
        return pd.DataFrame(columns=["date", "symbol", "close"]).set_index(["date", "symbol"])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], utc=True)
    return df.pivot(index="date", columns="symbol", values="close").sort_index()


def signal_ic(
    signals: Iterable[Signal],
    bars: Mapping[Symbol, Iterable[Bar]],
    *,
    horizon_bars: int = 1,
) -> dict[str, float]:
    """Spearman-rank IC between scores and forward ``horizon_bars`` returns.

    Returns ``{"mean_ic", "ic_std", "ic_t_stat", "n_observations"}``.
    Empty input or single-symbol input gives all-NaN.

    >>> from datetime import datetime, timedelta, timezone
    >>> from hedger.base import AssetClass, Bar, Signal, Symbol
    >>> t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    >>> a = Symbol('A', AssetClass.EQUITY); b = Symbol('B', AssetClass.EQUITY)
    >>> def mk(sym, prices):
    ...     return [Bar(symbol=sym, ts=t0+timedelta(days=i),
    ...         open=p, high=p, low=p, close=p, volume=1) for i, p in enumerate(prices)]
    >>> bars = {a: mk(a, [100, 101, 102, 103]), b: mk(b, [100, 99, 98, 97])}
    >>> sigs = [Signal(symbol=a, ts=t0+timedelta(days=i), score=0.5,
    ...             strategy='x') for i in range(3)]
    >>> sigs += [Signal(symbol=b, ts=t0+timedelta(days=i), score=-0.5,
    ...              strategy='x') for i in range(3)]
    >>> ic = signal_ic(sigs, bars, horizon_bars=1)
    >>> ic['mean_ic'] > 0
    True
    """
    sf = _signals_to_frame(signals)
    pf = _closes_to_frame(bars)
    if sf.empty or pf.empty:
        return {
            "mean_ic": float("nan"),
            "ic_std": float("nan"),
            "ic_t_stat": float("nan"),
            "n_observations": 0,
        }
    fwd = pf.pct_change(horizon_bars).shift(-horizon_bars)
    fwd_long = fwd.stack().rename("forward_return").reset_index()
    fwd_long.columns = ["date", "symbol", "forward_return"]
    merged = sf.merge(fwd_long, on=["date", "symbol"], how="inner").dropna(
        subset=["score", "forward_return"]
    )
    if merged.empty:
        return {
            "mean_ic": float("nan"),
            "ic_std": float("nan"),
            "ic_t_stat": float("nan"),
            "n_observations": 0,
        }
    ics = (
        merged.groupby("date")
        .apply(
            lambda g: (
                g["score"].corr(g["forward_return"], method="spearman")
                if g["symbol"].nunique() > 1
                else np.nan
            ),
            include_groups=False,
        )
        .dropna()
    )
    if ics.empty:
        return {
            "mean_ic": float("nan"),
            "ic_std": float("nan"),
            "ic_t_stat": float("nan"),
            "n_observations": 0,
        }
    mean_ic = float(ics.mean())
    ic_std = float(ics.std(ddof=1)) if len(ics) > 1 else float("nan")
    t_stat = mean_ic / (ic_std / np.sqrt(len(ics))) if ic_std and ic_std > 0 else float("nan")
    return {
        "mean_ic": mean_ic,
        "ic_std": float(ic_std),
        "ic_t_stat": float(t_stat),
        "n_observations": int(len(ics)),
    }


def alphalens_clean_data(
    signals: Iterable[Signal],
    bars: Mapping[Symbol, Iterable[Bar]],
    *,
    quantiles: int = 5,
    periods: tuple[int, ...] = (1, 5, 10),
):
    """Wrap signals + bars into alphalens' ``factor_data`` MultiIndex DataFrame.

    Returns whatever ``alphalens.utils.get_clean_factor_and_forward_returns``
    returns, or raises a clear ImportError if alphalens isn't installed.
    Use directly with alphalens' tear-sheet functions.
    """
    al = require("alphalens.utils", extra="research")
    sf = _signals_to_frame(signals)
    if sf.empty:
        raise ValueError("No signals supplied to alphalens_clean_data.")
    factor = sf.set_index(["date", "symbol"])["score"]
    prices = _closes_to_frame(bars)
    return al.get_clean_factor_and_forward_returns(
        factor=factor,
        prices=prices,
        quantiles=quantiles,
        periods=periods,
    )
