# Changelog

All notable changes to `hedger` are recorded here. Reflection-cycle commits append entries automatically; human commits should follow the same pattern.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/), but optimised for the reflection cycle to read and write.

---

## 2026-05-07 — Public release: qbot → hedger v0.1.0

The codebase moved from the private `tt/p_fin/qbot/` sandbox into its own
public repo at `https://github.com/thorwhalen/hedger`. Module + CLI + env
vars + state directory all renamed `qbot` → `hedger` / `QBOT` → `HEDGER` /
`.qbot/` → `.hedger/`. No behavioural change. Version bumped to 0.1.0.

---

## 2026-05-06 — Research toolkit for reflection mode

Adds a `hedger.research` package with thin facades over the optional
research stack so the reflection cycle has standard tools for performance
analysis, pair selection, and signal-quality diagnostics — without coupling
hedger core to heavyweight libraries.

### Added

- **`hedger/research/`** package with four modules:
  - `metrics.performance_summary(nav)` — Sharpe / Sortino / Calmar / Omega /
    drawdown battery via `empyrical-reloaded`, with a pandas-only fallback
    when empyrical isn't installed so hedger core stays callable.
  - `metrics.compare_strategies({name: nav, ...})` — side-by-side scorecard
    sorted by Sharpe.
  - `cointegration.find_cointegrated_pairs(bars)` — Engle–Granger screen
    via `statsmodels`, returning `(a, b, β)` triples ready to drop into
    `pairs_zscore`'s `context['pairs']`.
  - `factors.signal_ic(signals, bars)` — Spearman rank IC of scores vs
    forward returns; the reflection cycle's first sanity check on a new
    signal before promoting to a strategy.
  - `factors.alphalens_clean_data(...)` — adapter into `alphalens-reloaded`
    for full IC / quantile-spread tear sheets when wanted.
  - `tearsheet.html_tearsheet(nav, path)` — self-contained HTML report
    via `quantstats`.
  - `tearsheet.pyfolio_full_tearsheet(nav)` — pyfolio panels for notebook
    review.
- `hedger/research/_optional.require(modname)` — single-source helper that
  raises a precise `ImportError` pointing at `pip install -e .[research]`
  when an optional dep is missing. Each facade goes through it.

### Changed

- **`pyproject.toml` `[research]` extras** now include `statsmodels`,
  `empyrical-reloaded`, `pyfolio-reloaded`, `alphalens-reloaded`, and
  `quantstats` alongside the existing matplotlib / plotly / seaborn /
  scikit-learn. Install with `pip install -e .[research]`.

### Tests

- `tests/test_research.py` — 10 tests covering `require`, fallback &
  empyrical paths of `performance_summary`, cointegration recovery on
  synthetic A/B/C universes, IC sign on perfectly predictive signals,
  and end-to-end HTML tearsheet write. All four facade modules also
  carry doctests.

### Notes

- `riskfolio-lib` (HRP / ERC / Black–Litterman) and `tsfresh` (auto
  features) are deliberately not pulled in yet — `riskfolio-lib` brings
  cvxpy and several solvers, and tsfresh is heavy. Add them when an HRP
  sizer or ML strategy actually lands.
- The `pandas-ta` core dependency remains a known supply-chain concern
  per the strategies report; replacing it with `pandas-ta-classic` or
  TA-Lib is a separate, deliberate change.
- Avoided per the strategies report: original Quantopian forks of pyfolio
  / empyrical / alphalens (abandoned) and `mlfinlab` / `arbitragelab`
  (restrictive licence).

---

## 2026-05-06 — Six new strategy plug-ins from the strategies report

Adds the price-only / context-light strategies recommended in
`misc/docs/Trading Strategies for the hedger Framework.md` §3.1–3.6 as
discrete plug-in modules under `hedger/strategies/`. Each is a pure callable
on `(bars, *, context, **kwargs)` and registers via the existing decorator,
so the runner, backtester, and reflection sweep pick them up automatically.

### Added

- `donchian_breakout` (§3.1) — univariate trend / time-series momentum;
  ATR-scaled `tanh` score on Donchian-channel breaks.
- `bollinger_meanrev` (§3.2) — univariate contrarian; fades z-score
  deviations beyond `n_std` from the rolling mean.
- `xs_momentum` (§3.3) — Jegadeesh–Titman cross-sectional momentum with
  `formation_bars` / `skip_bars` knobs and quantile cuts.
- `pairs_zscore` (§3.4) — cointegration-based pairs trading on
  `(sym_a, sym_b, beta)` triples injected via `context['pairs']`.
