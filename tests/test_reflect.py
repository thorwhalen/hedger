"""Reflection orchestrator integration test.

Exercises the snapshot -> brief -> spawn-claude -> validate -> commit/rollback
loop end-to-end against a temp git repo, with the claude subprocess and
the pytest gate stubbed so we never spend real model time.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from hedger.config import Config, ReflectionConfig
from hedger.data.stores import mall
from hedger.reflection.orchestrator import reflect


def _init_temp_git_repo() -> Path:
    """A throwaway git repo with one committed file."""
    p = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q"], cwd=p, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=p, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=p, check=True)
    (p / "x.txt").write_text("hello")
    subprocess.run(["git", "add", "x.txt"], cwd=p, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=p, check=True)
    return p


@pytest.fixture
def temp_repo(monkeypatch):
    """Throwaway git repo with hedger paths pinned inside it for isolation."""
    p = _init_temp_git_repo()
    monkeypatch.setenv("HEDGER_DATA_DIR", str(p / ".hedger"))
    monkeypatch.setenv("HEDGER_STATE_DIR", str(p / ".hedger"))
    return p


def _cfg(*, max_usd: float | None = 5.0, max_turns: int | None = 50) -> Config:
    return Config(
        reflection=ReflectionConfig(
            enabled=True,
            cron="0 22 * * *",
            timezone="UTC",
            max_minutes=1,
            max_turns=max_turns,
            max_usd=max_usd,
            claude_code_cmd="claude",  # we mock subprocess.run, so this never runs
        ),
    )


def test_reflect_dry_run_writes_brief_and_tags_snapshot(temp_repo):
    m = mall(temp_repo / ".hedger")
    res = reflect(repo=temp_repo, cfg=_cfg(), mall=m, dry_run=True)
    assert res["status"] == "dry_run"
    assert res["tag"].startswith("reflect-")
    # Brief was written.
    briefs = list((temp_repo / ".hedger" / "briefs").glob("brief-*.json"))
    assert len(briefs) == 1
    payload = json.loads(briefs[0].read_text())
    assert "counts" in payload
    # Snapshot tag exists.
    p = subprocess.run(["git", "tag", "-l", res["tag"]], cwd=temp_repo,
                       capture_output=True, text=True)
    assert res["tag"] in p.stdout


_REAL_SUBPROCESS_RUN = subprocess.run  # capture before patching


def _patch_only_claude(behaviour):
    """Return a side_effect that delegates git calls to real subprocess.run
    but routes any 'claude' invocation through `behaviour(...)`.
    """
    def side_effect(cmd, *a, **kw):
        if cmd and cmd[0] == "claude":
            return behaviour(cmd, *a, **kw)
        return _REAL_SUBPROCESS_RUN(cmd, *a, **kw)
    return side_effect


def test_reflect_rolls_back_when_validation_fails(temp_repo):
    """Failing pytest gate must rollback to the snapshot tag."""
    m = mall(temp_repo / ".hedger")
    cfg = _cfg()
    ok_proc = subprocess.CompletedProcess(args=[], returncode=0,
                                          stdout="ok", stderr="")
    with patch("hedger.reflection.orchestrator.subprocess.run",
               side_effect=_patch_only_claude(lambda *a, **k: ok_proc)), \
         patch("hedger.reflection.orchestrator._validation_passed",
               return_value=False):
        res = reflect(repo=temp_repo, cfg=cfg, mall=m)
    assert res["status"] == "rolled_back"
    assert res["tag"].startswith("reflect-")


def test_reflect_records_session_when_validation_passes(temp_repo):
    m = mall(temp_repo / ".hedger")
    cfg = _cfg()
    ok_proc = subprocess.CompletedProcess(args=[], returncode=0,
                                          stdout="all good", stderr="")
    with patch("hedger.reflection.orchestrator.subprocess.run",
               side_effect=_patch_only_claude(lambda *a, **k: ok_proc)), \
         patch("hedger.reflection.orchestrator._validation_passed",
               return_value=True):
        res = reflect(repo=temp_repo, cfg=cfg, mall=m)
    assert res["status"] == "ok"
    assert res["record"]["returncode"] == 0
    assert len(m["reflections"]) == 1


def test_reflect_handles_missing_claude_cmd(temp_repo):
    m = mall(temp_repo / ".hedger")
    cfg = _cfg()
    def boom(*a, **k):
        raise FileNotFoundError("claude not installed")
    with patch("hedger.reflection.orchestrator.subprocess.run",
               side_effect=_patch_only_claude(boom)):
        res = reflect(repo=temp_repo, cfg=cfg, mall=m)
    assert res["status"] == "not_installed"


def test_reflect_records_cost_under_cap(temp_repo):
    """When session cost is under max_usd, record it and don't warn."""
    m = mall(temp_repo / ".hedger")
    cfg = _cfg(max_usd=5.0)
    cost_proc = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout='{"total_cost_usd": 1.23, "result": "ok"}', stderr="",
    )
    notes: list[tuple] = []

    class _Spy:
        def notify(self, level, msg, **kw): notes.append((level, msg, kw))

    with patch("hedger.reflection.orchestrator.subprocess.run",
               side_effect=_patch_only_claude(lambda *a, **k: cost_proc)), \
         patch("hedger.reflection.orchestrator._validation_passed",
               return_value=True):
        res = reflect(repo=temp_repo, cfg=cfg, mall=m, notifier=_Spy())
    assert res["status"] == "ok"
    assert res["cost_usd"] == 1.23
    assert res["record"]["cost_over_cap"] is False
    assert notes == []  # no cost warning fired


