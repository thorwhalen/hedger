# Instructions for Claude Code in this repository

You are working on **hedger**, a self-improving algorithmic trading bot.
This file is read by Claude Code on every session.

## House rules

1. **Sandbox-first.** Never enable `broker = "alpaca:live"` or any real-money
   broker spec. The owner moves to live manually after passing review.
2. **Risk middleware is sacred.** Do not modify `hedger/execution/risk.py`
   without (a) explicit owner approval in the task or (b) accompanying tests
   that demonstrate strictly stronger guarantees.
3. **Public surface is stable.** Don't change the dataclasses in
   `hedger/base.py` (`Bar`, `Signal`, `Decision`, `Order`, `Fill`, `Position`).
   Add new types alongside, with deprecation paths if needed.
4. **One change at a time.** Reflection sessions land **one** small change
   (target ≤150 lines, ideally one or two files), with tests, with a
   CHANGELOG entry. If the goal is bigger, leave a TODO.
5. **Test before committing.** `pytest -q` must pass. Backtest the
   touched strategy on the same window the bot used today.
6. **Document in the code.** Every new function gets a minimal docstring
   and, where feasible, a doctest. Follow the conventions in
   `hedger/base.py`.

## Architectural conventions

- **Dispatch over inheritance.** Strategies, brokers, sizers, tax policies,
  data sources are *functions or small dataclasses* registered by name.
  See `hedger/strategies/__init__.py` for the pattern.
- **Mappings over custom storage classes.** Anything persisted is a
  `MutableMapping` in `hedger/data/stores.py`. Add new stores by
  subclassing/composing the same way.
- **Middleware over branching.** Risk and tax checks are
  `Decision -> Decision | None` callables composed with
  `compose_middleware`. Add new checks as new middlewares.
- **Config is SSOT.** Settings live in `config.toml`; secrets in env.
  Don't hardcode magic numbers in new strategies — accept them as
  keyword args and let reflection sweep them.
- **Generators by default.** Yield, don't return lists, unless a list is
  strictly required. Saves memory on long histories.

## Available skills

`.claude/skills/` contains task-specific skills:

- `reflection-cycle/` — how to plan an overnight session safely.
- `strategy-development/` — adding/modifying strategies.
- `data-pipeline/` — extending data sources and stores.

Read the relevant SKILL.md before each major task.

## Files of interest

- `hedger/base.py` — types and Protocols (the SSOT for shapes).
- `hedger/strategies/` — plug-ins; `__init__.py` is the registry.
- `hedger/execution/` — brokers, sizers, risk middleware.
- `hedger/live/runner.py` — the application service that ticks live.
- `hedger/reflection/orchestrator.py` — what spawned you.
- `hedger/misc/CHANGELOG.md` — append a dated entry per session.
- `hedger/misc/docs/ARCHITECTURE.md` — read this for the why.
- `hedger/misc/docs/RESEARCH.md` — the standing research brief.
