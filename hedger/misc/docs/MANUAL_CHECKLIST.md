# Manual Checklist — Things Claude Code Can't Do For You

These are the items that require **you, in person, with a browser and a card**. Claude Code will not do them, and you should be wary of any tool that claims it will. Work through them roughly in order; the later items assume the earlier ones.

## Phase 1 — Accounts and Keys (do before first run)

- [ ] **Sign up for Alpaca**, paper account first. Go to [alpaca.markets](https://alpaca.markets), create an account, switch to "Paper" in the dashboard, generate API keys. Save them as environment variables on your dev machine and on your server: `ALPACA_API_KEY`, `ALPACA_SECRET_KEY`. The paper account is free, unlimited, and uses the same API as live — you only need to flip a flag later.
- [ ] **Get an Anthropic API key.** Go to [console.anthropic.com](https://console.anthropic.com), create an organisation, generate a key, set `ANTHROPIC_API_KEY` on dev and server. Set a monthly spend limit in the console (recommend $20 to start; the reflection cycle and LLM features will average well below that with the cost-discipline rules in `data-pipeline` skill).
- [ ] *(Optional, only if you want crypto)* **Sign up for Kraken or Coinbase**, generate API keys with **trading enabled** but **withdrawals disabled** (this is critical — the bot must never be able to move funds off-exchange). Save as `KRAKEN_API_KEY` / `KRAKEN_SECRET` etc.
- [ ] **Decide your jurisdiction.** Open `config.toml` and set `tax.policy`: `"none"` for paper trading (default), `"us_wash_sale"` if you'll trade live in the US, `"crypto_lifo"` for crypto, or roll your own.

## Phase 2 — Server Setup

- [ ] **Provision a small VPS.** Hetzner CX22 (~€5/mo) or DigitalOcean basic droplet (~$6/mo) is more than enough. Ubuntu 22.04 or 24.04 LTS. The bot is single-process and doesn't need a database server.
- [ ] **SSH key auth only**, disable password login, enable UFW firewall, install `unattended-upgrades`. Standard hardening.
- [ ] **Install Python 3.11+** and `pipx`. `apt install python3.11 python3.11-venv pipx`.
- [ ] **Install Claude Code** on the server. Follow [docs.claude.com/claude-code](https://docs.claude.com/claude-code) for the current install command. Authenticate it with your Anthropic account.
- [ ] **Clone the hedger repo** onto the server, `cd hedger`, `python -m venv .venv && source .venv/bin/activate && pip install -e .`.
- [ ] **Run `hedger doctor`.** If it lists missing pieces, fix them before going further.

## Phase 3 — First Backtest and Paper Run

- [ ] **Pick your starting universe.** Edit `config.toml` symbols list. Recommend starting with SPY, QQQ, IWM, plus 2–3 sector ETFs. Don't start with single names.
- [ ] **Run a backtest** on the SMA crossover strategy on at least 3 years of data: `hedger backtest --strategy sma_crossover --symbols SPY,QQQ --start 2023-01-01`. Check that the printed Sharpe and max-drawdown look sane. If Sharpe is unrealistically high (>3), suspect look-ahead bias in your modifications.
- [ ] **Start paper trading.** `hedger serve` will start the scheduler. Leave it running for **at least 2–4 weeks** before ever pointing it at a live broker.
- [ ] **Monitor the first night's reflection cycle by hand.** SSH in around 22:30 CET, check `.hedger/briefs/` for the brief, watch `git log` for new commits, verify pytest still passes after each. If anything looks wrong, `git reset --hard <pre-reflection-tag>` and investigate.

## Phase 4 — Going Live (only after Phase 3 has run cleanly)

- [ ] **Decide capital level.** Below $10k, restrict to fractional crypto or fractional ETFs (Alpaca supports fractional). Above $10k, full-share US stocks become viable.
- [ ] **Set hard risk limits** in `config.toml`. Defaults are conservative (10% per position, 100% gross, 2% daily loss); tighten if you want, never loosen for the first month live.
- [ ] **Flip Alpaca to live**. Generate live keys (separate from paper), set `ALPACA_LIVE=true` in your env, **restart the service**. Start with capital you would not lose sleep over losing entirely.
- [ ] **Talk to an accountant** about your jurisdiction's treatment of frequent trading, wash-sale-equivalents, and recordkeeping requirements. Bring printouts of `mall["fills"]` for the first month.

## Phase 5 — Operational Hygiene

- [ ] **Daily backups of `.hedger/`** to a separate machine or cloud (the parquet bar store can be regenerated, but the fills/decisions/reflections cannot).
- [ ] **Monitor and alerting.** Consider hooking up a Telegram or Discord webhook for: any rolled-back reflection cycle, any risk-middleware veto, any drawdown over 1% intraday. (Code for this is not in v0.1; plausibly Reflection Cycle #2's pick-up.)
- [ ] **Weekly review** of `CHANGELOG.md` — read what the reflection cycles did, push back (delete branches / revert commits) on anything you don't endorse.
- [ ] **Static IP / VPN** if your broker geofences (some EU regulators do, some brokers require a stable IP for API access).
- [ ] **Disaster drill once a month**: kill `hedger serve` mid-tick, verify it comes back up clean, position reconciles against the broker.

## What Claude Code is *not* allowed to do

For your safety the reflection cycle is forbidden from:

- Loosening any risk limit in `config.toml`.
- Adding any code path that can move funds out of the broker account.
- Calling any API with `ANTHROPIC_API_KEY` for purposes other than the LLM features used by registered strategies.
- Modifying `MANUAL_CHECKLIST.md`, `CLAUDE.md`, or any file under `.claude/skills/` except by appending notes that you must approve.

These constraints are written into `CLAUDE.md` at the project root. If you ever see them weakened in a diff, that's a bug to revert immediately.
