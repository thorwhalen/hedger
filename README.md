# hedger

A self-improving algorithmic trading bot. Runs on a single VPS, paper-trades by default, reflects on its own behaviour nightly.

```bash
pip install -e .
hedger install                                             # creates a 600-mode envfile
hedger where-keys                                          # prints the path + edit command
# ...edit the envfile, paste your ALPACA_* (and optionally ANTHROPIC_API_KEY)...
hedger doctor                                              # round-trips the Alpaca paper API
hedger backtest --strategy sma_crossover --symbols SPY,QQQ # uses Alpaca data by default
hedger tick                                                # one paper tick against alpaca:paper
hedger serve                                               # start the scheduler
```

## What it is

A layered, plugin-driven trading system that runs the **same code in backtest, paper, and live**. Strategies, data sources, brokers, sizers, tax policies, and notifiers are all plugin seams. Every artifact (bars, signals, decisions, orders, fills, positions, news, drifts) lives in a Mapping-shaped store on disk — no database to install, no external services to manage.

The nightly **reflection cycle** spawns Claude Code as a subprocess, hands it a daily brief of trading activity and the loaded skills, and lets it propose-and-test improvements between 22:00 and 06:00 local time. Every change is gated by pytest; failures roll back atomically via git.

## What it isn't

- An HFT system. The sweet-spot cadence is 1–4 hours; below 5 minutes the infrastructure cost climbs sharply for diminishing returns.
- A "press button, get rich" tool. Read [`MANUAL_CHECKLIST.md`](../MANUAL_CHECKLIST.md) first. Paper-trade for 2–4 weeks before any real capital.
- A black box. The mall on disk is your full audit trail. `cat .hedger/decisions.jsonl` shows you exactly what the bot decided and why.

## Read these in order

1. **[`MANUAL_CHECKLIST.md`](../MANUAL_CHECKLIST.md)** — the things only you can do (account signup, API keys, server provisioning).
2. **[`ARCHITECTURE.md`](../ARCHITECTURE.md)** — the data flow, the four plugin seams, and why the design choices.
3. **[`ALPACA.md`](../ALPACA.md)** — Alpaca API reference focused on this codebase.
4. **[`RESEARCH.md`](../RESEARCH.md)** — the survey of frameworks, brokers, LLM trading patterns, and tax considerations that shaped the scaffold (Vancouver-cited).
5. **[`CLAUDE.md`](CLAUDE.md)** — the house rules the reflection cycle is bound by.
6. **[`hedger/misc/CHANGELOG.md`](hedger/misc/CHANGELOG.md)** — what's shipped and what's known to be missing.

---

## Install

Requires Python 3.11+.

```bash
git clone <this-repo>
cd hedger
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

The install pulls in everything you need to run paper trading and the LLM-news strategy: `alpaca-py`, `anthropic`, `pandas`, `pyarrow`, `apscheduler`, `structlog`, `argh`, `vectorbt`, `ccxt`, `yfinance`. Heavy / optional groups:

```bash
pip install -e '.[dev]'         # pytest, ruff, mypy, ipython, jupyterlab
pip install -e '.[research]'    # matplotlib, plotly, seaborn, scikit-learn
pip install -e '.[nautilus]'    # the heavier event-driven backtester
```

---

## Configure

Two layers: **config.toml** for everything diffable; **a 600-mode envfile** for secrets. Secrets are *never* allowed in `config.toml` — `load_config` raises if it sees a `*_key` / `*_secret` / `*_token` / `*_password` / `anthropic_*` / `alpaca_*` key.

### Secrets (the easy way)

```bash
hedger install        # creates the envfile (mode 600), prints next steps
hedger where-keys     # tells you the path, which keys are set, and the edit command
```

`hedger install` writes:

- `/etc/hedger.env` when run as root, or
- `$XDG_CONFIG_HOME/hedger/hedger.env` (i.e. `~/.config/hedger/hedger.env`) otherwise.

Override with `--envfile /custom/path` if you want it elsewhere. The envfile is loaded automatically into `os.environ` whenever you invoke the `hedger` CLI (without overriding anything you `export`'d, so a systemd `EnvironmentFile=` always wins). To point hedger at a non-default path for one invocation: `HEDGER_ENVFILE=/path hedger tick`.

### Long-running (systemd)

```bash
hedger install --systemd       # also writes a unit file pointing at the envfile
# the command prints the exact systemctl daemon-reload / enable --now sequence
```

System scope (`/etc/systemd/system/hedger.service`) when run as root, user scope (`~/.config/systemd/user/hedger.service`) otherwise. The unit uses `EnvironmentFile=` so a `systemctl restart hedger.service` is enough to pick up rotated keys. The `hedger` binary path inside the unit is resolved at install time (`shutil.which`); nothing developer-machine-specific is baked in.

### Keys you'll need

| Variable | When |
|---|---|
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | always, for paper or live trading |
| `ANTHROPIC_API_KEY` | only for `llm_news` strategy or the nightly reflection cycle |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | only with `[notify].kind = "telegram"` |
| `HEDGER_WEBHOOK_URL` | only with `[notify].kind = "webhook"` |

### config.toml

Copy the example and edit:

```bash
cp config.example.toml config.toml
```

Defaults are picked so a fresh checkout with the env vars set works against the Alpaca paper account out of the box. The fields most worth knowing:

```toml
universe = ["SPY", "QQQ", "BTC/USD"]   # tickers; "BASE/QUOTE" => crypto
timeframe = "1h"                        # 1m | 5m | 15m | 1h | 1d
tax_policy = "none"                     # none | us_wash_sale | fr_pfu | crypto_lifo

