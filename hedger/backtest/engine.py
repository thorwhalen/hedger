"""Backtesting.

Two paths:
  * `backtest_simple` — a small event loop using the same Strategy/Sizer/
    Broker stack as live. Slow but **exact parity** with live execution
    code, so a passing backtest is meaningful evidence the live path works.
  * `backtest_vectorbt` — vectorised research mode via VectorBT (optional).
    Use it for parameter sweeps; promote winners to `backtest_simple` for
    realistic execution.

The reflection loop should *always* gate a strategy change with both:
sweep with vectorbt, then validate with the simple engine before promoting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Mapping

import pandas as pd

from hedger.base import Bar, Decision, Position, Sizer, Strategy, Symbol, utc_now
from hedger.execution.brokers import PaperBroker
from hedger.execution.sizing import equal_weight_sizer
from hedger.util import get_logger

log = get_logger("hedger.backtest")


@dataclass
class BacktestResult:
    nav: pd.Series
    trades: pd.DataFrame
    final_nav: float
    sharpe: float | None
    max_drawdown: float

    def summary(self) -> dict:
        return {
            "final_nav": self.final_nav,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "n_trades": len(self.trades),
        }


def backtest_simple(
    strategy: Strategy,
    bars: Mapping[Symbol, list[Bar]],
    *,
    sizer: Sizer = equal_weight_sizer,
    starting_cash: float = 100_000.0,
    fee_bps: float = 5.0,
    slippage_bps: float = 2.0,
    decision_middleware: Callable[[Decision], Decision | None] | None = None,
    context_fn: Callable[[datetime], dict] | None = None,
) -> BacktestResult:
    """Walk forward bar-by-bar, calling strategy and acting via PaperBroker.

    `bars` must have aligned timestamps. The backtester does not align for
    you (intentionally — alignment policy is a domain decision).
    """
    timestamps = sorted({b.ts for series in bars.values() for b in series})
    if not timestamps:
        return BacktestResult(pd.Series(dtype=float), pd.DataFrame(), starting_cash, None, 0.0)

    # price oracle
    last_price: dict[Symbol, float] = {}

    def price_fn(s: Symbol) -> float:
        return last_price.get(s, 0.0)

    broker = PaperBroker(
        starting_cash=starting_cash, fee_bps=fee_bps, slippage_bps=slippage_bps,
        price_fn=price_fn,
    )

    nav_series: list[tuple[datetime, float]] = []
    trades: list[dict] = []

    for i, ts in enumerate(timestamps):
        # advance prices to current bar
        window: dict[Symbol, list[Bar]] = {}
        for sym, series in bars.items():
            cut = [b for b in series if b.ts <= ts]
            if cut:
                last_price[sym] = cut[-1].close
                window[sym] = cut
        if not window:
            continue

        signals = list(strategy(window, context=(context_fn(ts) if context_fn else {})))
        if not signals:
            nav_series.append((ts, broker.nav()))
            continue

        positions = broker.positions()
        nav = broker.nav()
        decisions = list(sizer(signals, positions=positions, nav=nav))
        if decision_middleware:
            decisions = [d for d in (decision_middleware(d) for d in decisions) if d]

        for d in decisions:
            target_notional = d.target_weight * nav
            current = positions.get(d.symbol)
            current_notional = (current.qty * last_price.get(d.symbol, 0.0)) if current else 0.0
            delta_notional = target_notional - current_notional
            px = last_price.get(d.symbol, 0.0)
            if px <= 0:
                continue
            qty = delta_notional / px
            if abs(qty) < 1e-9:
                continue
            from hedger.base import Order, Side, OrderType
            order = Order(
                symbol=d.symbol,
                side=Side.BUY if qty > 0 else Side.SELL,
                qty=abs(qty),
                order_type=OrderType.MARKET,
            )
            broker.submit(order)
            for f in broker.fills():
                trades.append({
                    "ts": f.ts, "symbol": str(f.symbol), "side": f.side.value,
                    "qty": f.qty, "price": f.price, "fee": f.fee,
                })
        nav_series.append((ts, broker.nav()))

    nav_idx = pd.Series(dict(nav_series))
    nav_idx.index = pd.to_datetime(nav_idx.index, utc=True)
    rets = nav_idx.pct_change().dropna()
    sharpe = float((rets.mean() / rets.std()) * (252 ** 0.5)) if rets.std() else None
    dd = float((nav_idx / nav_idx.cummax() - 1).min())

    return BacktestResult(
        nav=nav_idx,
        trades=pd.DataFrame(trades),
        final_nav=float(nav_idx.iloc[-1]),
        sharpe=sharpe,
        max_drawdown=dd,
    )


def backtest_vectorbt(*args, **kwargs):
    """Optional VectorBT-backed backtester for fast sweeps. Still a stub.

    For parameter sweeps the right tool today is
    ``hedger.backtest.sweep.param_sweep`` — it runs ``backtest_simple``
    over the cartesian product of a parameter grid. Slower than a true
    vectorised engine but identical to what live execution does.

    A vectorbt-backed implementation belongs here; it should be
    benchmarked side-by-side against ``backtest_simple`` on a fixed
    fixture before being preferred for promote-decision evidence.
    """
    raise NotImplementedError(
        "Vectorbt backtester is a deferred extension. "
        "Use `hedger.backtest.sweep.param_sweep(strategy, grid, bars)` for "
        "parameter sweeps using the simple engine, or implement this "
        "function and add a benchmark comparing the two on the same data."
    )


__all__ = ["backtest_simple", "backtest_vectorbt", "BacktestResult"]