- `pca_residual_revert` (§3.5) — Avellaneda–Lee residual stat-arb on the
  supplied universe.
- `pead_drift` (§3.6) — post-earnings-announcement drift; gracefully
  no-ops when `context['earnings']` is absent (fundamentals feed not yet
  plumbed).

### Tests

- `tests/test_new_strategies.py` — registry check, fire-on-shape +
  silence-on-shape sanity per strategy, backtest-end-to-end smoke for the
  two univariate strategies, plus parametric scope checks. Each strategy
  also carries a doctest.

### Notes

- §3.7 (`news_sentiment`) and §3.8 (`llm_committee`) overlap with the
  existing `llm_news` plug-in; deferred until a FinBERT-or-equivalent local
  scorer is decided on, to avoid duplicating the LLM-call surface.
- `pead_drift` is a no-op until earnings events flow through `context`.
  Wiring an earnings feed is a follow-up under the data-pipeline skill.

---

## 2026-05-06 — Launch-and-walk-away defaults + reflection cost guardrails

`hedger serve` now does something useful out of the box, and the reflection
cycle has a real spending cap rather than just a wall-clock timeout.

### Changed

- **Default `universe`** is now `("SPY", "QQQ", "BTC/USD")` instead of empty,
  so a fresh install with no `config.toml` actually trades on the paper broker
  rather than ticking against nothing.
- **Default `reflection.enabled`** is now `false`. The overnight self-improvement
  loop is opt-in — flip it on once you trust the live runner and have `claude`
  (Claude Code) on PATH.

### Added

- **`reflection.max_turns`** (default `50`) — pre-emptive coarse cap on the
  Claude Code subprocess. Forwarded as `--max-turns`; each turn ≈ one model call.
  Set to `null` / omit to disable.
- **`reflection.max_usd`** (default `5.0`) — post-hoc soft cap. After the
  session, the orchestrator parses `total_cost_usd` from Claude Code's
  `--output-format json` envelope; if exceeded, it logs a warning, fires the
  configured notifier, and records `cost_over_cap=true` in the mall entry.
  The `reflect()` return dict now includes `cost_usd`.
- `hedger.reflection.orchestrator._extract_cost_usd` — tolerant parser that
  scans Claude Code stdout for the cost envelope, with a doctest.

### Notes

- True mid-run `$`-cap enforcement would require switching to
  `--output-format stream-json` and killing the subprocess on cumulative cost.
  Deferred: post-hoc + `--max-turns` is enough for current safety needs and
  keeps the orchestrator simple.

---

## 2026-05-06 — Safer secrets onboarding (`hedger install` + TOML guardrail + envfile auto-load)

Setting up a long-running deployment used to require hand-writing a systemd
unit and remembering to `chmod 600` an envfile. New CLI commands collapse
that to one step, the same envfile is auto-loaded for interactive CLI use,
and the config loader now refuses secrets that landed in `config.toml` by
mistake. README rewritten around the new flow.

### Added

- **`hedger install`** — idempotent setup. Creates the secrets envfile with
  mode 600 (`/etc/hedger.env` as root, `$XDG_CONFIG_HOME/hedger/hedger.env`
  otherwise) populated with placeholders for the known secrets. With
  `--systemd`, also writes a unit file (system or `--user` scope, picked
  by `geteuid`) that points at the envfile via `EnvironmentFile=` and
  resolves the hedger binary via `shutil.which`/`sys.executable` — no
  developer-machine paths baked in. Never overwrites existing files;
  re-runs report state and re-tighten permissions.
- **`hedger where-keys`** — prints the envfile path, current mode, which
  `KNOWN_SECRETS` are set vs. missing, and the exact `$EDITOR <path>`
  command (auto-prefixed with `sudo` when needed).
- **`hedger.install.is_secret_key_name`** — the predicate (`*_key`,
  `*_secret`, `*_token`, `*_password`, `anthropic_*`, `alpaca_*`) shared
  by the install machinery and the config-loader guardrail.
- **`hedger.install.load_envfile_into_environ`** — minimal `KEY=VALUE`
  parser, called from `hedger/__main__.py:main()` so every CLI invocation
  picks up the canonical envfile (path: argument → `HEDGER_ENVFILE` env var
  → `default_envfile()`). `override=False`: pre-existing env vars (incl.
  systemd `EnvironmentFile=`) always win. Warns once to stderr when the
  envfile mode is laxer than 600.

### Changed

