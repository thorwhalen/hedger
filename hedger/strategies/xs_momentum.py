"""Cross-sectional momentum (Jegadeesh-Titman 1993, "12-1") — multivariate.

Rank the universe by past return over ``formation_bars`` (skipping the most
recent ``skip_bars`` to dodge the 1-bar reversal effect). Long the top
``top_quantile`` and short the bottom ``bottom_quantile`` with score ±1.

Real but cyclical — the March-2009 momentum crash cost the factor ~70% in
three months. See §2.3 / §3.3 of the strategies report.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from hedger.base import Bar, Signal, Symbol
from hedger.strategies import register


@register("xs_momentum")
def xs_momentum(
    bars: Mapping[Symbol, Iterable[Bar]],
    *,
    context: Mapping[str, Any] | None = None,
    formation_bars: int = 252,
    skip_bars: int = 21,
    top_quantile: float = 0.2,
    bottom_quantile: float = 0.2,
    min_universe: int = 5,
) -> Iterable[Signal]:
    """Rank universe by skip-adjusted past return; long winners, short losers.

    >>> from hedger.base import AssetClass, Bar, Symbol
    >>> from datetime import datetime, timedelta, timezone
    >>> t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    >>> def series(name, slope):
    ...     s = Symbol(name, AssetClass.EQUITY)
    ...     return s, [Bar(symbol=s, ts=t0+timedelta(days=i),
    ...         open=100+i*slope, high=100+i*slope, low=100+i*slope,
    ...         close=100+i*slope, volume=1) for i in range(300)]
    >>> bars = dict(s for s in [series('UP', 0.1), series('FLAT', 0.0),
    ...     series('UP2', 0.2), series('DN', -0.05), series('DN2', -0.1)])
    >>> sigs = list(xs_momentum(bars, formation_bars=200, skip_bars=5))
    >>> {s.symbol.ticker: s.score for s in sigs}['UP2']
    1.0
    >>> {s.symbol.ticker: s.score for s in sigs}['DN2']
    -1.0
    """
    rets: dict[Symbol, float] = {}
    last_ts = None
    need = formation_bars + skip_bars + 1
    for symbol, bar_iter in bars.items():
        w = list(bar_iter)
        if len(w) < need:
            continue
        c_then = w[-need].close
        c_now = w[-skip_bars - 1].close
        if c_then <= 0:
            continue
        rets[symbol] = (c_now / c_then) - 1.0
        last_ts = w[-1].ts
    if len(rets) < min_universe or last_ts is None:
        return
    ranked = sorted(rets.items(), key=lambda kv: kv[1])
    n = len(ranked)
    n_top = max(1, int(n * top_quantile))
    n_bot = max(1, int(n * bottom_quantile))
    losers = dict(ranked[:n_bot])
    winners = dict(ranked[-n_top:])
    for sym, r in winners.items():
        yield Signal(
            symbol=sym, ts=last_ts, score=1.0, strategy="xs_momentum",
            meta={"ret_form": float(r), "side": "winner",
                  "formation_bars": formation_bars, "skip_bars": skip_bars},
        )
    for sym, r in losers.items():
        yield Signal(
            symbol=sym, ts=last_ts, score=-1.0, strategy="xs_momentum",
            meta={"ret_form": float(r), "side": "loser",
                  "formation_bars": formation_bars, "skip_bars": skip_bars},
        )