def test_reflect_warns_when_cost_exceeds_cap(temp_repo):
    """Over-cap session: validation still passes, but notifier is fired."""
    m = mall(temp_repo / ".hedger")
    cfg = _cfg(max_usd=2.0)
    cost_proc = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout='noise\n{"total_cost_usd": 7.5}\n', stderr="",
    )
    notes: list[tuple] = []

    class _Spy:
        def notify(self, level, msg, **kw): notes.append((level, msg, kw))

    with patch("hedger.reflection.orchestrator.subprocess.run",
               side_effect=_patch_only_claude(lambda *a, **k: cost_proc)), \
         patch("hedger.reflection.orchestrator._validation_passed",
               return_value=True):
        res = reflect(repo=temp_repo, cfg=cfg, mall=m, notifier=_Spy())
    assert res["status"] == "ok"
    assert res["cost_usd"] == 7.5
    assert res["record"]["cost_over_cap"] is True
    assert any("exceeded cap" in msg for _, msg, _ in notes)


def test_reflect_passes_max_turns_flag(temp_repo):
    """max_turns is forwarded to claude as --max-turns."""
    m = mall(temp_repo / ".hedger")
    cfg = _cfg(max_turns=3, max_usd=None)
    captured: list[list[str]] = []

    def capture(cmd, *a, **kw):
        captured.append(list(cmd))
        return subprocess.CompletedProcess(args=cmd, returncode=0,
                                           stdout="{}", stderr="")

    with patch("hedger.reflection.orchestrator.subprocess.run",
               side_effect=_patch_only_claude(capture)), \
         patch("hedger.reflection.orchestrator._validation_passed",
               return_value=True):
        reflect(repo=temp_repo, cfg=cfg, mall=m)
    assert captured, "claude was never invoked"
    cmd = captured[0]
    assert "--max-turns" in cmd and cmd[cmd.index("--max-turns") + 1] == "3"
    assert "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "json"


def test_reflect_handles_timeout(temp_repo):
    m = mall(temp_repo / ".hedger")
    cfg = _cfg()
    def slow(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=60)
    with patch("hedger.reflection.orchestrator.subprocess.run",
               side_effect=_patch_only_claude(slow)):
        res = reflect(repo=temp_repo, cfg=cfg, mall=m)
    assert res["status"] == "timeout"
    assert res["tag"].startswith("reflect-")
