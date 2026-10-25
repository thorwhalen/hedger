# Changelog

All notable changes to `hedger` are recorded here. Reflection-cycle commits append entries automatically; human commits should follow the same pattern.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/), but optimised for the reflection cycle to read and write.

---

## 2026-10-21 — Snapshot commits get a `HOME` too, and a failed snapshot stops the cycle instead of arming a destructive rollback

**Scope:** reflection-infrastructure fix (orchestrator only). No strategy, sizer, broker or risk-middleware change; the trading path is untouched.

**Changed:** `hedger/reflection/orchestrator.py` (`_git` passes `env=_child_env()`; `snapshot()` no longer swallows `CalledProcessError`; `reflect()` returns `status="snapshot_failed"` with an `error` notification and never spawns Claude Code when the snapshot commit fails), `tests/test_reflect.py` (two tests: every snapshot git call carries `HOME` and the snapshot commit lands with `HOME` removed from the parent; a failing commit yields `snapshot_failed`, no claude spawn, an `error` notification and no `reflect-*` tag). About 25 lines of code and 50 of tests.

**Why:** The git reflog shows that `hedger serve` has **never** made a snapshot commit. Every orchestrator git operation runs with the service's env, which has no `HOME`, so git cannot read the committer identity in `~/.gitconfig` (the repo has none of its own) and `git commit` fails. `snapshot()` caught that as "nothing to commit" (impossible with `--allow-empty`) and tagged the *previous* commit. The rollback tag therefore never covered the working tree, and a failed gate ran `git reset --hard` to the last human commit, discarding every uncommitted change in tracked files, including earlier sessions' work that had passed its own gate. This has already happened once: the reflog's last entry is `reset: moving to reflect-20261014T2200` at 2026-10-14 22:06:59 UTC by `root <root@thorwhalen.(none)>` (the identity git falls back to when it can't find one), and that cycle left no CHANGELOG entry and no `reflections.jsonl` record (only passing sessions are recorded). Right now the 2026-10-18 `HOME` fix is still only *staged* (the `git add -A` of tonight's snapshot ran; its commit did not), and `reflect-20261021T2200` points at `ae14386`, so if tonight's gate fails it takes the 10-18 fix with it. The 10-18 fix covered only the Claude Code spawn, not `_git`, so a restart alone would not have closed this. Refusing to run when the snapshot fails is the safe direction: a reflection that cannot be rolled back cleanly should not run.

**Metrics (90-day backtest, same universe):** not applicable and not run. There was no shell again this session (see the session note), and the change does not touch strategy, sizing or execution, so backtest results are identical by construction. Validation is the orchestrator's `pytest -q` gate.

**Learned today (from the brief):**
- All 53 signals scored 1.0 and produced 53 decisions: SPY and QQQ recrossed upward after the 2026-10-16 down-cross, and BTC stayed up. All three positions are at about 10.0% of the $103,207 NAV (SPY 13.36 at $772, QQQ 14.18 at $728, BTC 0.1222 at $84,346). No vetoes are visible and nothing was lost: `approx_realized_cash_change = +342` is net cash from rebalancing sells (BTC rose from about $82.6k to $84.3k).
- **Every `position_drift` in the brief is the bot's own fill**, matching to the last digit (BTC 0.12577928 → 0.12404754 is the 22:22 sell of 0.00173174). `Runner.reconcile()` compares broker positions with the *previous tick's* snapshot and never subtracts the tick's own fills (drained at `runner.py:451`, never passed to `reconcile`). The drift journal is therefore indistinguishable from fill history, and a genuine external change (a manual trade, a corporate action, the 2026-05 BTC symbol bug) would be buried in it. That 2026-05 BTC bug was only diagnosed from `drifts.jsonl` by reasoning about it by hand.
- Churn continues: eight fills, $61 to $170 each, all rebalances back to 10% (Question 4).
- `unfilled_orders` still lists May to July SPY buys (TODO 4c from 2026-10-11), and `counts.orders = 240` is all-time.

**Watch tomorrow:**
- **This fix and the 10-18 one only take effect after `hedger serve` restarts** (`systemctl restart hedger`, owner action). The next cycle's reflog should then show a `commit: snapshot before reflection …` entry by Thor Whalen before the session, and that session should have a working shell. If it shows `snapshot_failed` in `journalctl -u hedger` instead, the identity is still unreachable: check that `Path.home()` for the service user is `/root`.
- Snapshot commits will now land on local `master` ahead of `origin/master`. That is the original design, but nothing pushes them: land them with a PR (or squash) during the next shell-enabled session.
- If tonight's gate failed, the 10-18 and 10-21 changes are gone from the tree but their blobs are still in `.git/objects` (they were staged): `git fsck --lost-found` recovers them.

**TODO (next cycles, in priority order):**
1. **Restart `hedger serve`** on the current tree (owner; see QUESTIONS.md #6). Then a shell-enabled session should run `pytest -q`, commit the 10-18 and 10-21 changes properly and open a PR.
2. **Drift that ignores own fills** (observation, no behaviour change): pass the tick's drained fills (or `mall["fills"]` since the previous snapshot's `ts`) into `reconcile()` and add their signed qty to `prev_qty` before the epsilon check, so `drifts` holds only unexplained changes. Note that market fills can land after the tick's reconcile, so the "since prev snapshot ts" form is the robust one. One file plus a test in `tests/test_reconcile.py`.
3. Record rolled-back and skipped sessions in `mall["reflections"]` too (status, tag, cost), so a 10-14-style rollback is visible to the next session.
4. Carried over from 2026-10-18: exit-on-flat (Question 5), gross-exposure cap (Question 3), rebalance band (Question 4), brief observability (windowed `counts.orders`, journalled `submit_failed`, `avg_price < 0` flag, stale `unfilled_orders`), `Environment=HOME=%h` in the systemd unit template.

**Session note:** no shell for the third cycle (`HOME` unset; the service was not restarted after 2026-10-18, so `_child_env()` is not in the running process). File-level work only. Nothing was committed by this session: the orchestrator's pytest gate decides, and neither tonight's nor any earlier snapshot commit exists (see "Why").

---

## 2026-10-20 — Crypto follow-ups: unknown-quote warning, open orders counted, BTC sell-down test

**Scope:** execution adapter and runner; no risk-middleware or strategy change.

**Changed:** `hedger/execution/brokers.py` (warn on a crypto ticker whose quote currency is unknown; `open_orders()` on `AlpacaBroker` and `PaperBroker`), `hedger/live/runner.py` (`Runner.pending_qty()`; working orders count as already held when sizing), `tests/test_alpaca_unit.py`, `tests/test_runner_orders.py`.

**Why:** follow-ups from the review of the BTCUSD fix (issue 13). An unnormalised quote like `BTCEUR` would silently bring the symbol mismatch back; an unfilled sell was recomputed on every tick; and no runner-level test covered selling a held BTC position down to the per-asset cap.

---

## 2026-10-18 — Reflection sessions get a `HOME`: the orchestrator now spawns Claude Code with an explicit env

**Scope:** reflection-infrastructure fix (orchestrator only). No strategy, sizer, broker or risk-middleware change; the trading path is untouched.

**Changed:** `hedger/reflection/orchestrator.py` (new `_child_env()` and `_account_home()`; `reflect()` passes `env=_child_env()` to the Claude Code `subprocess.run`), `tests/test_reflect.py` (two tests: `_child_env` fills a missing or empty `HOME` from the passwd entry and never overrides one that is set; `reflect()` spawns claude with an explicit env that carries `HOME` and the parent's variables). About 45 lines of code plus one doctest.

**Why:** For the second cycle in a row this session started with `HOME` unset. The owner's `PreToolUse`/`PostToolUse` hooks are addressed as `$HOME/.claude/hooks/...`, which resolves to `/.claude/...`, so Claude Code refuses every Bash call and the session cannot run `pytest`, the backtest or `git`. `reflect()` called `subprocess.run(cmd, cwd=repo, ...)` with no `env`, so the child inherited the `hedger serve` environment, and a system `systemd` unit without `User=` has no `HOME` (systemd sets `HOME` only for units that set `User=`). The 2026-10-11 record in `reflections.jsonl` (returncode 0, validation passed, cost $8.06 against the $5 cap) shows the pytest gate itself works, so the fix belongs in the environment handed to the subprocess: `_child_env()` copies the parent env and fills `HOME` from `Path.home()` (the passwd entry when `HOME` is unset) only when it is missing or empty; secrets from `EnvironmentFile=` pass through unchanged.

**Metrics (90-day backtest, same universe):** not applicable and not run (no shell this session; the change does not touch strategy, sizing or execution, so backtest results are identical by construction). Validation is the orchestrator's `pytest -q` gate after this session; the new tests and the doctest live in files that roll back together with the code if the gate fails.

**Learned today (from the brief and the journals; the logs go to journald and were not readable):**
- The BTC fix from 2026-10-11 is live. The sell-down happened on 2026-10-16 (0.99507 BTC at $82,094 and 0.00118 BTC at $82,705); `latest_positions` shows `default:BTC/USD` at 0.1268 BTC, about 10.0% of the $102,401 NAV, and today's three fills (sell $79, buy $79, buy $70, zero paper fees) are hourly rebalances holding BTC at the 10% cap as the price moves. `approx_realized_cash_change = -70` is the net cash spent on those buys, not a loss.
- **The strategy never exits a position.** `sma_crossover` scores 0.0 when the fast SMA is below the slow one ("flat"), `equal_weight_sizer` drops zero-score signals, so there is no Decision, no order, and the position is kept. `signals.jsonl` shows SPY and QQQ at score 0.0 since the 2026-10-16T23:00 bar (fast 768.2 < slow 770.2; 719.1 < 725.9), yet the account still holds about $10k of each. Today's 23 signals against 11 decisions is the same mechanism for BTC: score 0.0 from 22:00 to 10:00 UTC, then 1.0 from the 11:00 bar (fast 81,626 > slow 81,531). `backtest_simple` has the identical gap (same sizer, same no-decision-on-zero), so backtest and paper agree with each other and both disagree with the strategy's docstring. Whether a flat signal should close the position is a trading-behaviour change and is now Question 5 in `hedger/misc/docs/QUESTIONS.md`.
- The negative `avg_price` on BTC/USD (-637,703) is Alpaca paper's own cost basis, passed through by `AlpacaBroker.positions()` from `avg_entry_price`. The old `BTCUSD` position carried `avg_price 0.0`; after the 2026-10-16 sell Alpaca recomputed the basis as (0 - proceeds) / remaining qty = -648,258, and the two buys since averaged it up to -637,703. Cosmetic for hedger today (nothing on the live path reads `avg_price`; `reconcile()` compares quantities only) but any per-position P&L report would be wrong. A guard in the brief (`avg_price < 0` flagged) is the right fix, not a broker-side correction.
- Weekend artefact, as predicted on 2026-10-11: 2026-10-18 is a Sunday, every SPY/QQQ signal carries Friday's last bar timestamp and falls outside the 24h window, hence 0 SPY/QQQ signals in the brief. Re-writing that same key on every hourly tick also appends a duplicate line to `signals.jsonl` each time (append-only store; only the in-memory cache dedups), which is why the file is 3.6 MB for a few thousand distinct signals.
- `scrap/_reflect_2026-10-11/` is gone (TODO 5 from 2026-10-11 is done).
- `hedger/util.py` logs to stdout/stderr only, so `submit_failed`, vetoes and `claude_code_done` are in `journalctl -u hedger` and invisible to a reflection session; the brief is the only in-band source.

**Watch tomorrow:**
- Tomorrow's reflection session should have a working shell. If Bash is still refused, either the service was not restarted on this code or the gate rolled it back: check `reflections.jsonl` and `journalctl -u hedger` for `validation_failed_rolling_back`.
- BTC's fast SMA was $1.50 above the slow one at the 21:00 bar (81,443.7 vs 81,442.2). If it crosses down, expect BTC signals with score 0.0, zero decisions, zero fills and an unchanged 0.1268 BTC position. That is the no-exit behaviour above, not a stall.
- SPY/QQQ on Monday: with score 0.0 there will be no orders at all until the fast SMA recrosses; the brief will show signals keyed by Monday's bars but no decisions.

**TODO (next cycles, in priority order):**
1. **Exit on flat** (after the owner answers Question 5): let a zero-score signal produce a `target_weight = 0` Decision, as a keyword switch on the sizer defaulting to the current behaviour, then backtest both variants on the 90-day window for SPY, QQQ and BTC/USD and compare Sharpe and max drawdown.
2. Gross-exposure cap is a no-op live (unchanged since 2026-10-11, Question 3).
3. Rebalance band on the runner (Question 4); today's three BTC fills were $70 to $79 each.
4. Brief observability: a windowed `counts.orders`, a journal entry for `submit_failed`, an `avg_price < 0` flag, and skipping the `signals.jsonl` append when the key already holds the same value.
5. Optional hardening: `Environment=HOME=%h` (or `User=`) in `_systemd_unit_text()` in `hedger/install.py`, so the service itself has a `HOME` for anything else that wants one.

**Session note:** no shell again (`HOME` unset, see "Why"); file-level work only, via Read/Edit/Write. Nothing was committed by this session: the orchestrator's pytest gate decides whether the working-tree change survives, and the next snapshot commit (or a PR) picks it up. Questions 1 and 2 of 2026-10-11 were removed from `QUESTIONS.md` as answered by evidence (this fix) and by events (the sell-down).

---

## 2026-10-11 — Alpaca crypto positions now use the universe symbol form (BTCUSD → BTC/USD)

**Scope:** bug fix in the Alpaca broker adapter (execution layer). No strategy, sizer or risk-middleware change.

**Changed:** `hedger/execution/brokers.py` (new `normalize_crypto_ticker` and `_symbol_from_alpaca`, used by `AlpacaBroker.positions()`, `fills()` and the fill stream), `tests/test_alpaca_unit.py` (three regression tests).

**Why:** Today's brief showed a 1.12 BTC position journalled as `default:BTCUSD` while every decision targets `default:BTC/USD` at 10% weight, with 23 decisions and 0 fills. Alpaca returns crypto *positions* in concatenated form (`BTCUSD`) but *orders and fills* in pair form (`BTC/USD`), as `fills.jsonl` and `drifts.jsonl` confirm. The runner's `positions.get(d.symbol)` therefore never found the BTC holding, treated it as zero, and submitted a fresh 10%-of-NAV buy on every hourly tick. `drifts.jsonl` shows exactly that on 2026-05-09: BTC grew by ~0.1246 BTC per hour from 08:34 to 15:34 UTC until buying power ran out, plus one more buy on 2026-05-20 after an equity sell freed cash. The paper account has held roughly 80% of NAV in BTC since then, so the `max_position_weight = 0.10` cap was effectively bypassed for crypto, and every hourly BTC buy since has been rejected by Alpaca (a stream of `submit_failed` warnings that the brief cannot see). With the fix the broker reports `Symbol("BTC/USD", CRYPTO)` and the existing sizing arithmetic produces the correct sell-down to 10%.

**Metrics (90-day backtest, same universe):** not run. This session had no command execution (see the session note below), and the change does not touch the backtest path (`backtest_simple` drives a `PaperBroker`), so before/after backtest metrics are identical by construction. Validation relies on the orchestrator's `pytest -q` gate.

**Watch tomorrow:**
- The fix takes effect when `hedger serve` restarts. On the first tick after that, expect one market **sell of about 0.98 BTC** (from ~1.12 to ~0.14 BTC, i.e. 10% of NAV) and a one-time pair of `position_drift` records (`default:BTCUSD` → 0 and `default:BTC/USD` → 1.12). If you would rather sell down by hand first, do it before restarting.
- The brief's `latest_positions` should then show `default:BTC/USD`, `fills` should be non-zero on the restart day, and the hourly BTC `submit_failed` warnings should stop.

**Session note:** the Claude Code process for this cycle was started without `HOME` in its environment, so the `PreToolUse` Bash hook configured as `$HOME/.claude/hooks/...` could not be found and Claude Code refused every Bash call; the Monitor tool needed an interactive approval. The session could not run pytest, the backtest, or git. The code change was kept minimal, with a doctest, and the tests were added to an existing test file so a gate rollback reverts code and tests together. Nothing was committed by this session; the orchestrator's pytest gate decides whether the working-tree change survives, and the next snapshot commit picks it up.

**TODO (next cycles, in priority order):**
1. **Make reflection sessions runnable again.** Ensure `HOME` (and `SHELL`) are present in the environment `reflect()` hands to the Claude Code subprocess, e.g. `env = {**os.environ}; env.setdefault("HOME", str(Path.home()))`, and consider `Environment=HOME=%h` in the systemd unit template. Needs a test in `tests/test_reflect.py` asserting the `env` kwarg. Open question in `hedger/misc/docs/QUESTIONS.md`.
2. **Gross-exposure cap is a no-op live.** `Runner.tick()` builds `default_risk_middleware` with the default `other_targets=lambda s: {}`, so `cap_gross_exposure` always sees gross = 0 and never vetoes. Wire current positions/targets in; needs owner approval since it touches the risk chain, and tests.
3. **Hourly rebalance churn on SPY/QQQ.** The dust threshold is `max($1, 0.05% of NAV)` ≈ $52, so the bot trades $50–$400 of SPY/QQQ almost every market hour (see `orders.jsonl`). Add a rebalance band as a keyword arg on the runner (e.g. skip when |target − current| < 1% of NAV).
4. **Brief observability.** (a) Signals and decisions are keyed by *bar* timestamp, so on weekends every SPY/QQQ record collapses onto Friday's last bar and falls outside the 24h window; today's zero SPY/QQQ signals are that artefact, not a silent strategy. (b) `counts.orders` is all-time (216), not windowed. (c) `unfilled_orders` matches on (symbol, side) only and lists May orders. (d) `submit_failed` is only logged, never journalled, so rejected orders are invisible to the brief. Fix in `hedger/reflection/monitor.py` and `Runner.tick()`.
5. Delete `scrap/_reflect_2026-10-11/` (scratch scripts this session could neither execute nor remove; untracked and harmless).

---

## 2026-06-06 — CLI guard: warn when ambient env shadows the envfile

**Why:** `load_envfile_into_environ()` is `override=False`, so an `ALPACA_API_KEY`
exported in the shell silently wins over `/etc/hedger.env`. In practice this made
`hedger status` report a *different* Alpaca paper account than the one `hedger serve`
actually trades (the shell's `ALPACA_*` pointed at a second account on the same box).

**Change:** added `install.warn_if_ambient_shadows_envfile()`, called from
`__main__.main()` before the envfile load. It prints a stderr warning (with last-4
fingerprints only — no secret leak) listing the mismatched keys and the
`env -u ... hedger <cmd>` fix. No behaviour change to the loader itself. Tests added
in `tests/test_install.py`; `pytest -q` green.

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
- `pandas-ta` was dropped from the core dependencies on 2026-05-07: it is
  unused in the codebase (indicators are computed inline) and pinned to
  Python ≥ 3.12, which broke CI on 3.11. If indicator helpers are needed
  later, add `pandas-ta-classic` or TA-Lib as a deliberate change.
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
