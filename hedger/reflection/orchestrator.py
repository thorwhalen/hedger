"""Overnight reflection orchestrator.

This is the agentic part. At the configured cron time (default 22:00 CET)
this module:

  1. Snapshots the bot (git commit + tag, freeze the state).
  2. Writes a daily brief from the mall (see `monitor.py`).
  3. Spawns Claude Code in headless mode with:
        - working directory = the bot repo
        - read access to .claude/skills/ (loaded automatically)
        - read access to the brief and logs
        - instructions in CLAUDE.md
  4. Waits up to `cfg.reflection.max_minutes` for it to finish.
  5. Inspects the diff Claude Code produced:
        - runs the test suite
        - runs `hedger backtest` on the changed strategy(ies)
        - if metrics improved (or at least didn't regress past a guardrail),
          merges to `main` and restarts the live runner;
        - otherwise rolls back to the snapshot tag.
  6. Appends a markdown record to `hedger/misc/CHANGELOG.md` and a structured
     entry to `mall['reflections']` so the next session sees its history.

Why subprocess Claude Code instead of calling the Anthropic API directly?
Because Claude Code already does the read/edit/run/commit loop with proper
sandboxing, MCP integration, and tool use. We give it the prompt and
guardrails; it does the agentic work.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from hedger.config import Config, load_config
from hedger.data.stores import mall as default_mall
from hedger.notify import LogNotifier, Notifier, make_notifier
from hedger.reflection.monitor import write_brief
from hedger.util import get_logger

log = get_logger("hedger.reflection")


REFLECT_PROMPT = textwrap.dedent("""
    You are running the overnight reflection cycle for the hedger trading bot.

    Inputs you can read:
      - {brief_path}         : last 24h activity brief (counts, samples, P&L)
      - logs/                 : structured logs from the day
      - hedger/misc/CHANGELOG.md: prior reflection cycles

    Skills available (autoloaded by Claude Code from .claude/skills):
      - reflection-cycle: how to plan a session, pick scope, gate changes
      - strategy-development: how to add or modify a strategy plug-in safely
      - data-pipeline: how to extend data sources and stores

    Goals, in priority order:
      1. SAFETY. Never produce code that bypasses risk middleware.
                 Never reduce test coverage. Never disable a circuit-breaker.
                 Never trade on live keys (this session is sandbox-only).
      2. LEARN.  Investigate today's surprises (look for losing trades, signals
                 that didn't fire, drift between backtest and paper).
      3. IMPROVE. Pick ONE small change (≤ ~150 lines, one file or two), test
                 it (`pytest -q`), backtest it on the same window the bot used
                 today, and commit if metrics improve and tests pass.
      4. RECORD. Append a dated entry to hedger/misc/CHANGELOG.md with what you
                 changed, why, the metric delta, and what to watch tomorrow.
                 Append a structured entry to .hedger/reflections/ (json).

    Hard constraints:
      - Do not modify pyproject.toml's required dependencies.
      - Do not change the public surface of hedger.base (Bar, Signal, etc.).
      - Do not modify hedger/execution/risk.py without an accompanying test.
      - Time budget: {max_minutes} minutes. If you can't finish, leave a TODO
        in CHANGELOG and exit cleanly — partial work is fine.

    When you are done, print a one-paragraph summary and exit 0 if changes
    were committed, exit 1 if no changes (this is also a valid outcome).
