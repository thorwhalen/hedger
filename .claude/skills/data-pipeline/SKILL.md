---
name: data-pipeline
description: Extending hedger's data layer — adding new data sources (broker APIs, news feeds, alt-data), new stores (parquet/SQLite/cloud), and feature pipelines. Use when the reflection cycle picks "I need data X to make decision Y" or when onboarding a new instrument class.
---

# Data Pipeline

The data layer is **two seams**: `DataSource` (read from world) and `Store` (read/write locally). Both are `MutableMapping`-shaped where it makes sense, so the rest of the code never special-cases backends.

## Adding a new DataSource

Implement the `DataSource` Protocol from `hedger.base`:

```python
class MyFeedSource:
    def get_bars(self, symbol: Symbol, start, end, *, timeframe="1d") -> Iterable[Bar]:
        ...
    def latest(self, symbol: Symbol, *, timeframe="1d") -> Bar | None:
        ...
```

Register it in `hedger/data/sources.py::make_source()` so config-by-name works:

```python
SOURCES["myfeed"] = MyFeedSource  # add one line
```

Then `data.source = "myfeed"` in `config.toml` is enough for the runner to pick it up. **No other file needs to change.**

## Adding a new Store

Stores are `MutableMapping[str, T]`. Look at `JsonlStore` and `BarStore` for two flavours: append-only log, and partitioned columnar.

Rules:
- Keys are strings (use `f"{symbol}|{timeframe}"` style for compound keys, never tuples).
- Values are `dict` (we serialize ourselves; don't pickle our dataclasses).
- `__contains__` should be cheap; readers iterate keys.
- Atomic writes: write to `tmp` then `os.replace` so crashes don't leave half-files.

Once written, add to `mall()` factory in the same file. The rest of the pipeline (runner, reflection, backtest) reads from `mall["whatever"]` and gets your store back.

## Feature pipelines

Features (RSI, sentiment scores, regime labels) are **stored**, not recomputed. Compute once in a `hedger/features/<name>.py` module, write to `mall["features:<name>"]`, then strategies read from there.

Why: the reflection cycle and the backtest engine both want the same features. Recomputing is non-deterministic (especially for LLM-based features). Caching them on disk gives reproducibility for free.

## Cost discipline for LLM features

Every LLM call costs money. Treat them like network calls to a paid API:

1. **Cache by content hash**, not by symbol+date. If two news articles are byte-identical, you score them once. Use `hashlib.sha1(article_text.encode()).hexdigest()[:16]` as the cache key.
2. **Batch**. If you're scoring 50 symbols' news, send one prompt with all 50 and parse the JSON back, not 50 separate calls.
3. **Cheap model first**, escalate. Haiku for routine sentiment scoring; only escalate to Sonnet/Opus when the cheap model returns "uncertain" or for the nightly reflection.
4. **Budget per cycle**. The runner's `tick()` should not be allowed to spend more than ~$0.10 on LLM calls. Track via `mall["costs"]` and circuit-break.

## Don't do

- Don't fetch live data inside a strategy's `score()`. Strategies read pre-fetched bars from the mall; data fetching is the runner's job.
- Don't store raw broker responses without normalising to our dataclasses. Future-you will regret it when you swap broker.
- Don't put secrets (API keys) in stores. They live in env vars / `config.toml` only.
