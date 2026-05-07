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
2. **Tune a parameter.** Sweep `fast`/`slow` for sma_crossover, threshold
   for llm_news, etc. Keep the baseline; only commit a swap if the new
   parameters beat the old by a clear margin on the same window.
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