""").strip()


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True)


def snapshot(repo: Path, label: str) -> str:
    """Tag the current state so we can roll back. Returns the tag name."""
    try:
        _git(["add", "-A"], repo)
        _git(["commit", "-m", f"snapshot before reflection {label}",
              "--allow-empty"], repo)
    except subprocess.CalledProcessError:
        pass  # nothing to commit
    tag = f"reflect-{label}"
    _git(["tag", "-f", tag], repo)
    return tag


def rollback(repo: Path, tag: str) -> None:
    _git(["reset", "--hard", tag], repo)


def _validation_passed(repo: Path) -> bool:
    """Run pytest and reject if anything failed."""
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"], cwd=repo, capture_output=True, text=True,
    )
    log.info("post_reflect_pytest", returncode=p.returncode,
             tail=p.stdout[-2000:] if p.stdout else "")
    return p.returncode == 0


def reflect(
    *,
    repo: Path | None = None,
    cfg: Config | None = None,
    mall: Mapping | None = None,
    notifier: Notifier | None = None,
    dry_run: bool = False,
) -> dict:
    """Run one overnight reflection cycle. Returns a result dict."""
    cfg = cfg or load_config()
    repo = repo or Path.cwd()
    mall = mall or default_mall()
    if notifier is None:
        try:
            notifier = make_notifier(cfg.notify.kind)
        except Exception:
            notifier = LogNotifier()

    label = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M")
    brief_path = write_brief(mall, out_dir=str(repo / ".hedger" / "briefs"))
    tag = snapshot(repo, label)
    log.info("reflection_start", repo=str(repo), tag=tag, brief=str(brief_path))

    prompt = REFLECT_PROMPT.format(
        brief_path=str(brief_path), max_minutes=cfg.reflection.max_minutes,
    )
    cmd = [cfg.reflection.claude_code_cmd, "-p", prompt,
           "--allowed-tools", "edit,bash",
           "--output-format", "json"]
    if cfg.reflection.max_turns is not None:
        cmd.extend(["--max-turns", str(cfg.reflection.max_turns)])
    if dry_run:
        log.info("dry_run_reflection", cmd=cmd[:3] + ["<prompt>"])
        return {"status": "dry_run", "tag": tag, "brief": str(brief_path)}

    try:
        proc = subprocess.run(
            cmd, cwd=repo, capture_output=True, text=True,
            timeout=cfg.reflection.max_minutes * 60,
        )
        log.info("claude_code_done", returncode=proc.returncode,
                 tail_stdout=proc.stdout[-2000:] if proc.stdout else "",
                 tail_stderr=proc.stderr[-1000:] if proc.stderr else "")
    except subprocess.TimeoutExpired:
        log.warning("claude_code_timeout")
        rollback(repo, tag)
        notifier.notify("warning", "reflection rolled back: timeout", tag=tag)
        return {"status": "timeout", "tag": tag}
    except FileNotFoundError:
        log.error("claude_code_not_installed", cmd=cfg.reflection.claude_code_cmd)
        return {"status": "not_installed"}

    # TODO(thorwhalen/p_fin#1): streaming-JSON cost kill-switch. Today
    # max_usd is enforced post-hoc (parsed from the final --output-format
    # json envelope), with max_turns as the only pre-emptive guard. A
    # finer-grained kill would switch to --output-format stream-json, sum
    # per-event cost in a reader thread, and SIGTERM the subprocess when
    # the cap is crossed. The tracking issue covers the trade-off
    # (complexity, schema brittleness, single-turn overshoot unavoidable)
    # and the triggers under which this work becomes worth doing.
    cost_usd = _extract_cost_usd(proc.stdout)
    cost_over = (cfg.reflection.max_usd is not None
                 and cost_usd is not None
                 and cost_usd > cfg.reflection.max_usd)
    if cost_over:
        log.warning("reflection_cost_over_cap",
                    cost_usd=cost_usd, max_usd=cfg.reflection.max_usd)
        notifier.notify(
            "warning",
            f"reflection cost ${cost_usd:.2f} exceeded cap "
            f"${cfg.reflection.max_usd:.2f}",
            tag=tag, cost_usd=cost_usd, max_usd=cfg.reflection.max_usd,
        )

    if not _validation_passed(repo):
        log.warning("validation_failed_rolling_back")
        rollback(repo, tag)
        notifier.notify(
            "warning", "reflection rolled back: pytest failed", tag=tag,
        )
        return {"status": "rolled_back", "tag": tag, "cost_usd": cost_usd}

    # Persist a structured record of this session.
    record = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "tag": tag,
        "returncode": proc.returncode,
        "cost_usd": cost_usd,
        "cost_over_cap": cost_over,
        "summary_tail": (proc.stdout or "")[-500:],
    }
    mall["reflections"][(record["ts"][:10], "session")] = record
    return {"status": "ok", "tag": tag, "cost_usd": cost_usd, "record": record}


def _extract_cost_usd(stdout: str | None) -> float | None:
    """Pull ``total_cost_usd`` out of Claude Code's ``--output-format json`` output.

    Claude Code emits a single JSON envelope on stdout when invoked with
    ``--output-format json``. We parse the last JSON-looking line so noisy
    stdout from tools doesn't confuse the read. Returns ``None`` if cost
    can't be located — the caller treats that as "unknown, don't enforce".

    >>> _extract_cost_usd('chatter\\n{"total_cost_usd": 1.23, "x": 1}\\n')
    1.23
    >>> _extract_cost_usd('no json here') is None
    True
    """
    if not stdout:
        return None
    for raw in reversed(stdout.splitlines()):
        line = raw.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        cost = payload.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            return float(cost)
    return None


__all__ = ["reflect", "snapshot", "rollback"]