- **`hedger.config.load_config`** now raises `ValueError` if any TOML key
  (top-level or in nested tables) matches the secret-shape predicate.
  Error message points the user at `hedger install` / `hedger where-keys`.
  Catches the foot-gun where someone pastes `anthropic_api_key = "..."`
  into `config.toml` and silently lands it in `Config.extras`.
- **`hedger doctor`** appends a hint about `hedger install` / `hedger where-keys`
  whenever the missing-things list contains an `*_API_KEY` / `*_SECRET_KEY`.
- **`hedger/__main__.py:main()`** auto-loads the canonical envfile before
  dispatch, so interactive `hedger tick` / `hedger doctor` work with the same
  secrets store as the systemd unit. Library imports (`import hedger`)
  unaffected.
- **README** rewritten around `hedger install` + `hedger where-keys`; the old
  "export ALPACA_API_KEY=..." quickstart is gone. Default editor in
  printed commands is `pico` (overridable via `$EDITOR`).

### Tests

- `tests/test_install.py` — envfile mode 600, idempotency on re-run,
  systemd unit content + non-clobber, `where-keys` reporting, predicate
  positives/negatives.
- `tests/test_config_secrets.py` — top-level and nested secret keys raise;
  clean configs still load; alpaca-prefixed keys refused.

---

## 2026-05-05 — Closing v0.1 known limitations (autonomous session)

This entry covers the work that follows the Alpaca-first refactor in the
same day, addressing six of the v0.1 known-limitations / pick-up items
in the original CHANGELOG, plus a few latent bugs surfaced along the way.

### Changed

- **`Runner.fetch_window`** is now cache-aware. Reads cached bars from
  `mall["bars"]` first, only fetches the gap from the last cached
  timestamp to now. Drops API usage from `lookback_bars` per tick to
  `new_bars_since_last_tick` after the first warm-up.
- **`BarStore.write_bars(bars, *, timeframe=...)`** — fixed a latent bug
  (the old impl read `b.__dict__.get('timeframe')` which doesn't exist
  on `slots=True` frozen dataclasses). The method was dormant; now it
  has callers.
- **`AlpacaBroker.fills()`** uses a watermark-based polling strategy
  (`GetOrdersRequest(after=watermark)`) instead of the prior
  scan-last-200 every call. Steady-state polls become O(new fills).
  Both streamed and polled fills advance the watermark so the two
  paths play nicely together. First call on a fresh broker falls back
  to a 24h lookback.
- **`make_runner`** seeds the watermark from `mall["fills"]` on
  startup, so a process restart doesn't re-emit every fill from the
  prior 24h.
- **`Runner` now persists today's open NAV across ticks** (was a TODO
  in the previous version of the runner).
- **`tools.py` adds `hedger status`** — one-screen ops snapshot: NAV,
  positions, today's signals/decisions/fills, approx realised cash
  change, unfilled-order count.
- **Reflection orchestrator** now notifies on rolled-back sessions
  (timeout or pytest failure) via the new notifier seam.

### Added

- **`hedger.notify`** — new module:
  - `Notifier` Protocol; `LogNotifier` (default), `WebhookNotifier`
    (Slack/Discord-compatible), `TelegramNotifier`, `MultiNotifier`.
  - `make_notifier('log' | 'webhook[:URL]' | 'telegram' | 'multi:a,b')`.
  - `Config.notify` (`NotifyConfig`) with `kind` and
    `drawdown_alert_pct` (default 1%).
  - Wired into `Runner` to fire notifications on **risk vetoes**, **tax
    vetoes**, **once-per-day intraday drawdown** crossing the threshold.
- **Position reconciliation** (`Runner.reconcile`):
  - `mall["positions"]` JsonlStore slot.
  - `positions_to_snapshot()` JSONable rendering helper.
  - Snapshot at `make_runner` startup (baseline) and after every
    `tick()` (audit trail). Drift between consecutive snapshots is
    logged as `position_drift` warnings.
- **News pipeline for `llm_news`**:
  - `Runner.refresh_news(symbols)` — pulls from a configured
    `news_source` (e.g. `AlpacaNews`), persists into `mall["news"]`
    keyed by news id (idempotent), throttled by
    `news_refresh_minutes` (default 60).
  - `Runner.news_context(symbols, hours=24)` — builds the
    `{symbol_str: [headlines]}` mapping `llm_news` consumes.
  - `Runner.tick` passes `context = {"news": ..., "tick_ts": ...}` to
    the strategy with a back-compat fallback for context-naive
    strategies. `make_runner` best-effort instantiates `AlpacaNews`.
