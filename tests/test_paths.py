"""Tests for ``hedger._paths``: per-user directory resolution."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hedger import _paths


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip any pre-existing override env vars so each test starts clean."""
    for k in ("HEDGER_HOME", "HEDGER_CONFIG_DIR", "HEDGER_DATA_DIR", "HEDGER_STATE_DIR"):
        monkeypatch.delenv(k, raising=False)
    yield


def test_defaults_use_platformdirs():
    """No env vars → platformdirs defaults; dirs are created on access."""
    assert _paths.config_dir().is_dir()
    assert _paths.data_dir().is_dir()
    assert _paths.state_dir().is_dir()


def test_kind_specific_env_overrides(tmp_path: Path, monkeypatch):
    """``HEDGER_<KIND>_DIR`` env vars override per-kind defaults."""
    cfg, dat, sta = tmp_path / "c", tmp_path / "d", tmp_path / "s"
    monkeypatch.setenv("HEDGER_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("HEDGER_DATA_DIR", str(dat))
    monkeypatch.setenv("HEDGER_STATE_DIR", str(sta))
    assert _paths.config_dir() == cfg
    assert _paths.data_dir() == dat
    assert _paths.state_dir() == sta
    # All created
    assert cfg.is_dir() and dat.is_dir() and sta.is_dir()


def test_hedger_home_groups_under_one_root(tmp_path: Path, monkeypatch):
    """``HEDGER_HOME`` places all kinds under one root."""
    monkeypatch.setenv("HEDGER_HOME", str(tmp_path))
    assert _paths.config_dir() == tmp_path / "config"
    assert _paths.data_dir() == tmp_path / "data"
    assert _paths.state_dir() == tmp_path / "state"


def test_kind_specific_takes_precedence_over_hedger_home(tmp_path: Path, monkeypatch):
    """A kind-specific override beats ``HEDGER_HOME``."""
    monkeypatch.setenv("HEDGER_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HEDGER_DATA_DIR", str(tmp_path / "explicit_data"))
    assert _paths.data_dir() == tmp_path / "explicit_data"
    # Other kinds still follow HEDGER_HOME
    assert _paths.config_dir() == tmp_path / "home" / "config"


def test_tilde_expansion(tmp_path: Path, monkeypatch):
    """``~`` in env-var values is expanded."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("HEDGER_DATA_DIR", "~/hedger_data")
    assert _paths.data_dir() == tmp_path / "hedger_data"


def test_mall_uses_data_dir_by_default(tmp_path: Path, monkeypatch):
    """``mall()`` with no args writes under ``data_dir()``."""
    monkeypatch.setenv("HEDGER_DATA_DIR", str(tmp_path))
    from hedger.data.stores import mall

    m = mall()
    m["fills"][("k",)] = {"foo": "bar"}
    assert (tmp_path / "fills.jsonl").exists()


def test_write_brief_uses_state_dir_by_default(tmp_path: Path, monkeypatch):
    """``write_brief()`` with no args writes under ``state_dir() / 'briefs'``."""
    monkeypatch.setenv("HEDGER_STATE_DIR", str(tmp_path))
    from hedger.reflection.monitor import write_brief

    out = write_brief({})
    assert out.parent == tmp_path / "briefs"
    assert out.exists()
