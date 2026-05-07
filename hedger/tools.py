"""User-facing CLI commands (dispatched by `__main__.py`).

Each of these is a thin function over the package surface, suitable for
argh dispatch. Add a new command by writing a function and appending it
to `_dispatch_funcs`.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hedger.backtest import backtest_simple
from hedger.base import AssetClass, Symbol
from hedger.config import load_config
from hedger.data.sources import make_source
from hedger.install import install as install_cmd, where_keys as where_keys_cmd
from hedger.live import make_runner, run_scheduler
from hedger.reflection import reflect as reflect_cycle
from hedger.reflection.monitor import daily_brief
from hedger.strategies import available, get
from hedger.util import check_requirements, get_logger

log = get_logger("hedger.cli")


def doctor(*, broker: str = "alpaca", llm: bool = True) -> str:
    """Check the environment and report what's missing.

    Run this first. It tells you exactly which env vars or installs are
    needed for your chosen broker. Default broker is ``alpaca`` — the
    recommended starting target.
    """
    missing = check_requirements(broker=broker, llm=llm)
    if not missing:
        return "Environment looks good."
    out = ["Environment is missing things:"]
    for k, v in missing.items():
        out.append(f"  - {k}: {v}")
    if any(k.endswith("_API_KEY") or k.endswith("_SECRET_KEY") for k in missing):
        out.append("")
        out.append(
            "Hint: run `hedger install` to create a 600-mode envfile, "
            "then `hedger where-keys` to see exactly where to put them."
        )
    return "\n".join(out)


def list_strategies() -> str:
    """List registered strategies."""
    return "\n".join(available())


def fetch(
    symbol: str,
    *,
    timeframe: str = "1h",
    days: int = 30,
    source: str = "alpaca",
) -> str:
    """Fetch recent bars and print summary stats. For sanity-checking sources.

    Alpaca's data API has a ~15-minute delay on the free tier; we fetch up to
    20 minutes ago to avoid an empty trailing window.
    """
    src = make_source(source)
    ac = AssetClass.CRYPTO if "/" in symbol else AssetClass.EQUITY
    sym = Symbol(ticker=symbol, asset_class=ac)
    end = datetime.now(tz=timezone.utc)
    if source == "alpaca" and ac is not AssetClass.CRYPTO:
        end -= timedelta(minutes=20)
    start = end - timedelta(days=days)
    bars = list(src.bars(sym, start=start, end=end, timeframe=timeframe))
    if not bars:
        return f"No bars fetched for {symbol}."
    return json.dumps(
        {
            "n": len(bars),
            "first": bars[0].ts.isoformat(),
            "last": bars[-1].ts.isoformat(),
            "last_close": bars[-1].close,
        },
        indent=2,
    )


def backtest(
    *,
    strategy: str = "sma_crossover",
    symbols: str = "SPY,QQQ",
    timeframe: str = "1d",
    days: int = 365,
    source: str = "alpaca",
) -> str:
    """Backtest a registered strategy. Symbols comma-separated.

    Example:
        hedger backtest --strategy=sma_crossover --symbols=AAPL,MSFT --days=180
    """
    src = make_source(source)
    end = datetime.now(tz=timezone.utc)
    if source == "alpaca":
        end -= timedelta(minutes=20)  # respect free-tier delay on stock bars
    start = end - timedelta(days=days)
    bars_by_sym = {}
    for t in symbols.split(","):
        ac = AssetClass.CRYPTO if "/" in t else AssetClass.EQUITY
        sym = Symbol(ticker=t.strip(), asset_class=ac)
        bars_by_sym[sym] = list(src.bars(sym, start=start, end=end, timeframe=timeframe))
    strat = get(strategy)
    res = backtest_simple(strat, bars_by_sym)
    return json.dumps(res.summary(), indent=2)


def tick(
    *,
    strategy: str = "sma_crossover",
    config: str | None = None,
    broker: str | None = None,
    symbols: str | None = None,
) -> str:
    """Run a single live tick (paper by default). Useful for cron sanity.

    Pass ``--broker=paper`` to override config and use the in-memory paper
    broker (no Alpaca round-trip; useful when offline). Pass ``--symbols``
    to override the configured universe (comma-separated).
    """
    cfg = load_config(config)
    if broker or symbols:
        from dataclasses import replace

        new_broker = replace(cfg.broker, name=broker) if broker else cfg.broker
        new_universe = tuple(s.strip() for s in symbols.split(",")) if symbols else cfg.universe
        cfg = replace(cfg, broker=new_broker, universe=new_universe)
    runner = make_runner(cfg, strategy_name=strategy)
    summary = runner.tick()
    return json.dumps(summary, indent=2, default=str)


def serve(*, strategy: str = "sma_crossover", config: str | None = None) -> str:
    """Start the live scheduler (blocks). Includes overnight reflection."""
    cfg = load_config(config)
    runner = make_runner(cfg, strategy_name=strategy)
    run_scheduler(cfg, runner=runner, on_reflect=lambda: reflect_cycle())
    return "scheduler exited"


def reflect(*, dry_run: bool = False, config: str | None = None) -> str:
    """Trigger one reflection cycle now (instead of waiting for the cron)."""
    cfg = load_config(config)
    res = reflect_cycle(cfg=cfg, dry_run=dry_run)
    return json.dumps(res, indent=2, default=str)


def brief(*, config: str | None = None) -> str:
    """Print today's mall-derived brief without launching reflection."""
    from hedger.data import mall

    return json.dumps(daily_brief(mall()), indent=2, default=str)


