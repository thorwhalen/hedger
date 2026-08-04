---
name: strategy-development
description: Use when adding, modifying, or tuning a strategy plug-in in hedger/strategies/. Covers the registry decorator, signal contract, parameterisation conventions, doctests, and how to wire a strategy into the runner and backtester.
---

# Strategy development

A strategy is **a callable** that takes a window of bars and yields signals.
That's it. It is *not* a class, does *not* manage state, does *not* talk
to brokers. Composition with sizers/risk/brokers happens in the runner.

## Signature

```python
from hedger.base import Bar, Signal, Symbol
from hedger.strategies import register


@register("my_strategy_name")
def my_strategy(
    bars: Mapping[Symbol, Iterable[Bar]],
    *,
    context: Mapping[str, Any] | None = None,
    # all tunable knobs as keyword args with defaults
    lookback: int = 50,
    threshold: float = 0.0,
) -> Iterable[Signal]:
    """One-line docstring. What does this strategy bet on?"""
    for symbol, bar_iter in bars.items():
        ...
        yield Signal(
            symbol=symbol,
            ts=last_bar.ts,
            score=...,  # in [-1, 1]
            strategy="my_strategy_name",
            meta={"reason": "...", "lookback": lookback},
        )
```

## Rules

1. **No globals.** State that needs to persist (e.g. an EMA) goes in
   `context` or in a store; not in module variables.
2. **Generators.** Yield signals; don't accumulate.
3. **Score is bounded.** `score in [-1, 1]`. Sign = direction, magnitude =
   conviction. The sizer turns conviction into weight; don't size yourself.
4. **Empty is fine.** Yielding zero signals means "no opinion this tick".
5. **Knobs as kwargs.** Every tunable parameter is a keyword arg with a
   default, so reflection can sweep without editing the file.
6. **Meta carries provenance.** Put anything an auditor would need to
   reconstruct *why* the signal fired.

## Tests

Every strategy needs:

- A doctest in the docstring or a sibling `test_<name>.py` in `tests/`.
- At least one synthetic-data test that confirms the strategy fires on a
  data shape it should fire on (e.g. an obvious uptrend for a trend
  follower).

## Backtesting

```bash
hedger backtest --strategy my_strategy_name --symbols SPY,QQQ --days 365
```

The summary returns `{final_nav, sharpe, max_drawdown, n_trades}`. For
parameter sweeps, write a small script that calls `backtest_simple` in
a loop. Don't enshrine the winner — record it in CHANGELOG so the next
session can see what was tried.

## When to add a new strategy vs. modify an existing one

- Different *signal* (different feature/idea): new strategy.
- Same idea, better implementation: modify in place, keep the doctest
  unchanged so we know behaviour didn't drift.

## Common mistakes

- **Look-ahead bias.** Only use bars whose `ts <= now`. The runner does
  this automatically; don't reach into the future via context.
- **Survivorship bias.** Don't filter the universe at backtest time by
  things that depend on the future (e.g. "stocks that exist today").
- **Calling out from the strategy.** No HTTP, no broker calls, no
  database writes inside a strategy. Pure functions only.