[broker]
name = "alpaca:paper"                   # "paper" (in-memory) | "alpaca:paper" | "alpaca:live"

[data]
primary = "alpaca"                      # "alpaca" | "yfinance" | "ccxt:kraken"
timeframe = "1h"

[risk]
max_gross_exposure  = 1.0               # 100% of NAV
max_position_weight = 0.10              # no single position > 10% NAV
max_daily_loss      = 0.02              # circuit-breaker at -2% intraday

[notify]
kind = "log"                            # "log" | "webhook[:URL]" | "telegram" | "multi:a,b"
drawdown_alert_pct = 0.01               # notify on >= 1% intraday loss

[reflection]
enabled         = true
cron            = "0 22 * * *"          # 22:00 every day, in your TZ below
timezone        = "Europe/Paris"
max_minutes     = 480                   # 8h budget for Claude Code
claude_code_cmd = "claude"              # path/binary
```

`config.toml` is loaded from the working directory by default; override with `HEDGER_CONFIG=/path/to/file.toml` or `hedger <cmd> --config /path`.

---

## Verify

```bash
hedger doctor
```

Checks `ANTHROPIC_API_KEY`, `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` are set, **does a real round-trip to the Alpaca paper API to confirm authentication and account status**, and notes whether the `claude` CLI is installed (only needed for the nightly reflection cycle). Pass `--broker=paper` if you only need the in-memory broker for backtests.

---

## Operate

### CLI commands

| Command | What it does |
|---|---|
| `hedger doctor` | Verify env, round-trip Alpaca, report missing pieces. |
| `hedger install [--systemd]` | Create the secrets envfile (600) and optionally a systemd unit. Idempotent. |
| `hedger where-keys` | Show the envfile path, current mode, set/missing keys, and the edit command. |
| `hedger list-strategies` | List registered strategies. |
| `hedger fetch SPY --days 30` | Sanity-check a data source. |
| `hedger backtest --strategy sma_crossover --symbols SPY,QQQ --days 365` | One-shot backtest, prints summary stats. |
| `hedger tick` | Run one full live tick (default `alpaca:paper`). Use `--broker paper` to override; `--symbols A,B` to override universe. |
| `hedger serve` | Block forever; ticks on cadence + nightly reflection. |
| `hedger status` | One-screen ops snapshot: NAV, positions, today's signals/decisions/fills, unfilled orders. |
| `hedger brief` | Print today's reflection brief without launching the cycle. |
| `hedger reflect --dry-run` | Snapshot + write brief without spawning Claude Code. Drop `--dry-run` to launch a real cycle. |

### A normal day

```bash
hedger serve              # leave running in the foreground (or under systemd)
hedger status             # in another shell, anytime — quick health check
hedger brief              # what would the reflection cycle see?
```

The scheduler ticks at the cadence in `[data].timeframe` (default 60 min) during market hours, fires the reflection cycle nightly at the configured cron, and:

- **Risk middleware** clips every decision to the caps in `[risk]`. Vetoes are notified via the configured `[notify]`.
- **Tax policy** annotates / vetoes decisions per `tax_policy`. `none` is the default; `us_wash_sale`, `crypto_lifo`, `fr_pfu` ship.
- **Drawdown alert** fires once per day when intraday loss crosses `[notify].drawdown_alert_pct` (separate from, and earlier than, the harder `[risk].max_daily_loss` circuit-breaker that vetoes new orders).
- **Position reconciliation** snapshots the broker's view to `mall["positions"]` at startup and after every tick; differences vs the prior snapshot are logged as `position_drift` and persisted to `mall["drifts"]` for the brief.
- **Fill streaming** subscribes to Alpaca's `TradingStream` for low-latency fill notifications, with auto-reconnect on disconnect (exponential backoff). Polling via `GetOrdersRequest(after=watermark)` is the fallback.

### Where state lives

Everything persistent goes under `.hedger/` in the working directory (configurable via `[data].cache_dir`):

```
.hedger/
  bars/             ← parquet, partitioned by (symbol, timeframe)
  signals.jsonl     ← every Signal a strategy emitted
  decisions.jsonl   ← every Decision (post-sizing, pre-execution)
  orders.jsonl      ← every Order submitted
  fills.jsonl       ← every Fill the broker confirmed
  news.jsonl        ← normalised news items (id, headline, symbols, ...)
  positions.jsonl   ← positions snapshot per startup + per tick
  drifts.jsonl      ← reconciliation drift events
  reflections.jsonl ← per-session reflection-cycle records
  briefs/           ← daily reflection briefs (json)
