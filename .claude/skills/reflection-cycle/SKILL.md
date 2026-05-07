---
name: reflection-cycle
description: Use when running the overnight self-improvement cycle. Covers session planning, scope discipline, validation gates, and what to write to CHANGELOG. Trigger when the brief in .hedger/briefs/ is loaded, when invoked by `hedger reflect`, or when the orchestrator hands you a `REFLECT_PROMPT`.
---

# Overnight reflection cycle

You have **one cycle**, ~8 hours of wall-clock budget. Your job is to make
the bot a little better, *safely*, before the owner wakes up.

## Phase 1 — Read (30 min budget)

1. Read the daily brief in `.hedger/briefs/brief-YYYY-MM-DD.json`.
2. Read the last 3 entries of `hedger/misc/CHANGELOG.md` to avoid retracing.
3. Skim today's structured logs (`logs/*.jsonl` if present) for warnings
   and errors.
4. List the active strategies (`hedger list-strategies`).

## Phase 2 — Pick scope (15 min budget)

Choose **one** of these classes of change. Order roughly by safety:

1. **Tighten an observation.** Add a new metric or log to a strategy whose
   behaviour was confusing today. No behaviour change.
2. **Tune a parameter.** Sweep the kwargs of any registered strategy
   (e.g. `fast`/`slow` on sma_crossover or donchian_breakout, `window`/
   `n_std` on bollinger_meanrev, `formation_bars`/`skip_bars` on
   xs_momentum, `lookback`/`entry_z` on pairs_zscore or
   pca_residual_revert, threshold on llm_news). Keep the baseline; only
   commit a swap if the new parameters beat the old by a clear margin on
   the same window.
3. **Add a strategy.** Drop a new file in `hedger/strategies/`, register it,
   write a doctest, write at least one pytest. Do **not** enable it in
   `config.toml` — that's the owner's call.
4. **Refactor.** Only with a strong reason and only when tests fully cover
   the touched surface. Refactors should produce *no* behaviour change.

If you can't pick confidently, default to (1).

## Phase 3 — Implement (4–6h budget)

- Stay within the chosen scope. If the change wants to grow past ~150 LOC
  or touch more than 2 files, that's a signal to split: leave the rest
  as a TODO in CHANGELOG and stop.
- Follow the code style in existing files: dataclasses for shapes,
  Protocols for seams, generators by default, keyword-only after arg 3.
- Strategies must be pure over their inputs. State lives in stores.

## Phase 4 — Validate (1h budget)

Run, in order:

1. `pytest -q` — must be green.
2. `hedger backtest --strategy <touched> --symbols <today's universe> --days 90`
   — record the summary.
3. If the change is a parameter swap, also run the *prior* parameters and
   compare. Only commit if Sharpe and max_drawdown both improve, or if
   one improves and the other is within 5%.
4. Check that no risk middleware was bypassed: `grep -n risk_budget hedger/`.

### Research toolkit (use during validation, not in production code)

`hedger.research` (behind the `[research]` extra) wraps statsmodels,
empyrical-reloaded, pyfolio-reloaded, alphalens-reloaded, and quantstats
for reflection-time analysis. Each call gracefully raises an ImportError
with an install hint when the underlying lib is missing. Useful entry
points:

- `from hedger.research import performance_summary` —
  Sharpe / Sortino / Calmar / Omega / drawdown on a NAV series. Use this
  in the CHANGELOG metrics block instead of the hand-computed Sharpe
  from `BacktestResult.summary()` whenever you can.
- `from hedger.research.metrics import compare_strategies` — side-by-side
  scorecard sorted by Sharpe; the right shape for "before vs after"
  evidence in the CHANGELOG.
- `from hedger.research import signal_ic` — Spearman-rank IC of scores vs
  forward returns. Use *before* a parameter sweep to check that the
  underlying signal even has predictive power.
- `from hedger.research import find_cointegrated_pairs` — Engle–Granger
  pair screening; output is shaped for `pairs_zscore`'s
  `context['pairs']`.
- `from hedger.research import html_tearsheet` — single self-contained
  HTML report; persist under `logs/tearsheets/` and link from the
  CHANGELOG entry when a strategy ships.

Do **not** import `hedger.research` from inside a strategy or from the
runner — these tools are for analysis, not production decisions. Strategies
must stay pure and dependency-light.

## Phase 5 — Record (15 min budget)

Append to `hedger/misc/CHANGELOG.md`:

```
## YYYY-MM-DD — <one-line summary>

**Scope:** observation | tuning | new-strategy | refactor

**Changed:** <files>

**Why:** <one paragraph from the brief>

**Metrics (90-day backtest, same universe):**
- Before: sharpe=…, mdd=…, n_trades=…
- After:  sharpe=…, mdd=…, n_trades=…

**Watch tomorrow:** <what to look for in the next brief>

**TODO:** <if anything was deferred>
```

Then commit with a message that starts with `reflect: `.

## Hard guardrails

- Never trade on a non-paper broker.
- Never weaken `hedger/execution/risk.py`.
- Never increase `max_position_weight` or `max_gross_exposure` defaults.
- Never `pip install` anything not already in `pyproject.toml`.
- Never delete tests.
- If anything is unclear, leave the system unchanged and write your
  questions to `hedger/misc/docs/QUESTIONS.md` instead.