- **Daily brief** (`reflection.monitor.daily_brief`) is now actionable:
  per-symbol activity rollup (`signals`, `fills`, `buy_qty`,
  `sell_qty`, `notional_traded`), unfilled-order anomalies, latest
  positions snapshot + NAV, and recent news samples. Mall reads are
  fail-soft so test malls don't crash.
- **France PFU tax policy** (`FrenchPFUPolicy`, name `fr_pfu`):
  rate=0.30 (12.8% IR + 17.2% CSG/CRDS), FIFO lots per symbol,
  bookkeeping-only, exposes `tax_owed(symbol|None)` (positive gains
  only). Registered in `hedger.tax`.
- **Parameter sweep** (`hedger.backtest.sweep.param_sweep`): cartesian
  product over a parameter grid, each combo runs `backtest_simple`,
  results returned as a DataFrame sorted by Sharpe descending.
  Optional thread parallelism. `backtest_vectorbt` remains a stub but
  its `NotImplementedError` now points users at `param_sweep`.
- **Tests** added: `test_runner_cache` (3), `test_reconcile` (4),
  `test_alpaca_unit` (additions for watermark + monotonic seed),
  `test_notify` (10), `test_llm_news` (7 incl. live Anthropic round
  trip), `test_brief` (6), `test_tax_fr_pfu` (8), `test_sweep` (4),
  `test_reflect` (5 — full snapshot/spawn/validate/rollback flow with
  the claude subprocess mocked but git ops real).

### Verified

- 95 tests pass (was 39 at start of session).
- `hedger status` against the live paper account: NAV $100,001.87, two
  positions visible.
- `hedger tick` end-to-end: drift detected and logged when the journal
  diverges from broker truth (it did, between test runs); per-tick
  positions snapshot persisted; fills consumed via the watermark path.
- `param_sweep` on real Alpaca data (400 days, SPY+QQQ): best
  (fast=30, slow=50) gives Sharpe 1.64.
- Live `llm_news` against real AlpacaNews + Anthropic Haiku: 8 SPY
  headlines pulled, score 0.35 with rationale "Strong uptrend
  momentum...".

### Pick-ups for the next reflection cycle

- A vectorbt-backed implementation of `backtest_vectorbt` (currently
  delegated to `param_sweep` which is correct but slow).
- IBKR broker (still listed in v0.1 known limitations).
- Hedge against `AlpacaBroker.fills()` watermark slip when a fill
  arrives with `filled_at < poll_start` (extremely rare race).
- Alert on accumulated `position_drift` events (currently each is just
  a log line; a digest in the brief would be more actionable).

---

## 2026-05-05 — Alpaca-first refactor (per ALPACA.md)

### Changed

- **`AlpacaSource`** now falls back to `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` env vars when no keys are passed (was: silently disabled the stock client). Crypto and stock paths now both go through the canonical `alpaca.data.historical` import. Iterates `res.data[ticker]` defensively.
- **`AlpacaBroker`** now silently coerces `time_in_force=DAY` → `GTC` for crypto orders (Alpaca crypto rejects DAY); supports fractional dollar-notional orders via `Order.meta['notional']`; carries the asset class through fills/positions; uses `_alpaca_to_asset_class()` to translate Alpaca's class strings.
- **`AlpacaBroker.fills()`** now drains the streaming queue first (when `start_fill_stream()` was called) before falling back to polling closed orders.
- **`Runner.tick()`** uses a *per-tick* `client_order_id` (`run_id:tick_ts:symbol`) — fixes the bug where the same Runner instance produced identical client_order_ids for every tick of the same symbol, causing duplicate-rejection on every tick after the first. Also: fetch failures are now per-symbol fail-soft, equity orders are skipped when the Alpaca clock reports the market closed (crypto continues 24/7), and the tick summary includes `tick_ts` / `market_open` / `n_orders_submitted` / `n_skipped_market_closed`.
- **`make_runner()`** auto-starts the fill stream on `AlpacaBroker` (best-effort).
- **`check_requirements()`** default broker is now `alpaca` (was `paper`); performs a live `TradingClient.get_account()` round-trip when keys are present, surfaces auth failures with a fix-it message, and warns on non-ACTIVE account status.
- **`make_source()` / `make_broker()`** defaults switched to `alpaca` / `alpaca:paper` respectively. `BrokerConfig.name` default is now `"alpaca:paper"`. `tools.fetch` / `tools.backtest` default `--source` is `alpaca`. `tools.tick` accepts `--broker` and `--symbols` overrides for ad-hoc smoke tests.