```

Inspectable on disk: `head -1 .hedger/decisions.jsonl | jq .value`. Mockable in tests: `mall = {"bars": {}, "decisions": {}, ...}` is a valid test mall.

### Notifications

The `[notify].kind` setting controls where alerts go:

```toml
[notify]
kind = "log"                                    # default — structured log lines only
# kind = "webhook"                              # POST {"text": ...} JSON to HEDGER_WEBHOOK_URL
# kind = "webhook:https://hooks.slack.com/..."  # explicit URL
# kind = "telegram"                             # uses TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
# kind = "multi:log,webhook"                    # fan-out
```

What gets notified: risk vetoes, tax-policy vetoes, intraday drawdown crossing the threshold (once per day), Alpaca fill-stream lifecycle (started / died / reconnecting), and reflection-cycle rollbacks.

### Backtests and parameter sweeps

```bash
hedger backtest --strategy sma_crossover --symbols SPY,QQQ --days 365
```

Or, for parameter sweeps, use the Python API:

```python
from hedger.backtest import param_sweep
from hedger.base import AssetClass, Symbol
from hedger.data.sources import make_source
from hedger.strategies.sma_crossover import sma_crossover
from datetime import datetime, timedelta, timezone

src = make_source("alpaca")
end = datetime.now(timezone.utc) - timedelta(minutes=20)
start = end - timedelta(days=400)
bars = {Symbol(t, AssetClass.EQUITY): list(src.bars(
    Symbol(t, AssetClass.EQUITY), start=start, end=end, timeframe="1d"
)) for t in ("SPY", "QQQ")}

df = param_sweep(sma_crossover,
                 {"fast": [5, 10, 20, 30], "slow": [50, 100, 150]},
                 bars, max_workers=4)
print(df.head())   # sorted by sharpe desc
```

---

## Going live

Read [`MANUAL_CHECKLIST.md`](../MANUAL_CHECKLIST.md) Phase 4 first. The short version:

1. Paper-trade for **2–4 weeks** with `hedger serve` and check `hedger status` daily.
2. Confirm the nightly reflection cycle runs cleanly (briefs make sense; no rollbacks for spurious reasons).
3. Generate a *separate* live API key from the Alpaca dashboard. Put it in the envfile on the live host (`hedger where-keys` to find / edit it).
4. Flip `config.toml` `[broker].name = "alpaca:live"`. Restart the service.
5. Tighten `[risk].max_position_weight` for the first month (e.g. 0.05).

---

## Plug-in seams

Add a new strategy / broker / data source / sizer / tax policy / notifier without touching the runner. See [`ARCHITECTURE.md`](../ARCHITECTURE.md) for the patterns and [`.claude/skills/`](hedger/.claude/skills/) for the conventions.

```
hedger/base.py            ← dataclasses + Protocols (the trading vocabulary, SSOT)
hedger/config.py          ← TOML + env-var config
hedger/data/              ← sources (Alpaca, yfinance, CCXT, AlpacaNews) + stores (the mall)
hedger/strategies/        ← plugin-registered; sma_crossover and llm_news ship
hedger/execution/         ← brokers, risk middleware, sizers
hedger/tax/               ← TaxPolicy plugins (none / us_wash_sale / fr_pfu / crypto_lifo)
hedger/backtest/          ← engine + param_sweep, sharing the live code path
hedger/live/              ← runner.tick() + APScheduler
hedger/reflection/        ← daily brief + Claude Code subprocess orchestrator
hedger/notify.py          ← Notifier protocol + log/webhook/telegram impls
hedger/.claude/skills/    ← skills loaded by the nightly reflection cycle
```

## Tests

```bash
pytest -q                                      # 104 tests, ~6 s
pytest tests/test_alpaca_integration.py -v     # only runs when ALPACA env vars set
pytest tests/test_llm_news.py -v               # includes a live Anthropic round-trip
```

## License

MIT.