def status(*, config: str | None = None, broker: str | None = None) -> str:
    """One-screen ops snapshot: NAV, positions, today's activity, last drift.

    Reads the broker for live truth, the mall for journalled history.
    Useful for SSH'd-in spot checks without writing ad-hoc queries.
    """
    from dataclasses import replace
    from hedger.data import mall as default_mall
    from hedger.execution.brokers import make_broker

    cfg = load_config(config)
    if broker:
        cfg = replace(cfg, broker=replace(cfg.broker, name=broker))
    bk = make_broker(cfg.broker.name)
    m = default_mall()

    out = {
        "broker": cfg.broker.name,
        "nav": None,
        "positions": [],
        "today": {},
        "recent_vetoes": [],
        "last_drift": None,
    }
    try:
        out["nav"] = bk.nav()
    except Exception as e:
        out["nav_error"] = f"{type(e).__name__}: {e}"
    try:
        out["positions"] = [
            {"symbol": str(p.symbol), "qty": p.qty, "avg_price": p.avg_price}
            for p in bk.positions().values()
        ]
    except Exception as e:
        out["positions_error"] = f"{type(e).__name__}: {e}"

    brief_today = daily_brief(m)
    out["today"] = {
        "signals": brief_today["counts"]["signals"],
        "decisions": brief_today["counts"]["decisions"],
        "fills": brief_today["counts"]["fills"],
        "approx_realized_cash_change": brief_today["approx_realized_cash_change"],
        "unfilled_orders": len(brief_today.get("unfilled_orders", [])),
    }

    return json.dumps(out, indent=2, default=str)


# Aliases so argh exposes them as `hedger install` / `hedger where-keys`
# without the importer-name collision (`install` would shadow the symbol).
def install(
    *, systemd: bool = False, envfile: str | None = None, workdir: str | None = None
) -> str:
    """Set up hedger for long-running deployment (idempotent envfile + systemd unit)."""
    return install_cmd(systemd=systemd, envfile=envfile, workdir=workdir)


def where_keys(*, envfile: str | None = None) -> str:
    """Show where hedger expects its secrets envfile and which keys are set."""
    return where_keys_cmd(envfile=envfile)


# SSOT for the dispatcher
_dispatch_funcs = [
    doctor,
    install,
    where_keys,
    list_strategies,
    fetch,
    backtest,
    tick,
    serve,
    reflect,
    brief,
    status,
]


if __name__ == "__main__":
    import argh

    argh.dispatch_commands(_dispatch_funcs)