### Added

- **`AlpacaNews`** — thin wrapper over `alpaca-py`'s `NewsClient`. Yields plain dicts (id, headline, summary, symbols, created_at, url, author) ready for the `news` mall slot. The `llm_news` strategy reads from `context['news']`; the runner can populate that from this stream.
- **`mall["news"]`** — new JsonlStore slot for normalised news headlines.
- **`AlpacaBroker.start_fill_stream()`** — spins up a background `TradingStream` thread that pushes Fills into a thread-safe queue; transparently consumed by the next `fills()` call. Best-effort: a stream failure does not break polling.
- **`AlpacaBroker.is_market_open()`** — convenience over `TradingClient.get_clock().is_open`. Used by the runner to gate equity orders (crypto bypasses).
- **Tests:** `tests/test_alpaca_unit.py` (mock-based, no API), `tests/test_alpaca_integration.py` (skipped when Alpaca env vars missing). 39 tests, all green.

### Verified end-to-end

- `hedger doctor` (Alpaca round-trip): clean.
- `hedger fetch SPY --source alpaca --days 5 --timeframe 1h`: 48 hourly bars.
- `hedger backtest --strategy sma_crossover --symbols SPY,QQQ --days 200 --source alpaca --timeframe 1d`: Sharpe 1.12, max DD -2.1%, 88 trades.
- `hedger tick` against `alpaca:paper` with config `max_position_weight=0.05`: order placed for QQQ, sized to ~5% NAV (~$5k), filled, position visible in the actual paper account.

### Pick-ups for future reflection cycles

- Per-tick fill streaming is wired but not stress-tested; add a reconnect loop and surface stream-died telemetry.
- `fills()` polling still scans the last 200 closed orders every call. Switch to `after=<watermark>` once we trust the streamed path.
- Position reconciliation against the broker on startup (still listed in the v0.1 known limitations) — high value, low risk.

---

## 2026-05-05 — v0.1.0 — Initial scaffold

### Added

- Core dataclasses and Protocols in `hedger/base.py` (Symbol, Bar, Signal, Decision, Order, Fill, Position; DataSource, Strategy, Sizer, Broker, TaxPolicy).
- Config layer (`hedger/config.py`) with TOML loading and env-var override for secrets.
- Mall pattern (`hedger/data/stores.py`) — JsonlStore + BarStore exposed through `mall()` factory.
- Three data sources (yfinance, Alpaca, CCXT) behind a `make_source()` factory.
- Strategy plugin registry with two shipped strategies: `sma_crossover` and `llm_news`.
- Two brokers: `PaperBroker` (with configurable fees and slippage) and `AlpacaBroker` (paper-default).
- Risk middleware composition: `cap_position_weight`, `cap_gross_exposure`, `block_when_loss_exceeds`.
- Two sizers: `equal_weight_sizer`, `kelly_capped_sizer`.
- Three tax policies: `none`, `us_wash_sale`, `crypto_lifo`.
- Backtest engine (`backtest_simple`) sharing the PaperBroker code path with live.
- Live runner with idempotent `tick()` and APScheduler-based scheduler.
- Reflection orchestrator: snapshot → brief → spawn `claude` subprocess → pytest gate → commit-or-rollback.
- CLI (`hedger doctor`, `list-strategies`, `fetch`, `backtest`, `tick`, `serve`, `reflect`, `brief`).
- `.claude/skills/` for the reflection cycle: `reflection-cycle`, `strategy-development`, `data-pipeline`.
- Documentation: `README.md`, `ARCHITECTURE.md`, `RESEARCH.md` (Vancouver-cited), `MANUAL_CHECKLIST.md`, `CLAUDE.md`.
- Tests: `test_base.py`, `test_backtest.py`, `test_risk.py`.

### Known limitations (pick-up candidates for the reflection cycle)

- `backtest_vectorbt()` is a stub.
- No position reconciliation against the broker on startup.
- No alerting/webhook integration (Telegram, Discord).
- No `requirements.lock` — pyproject deps are unpinned.
- IBKR broker not yet implemented.
- EU jurisdiction-specific tax policies not yet implemented.

### Sacred (reflection cycle must not weaken)

- The risk middleware default stack and its caps.
- The pytest-gate-and-rollback in `reflect()`.
- The ban on funds-withdrawal API paths.
- The `MANUAL_CHECKLIST.md` and `CLAUDE.md` content.
