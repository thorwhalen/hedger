"""SMA crossover — the canonical first strategy.

Simple, transparent, and good as a sanity baseline for everything else
(including LLM strategies). If your fancy strategy can't beat SMA-cross
after costs, it's not the regime for it.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import pandas as pd

from hedger.base import Bar, Signal, Symbol, utc_now
from hedger.strategies import register


@register("sma_crossover")
def sma_crossover(
    bars: Mapping[Symbol, Iterable[Bar]],
    *,
    context: Mapping[str, Any] | None = None,
    fast: int = 20,
    slow: int = 50,
) -> Iterable[Signal]:
    """Long when fast SMA > slow SMA, flat when not. One signal per symbol per call.

    Parameters live as keyword args so the reflection loop can sweep them
    via partials without touching this file.
    """
    for symbol, bar_iter in bars.items():
        bar_list = list(bar_iter)
        if len(bar_list) < slow:
            continue
        closes = pd.Series([b.close for b in bar_list])
        fast_ma = closes.rolling(fast).mean().iloc[-1]
        slow_ma = closes.rolling(slow).mean().iloc[-1]
        if pd.isna(fast_ma) or pd.isna(slow_ma):
            continue
        score = 1.0 if fast_ma > slow_ma else 0.0
        yield Signal(
            symbol=symbol,
            ts=bar_list[-1].ts,
            score=score,
            strategy="sma_crossover",
            meta={"fast": fast, "slow": slow, "fast_ma": float(fast_ma), "slow_ma": float(slow_ma)},
        )
