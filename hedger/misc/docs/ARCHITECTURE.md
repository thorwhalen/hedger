# Architecture

`hedger` is a layered, plugin-driven trading system. The whole design is shaped by one constraint: **the same code that backtests must paper-trade must live-trade**, and any of those modes must be runnable on a single VPS without additional services.

## The data flow

Every trading decision walks the same pipeline:

```
Bar  ─►  Strategy.score()  ─►  Signal  ─►  Sizer  ─►  Decision
                                                          │
                                                          ▼
                                              Risk middleware (compose)
                                                          │
                                                          ▼
                                              Tax policy (veto / annotate)
                                                          │
                                                          ▼
                                                      Broker.submit()
                                                          │
                                                          ▼
                                                       Order ─► Fill ─► Position
```

Every artifact in this flow — `Bar`, `Signal`, `Decision`, `Order`, `Fill`, `Position` — is a frozen dataclass defined once in `hedger/base.py` (the SSOT for the trading vocabulary). Every cross-cutting capability — strategies, sizers, brokers, tax policies, data sources — is a `Protocol` in the same file. **Concrete implementations import the Protocol, not the other way around**, so you can add a new broker without touching the runner.

## The four seams

There are four extension points worth knowing by name:

1. **`DataSource`** — anything that yields `Bar`s for a symbol. Three implementations ship: yfinance, Alpaca, CCXT. Add a fourth by implementing the Protocol and registering in `hedger/data/sources.py::SOURCES`.

2. **`Strategy`** — anything with a `.score(bars) -> Signal` method. Strategies live in `hedger/strategies/` and self-register via the `@register` decorator. Eight ship: the baselines `sma_crossover` (deterministic) and `llm_news` (Claude-based sentiment), plus six adapted from `misc/docs/Trading Strategies for the hedger Framework.md` — `donchian_breakout`, `bollinger_meanrev`, `xs_momentum`, `pairs_zscore`, `pca_residual_revert`, `pead_drift`.

3. **`Broker`** — `submit(order) -> Fill`, `positions()`, `equity()`. `PaperBroker` and `AlpacaBroker` ship; `IBKRBroker` is the obvious next addition.

4. **`TaxPolicy`** — `evaluate(decision, history) -> Decision | None` (None = veto). `NoTaxPolicy`, `USWashSalePolicy`, `CryptoLIFOPolicy` ship.

If you find yourself patching the `Runner` to add a feature, **stop** — the right answer is almost always a new plugin at one of these seams.

## The Mall — one Mapping to rule them all

State that needs to outlive a process (bars, signals, decisions, orders, fills, positions, reflections, costs) lives in the **mall**: a `dict[str, MutableMapping]` produced by `hedger/data/stores.py::mall()`. Each entry is a `MutableMapping[str, dict]`-shaped store — JSONL for append-only logs, parquet for columnar bars, easily extended.

Why a dict-of-mappings instead of a database?

- Zero infra cost. No database to install, back up, or upgrade.
- Inspectable on disk. `cat .hedger/decisions/2026-05-05.jsonl` is the audit trail.
- Mockable in tests. `mall = {"bars": {}, "decisions": {}, ...}` is a valid test mall.
- Swappable. Once you outgrow JSONL, replace one entry of the mall dict with a SQLite-backed mapping; nothing else changes.

This is the standard `dol`-style pattern from Thor's coding conventions.

## Risk middleware — composition over inheritance

`compose_middleware(m1, m2, m3)` returns a single function that runs the three in sequence, short-circuiting on any veto. Each middleware is a small pure function: `(decision, context) -> decision | None`. The default stack:

1. `cap_position_weight(0.10)` — clip any single-position weight.
2. `cap_gross_exposure(1.0)` — clip aggregate leverage.
3. `block_when_loss_exceeds(0.02)` — circuit-break on daily drawdown.

These are **sacred**. The reflection cycle is forbidden from weakening them (enforced socially via `CLAUDE.md`, and technically via a guardrail check at startup). Adding *new* middleware is fine; loosening the existing ones is not.

## The reflection cycle — why subprocess Claude Code, not the API

The nightly self-improvement loop spawns `claude` as a subprocess (`hedger/reflection/orchestrator.py`), not a direct `anthropic.messages.create()` call. Three reasons:

