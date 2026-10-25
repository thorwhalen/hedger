# Questions for the owner

Written by reflection cycles when something was unclear and the safe choice was to leave the system unchanged (see `.claude/skills/reflection-cycle/SKILL.md`). Newest first. Delete a question once answered, and record the answer in `CHANGELOG.md` or `ARCHITECTURE.md`.

## 2026-10-21

6. **Please restart `hedger serve`** (`systemctl restart hedger`) on the current working tree, and if you can, run `pytest -q` and commit the staged/modified files (orchestrator, `tests/test_reflect.py`, CHANGELOG, this file) yourself first. The 2026-10-18 and 2026-10-21 orchestrator fixes give the reflection session and its git calls a `HOME`, but the running process still has the old code: three cycles in a row have had no shell, and no snapshot commit has ever been made, so a failed gate resets the tree to the last human commit (it did on 2026-10-14). Until it restarts, every reflection runs blind and unprotected. If you would rather pause reflections until then, set `reflection.enabled = false`.

## 2026-10-18

5. **Should a flat signal close the position?** `sma_crossover` is documented as "long when fast SMA > slow SMA, flat when not", but a score of 0.0 is dropped by `equal_weight_sizer` (it only sizes non-zero scores), so no Decision and no order are produced and the position is simply kept. Live effect right now: SPY and QQQ have scored 0.0 since the 2026-10-16T23:00 bar and the paper account still holds about $10k of each; BTC behaves the same way. `backtest_simple` has the identical gap, so every backtest number so far describes a bot that only ever buys and rebalances to 10%, never sells out. Options: (a) a zero score produces `target_weight = 0` (true "flat": the runner would then sell the whole position on the next tick after a down-cross, three positions at once on a broad sell-off); (b) keep today's behaviour and reword the strategy docstring to "buy-and-rebalance when fast > slow"; (c) make it a sizer keyword (`flat_closes: bool`) defaulting to today's behaviour and let a reflection cycle backtest both. The reflection cycle proposes (c) and will not change trading behaviour until you choose. Related: with (a), a reflection session needs the `rebalance_band` of Question 4 to avoid flip-flopping near the cross.

## 2026-10-11

Questions 1 (reflection session without `HOME`) and 2 (BTC sell-down on restart) were resolved on 2026-10-18: the orchestrator now passes an explicit env with `HOME`, and the sell-down happened on 2026-10-16. See `CHANGELOG.md`.

3. **Gross-exposure cap.** `Runner.tick()` wires `cap_gross_exposure` with `other_targets=lambda s: {}`, so it never vetoes. Is enforcing it live wanted? It would require threading current positions into the middleware, which touches the risk chain and so needs your approval.

4. **Rebalance band.** Hourly $50–$400 SPY/QQQ orders are pure churn under the current dust threshold (0.05% of NAV). Is a `rebalance_band` keyword on the runner (default around 1% of NAV) acceptable?
