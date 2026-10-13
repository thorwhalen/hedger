# Questions for the owner

Written by reflection cycles when something was unclear and the safe choice was to leave the system unchanged (see `.claude/skills/reflection-cycle/SKILL.md`). Newest first. Delete a question once answered, and record the answer in `CHANGELOG.md` or `ARCHITECTURE.md`.

## 2026-10-11

1. **Why does the reflection session start without `HOME`?** The Claude Code process spawned by `reflect()` had no `HOME` in its environment: a hook configured as `$HOME/.claude/hooks/...` resolved to `/.claude/...`, which disabled every Bash call for the whole session (and with it pytest, the backtest and git). The likely cause is `hedger serve` running as a system `systemd` unit without `User=` or `Environment=HOME=...`, so the child inherits an environment without `HOME`; this could not be verified without a shell. Please confirm how the service is launched. Proposed fix (about ten lines plus one test): in `hedger/reflection/orchestrator.py`, pass `env={**os.environ, "HOME": os.environ.get("HOME") or str(Path.home())}` to the Claude Code `subprocess.run`, and add `Environment=HOME=%h` to `_systemd_unit_text()` in `hedger/install.py`. Not applied this cycle because of the one-change rule and the unverified diagnosis.

2. **BTC sell-down on restart: automatic or by hand?** With the `BTCUSD → BTC/USD` fix, the first tick after a restart will sell about 0.98 BTC on the paper account to return BTC to the 10% cap. If you prefer to do that manually, or to allow a larger crypto weight, do it before restarting `hedger serve`.

3. **Gross-exposure cap.** `Runner.tick()` wires `cap_gross_exposure` with `other_targets=lambda s: {}`, so it never vetoes. Is enforcing it live wanted? It would require threading current positions into the middleware, which touches the risk chain and so needs your approval.

4. **Rebalance band.** Hourly $50–$400 SPY/QQQ orders are pure churn under the current dust threshold (0.05% of NAV). Is a `rebalance_band` keyword on the runner (default around 1% of NAV) acceptable?