1. **Tool use.** Claude Code already knows how to read files, run pytest, edit code, and stage commits. Re-implementing that against the raw API would mean re-implementing Claude Code.
2. **Skills.** The `.claude/skills/` directory (reflection-cycle, strategy-development, data-pipeline) is loaded automatically by Claude Code. Direct API calls would have to reload them every session.
3. **Sandboxing.** Subprocess + git tag + pytest gate gives us cheap rollback. If the reflection cycle breaks the build, `git reset --hard <pre-tag>` undoes the night's work atomically.

The orchestrator's contract is small: snapshot → write daily brief → run `claude` with `REFLECT_PROMPT` → run pytest → rollback or commit. The actual *thinking* — what to improve, how to improve it — is delegated to Claude Code reading the brief and the skills.

## What the runner does

`hedger/live/runner.py::Runner.tick()` is the heartbeat:

1. For each symbol, fetch latest bars from the data source.
2. Persist new bars to `mall["bars"]`.
3. For each registered strategy, score and produce signals → `mall["signals"]`.
4. Pass signals to the sizer to produce decisions.
5. Run decisions through risk middleware → tax policy → `mall["decisions"]`.
6. Submit surviving decisions to the broker as orders → `mall["orders"]`.
7. Record fills → `mall["fills"]` → update positions.

`tick()` is idempotent on retry (the broker's order ID dedupes), so the scheduler can safely re-fire after a crash.

## What the scheduler does

`hedger/live/scheduler.py` is APScheduler with two jobs:

- A cron trigger for `runner.tick()` (default: every 4 hours during market hours, in `Europe/Paris`).
- A cron trigger for `reflection.reflect()` (default: 22:00 daily).

The scheduler runs in-process. There is no separate worker, no Redis, no Celery. If the bot needs to scale beyond what one VPS can do, the right answer is almost certainly to lower the trading frequency, not to add infrastructure.

## What lives where

```
hedger/
  base.py              ← dataclasses + Protocols (SSOT)
  config.py            ← Config dataclass + TOML/env loading
  util.py              ← check_requirements, structured logger
  data/
    sources.py         ← yfinance / Alpaca / CCXT
    stores.py          ← JsonlStore, BarStore, mall()
  strategies/
    __init__.py        ← register() decorator + autoload
    sma_crossover.py
    llm_news.py
    donchian_breakout.py
    bollinger_meanrev.py
    xs_momentum.py
    pairs_zscore.py
    pca_residual_revert.py
    pead_drift.py
  execution/
    brokers.py         ← PaperBroker, AlpacaBroker
    risk.py            ← compose_middleware + caps
    sizing.py          ← equal_weight, kelly_capped
  tax/
    policies.py        ← NoTaxPolicy, USWashSalePolicy, CryptoLIFOPolicy
  backtest/
    engine.py          ← backtest_simple (uses PaperBroker)
    sweep.py           ← param_sweep over backtest_simple
  research/            ← optional, behind [research] extra
    metrics.py         ← performance_summary, compare_strategies
    cointegration.py   ← find_cointegrated_pairs (Engle–Granger)
    factors.py         ← signal_ic, alphalens_clean_data
    tearsheet.py       ← html_tearsheet (quantstats), pyfolio_full_tearsheet
  live/
    runner.py          ← Runner.tick()
    scheduler.py       ← APScheduler wrapper
  reflection/
    monitor.py         ← daily_brief()
    orchestrator.py    ← reflect()
  tools.py             ← CLI commands (argh-dispatched)
  __main__.py          ← `hedger ...` entry point
.claude/
  skills/              ← reflection-cycle, strategy-development, data-pipeline
```

## Progressive disclosure in the public surface

Three levels of API:

- **Top-level imports** for the 90% case: `from hedger import make_runner, run_scheduler, backtest_simple`.
- **Subpackage imports** for the 9% case: `from hedger.execution.risk import compose_middleware, cap_position_weight`.
- **Module-internal symbols** (anything underscore-prefixed) are private and may change without notice.

The reflection cycle is allowed to add to the public surface but not break it. Backwards-incompatible changes require a human review.
