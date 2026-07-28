"""Tests for hedger.install: envfile creation, idempotency, systemd unit."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from hedger.install import (
    KNOWN_SECRETS,
    _parse_envfile,
    install,
    is_secret_key_name,
    load_envfile_into_environ,
    warn_if_ambient_shadows_envfile,
    where_keys,
)


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_install_creates_envfile_mode_600(tmp_path: Path):
    env = tmp_path / "hedger.env"
    out = install(envfile=str(env))
    assert env.exists()
    assert _mode(env) == 0o600
    assert "created" in out
    # Template lists every known secret.
    text = env.read_text()
    for name in KNOWN_SECRETS:
        assert f"{name}=" in text


def test_install_is_idempotent_and_does_not_clobber(tmp_path: Path):
    env = tmp_path / "hedger.env"
    install(envfile=str(env))
    env.write_text("ANTHROPIC_API_KEY=already-set\n")
    # Loosen permissions to verify install re-tightens them.
    env.chmod(0o644)

    out = install(envfile=str(env))
    assert "present" in out
    assert env.read_text() == "ANTHROPIC_API_KEY=already-set\n"
    assert _mode(env) == 0o600


def test_install_systemd_writes_unit_with_envfile_reference(tmp_path: Path):
    env = tmp_path / "hedger.env"
    unit = tmp_path / "hedger.service"
    work = tmp_path / "work"
    work.mkdir()
    # Patch the unit-path resolver to point at our tmp file.
    from hedger import install as install_mod
    install_mod._create_unit(unit, env, work)
    text = unit.read_text()

    assert f"EnvironmentFile={env}" in text
    assert f"WorkingDirectory={work}" in text
    assert "ExecStart=" in text and " serve" in text
    assert "Restart=on-failure" in text


def test_install_systemd_does_not_overwrite_existing_unit(tmp_path: Path):
    env = tmp_path / "hedger.env"
    unit = tmp_path / "hedger.service"
    work = tmp_path
    unit.write_text("custom unit, hands off")

    from hedger import install as install_mod
    created = install_mod._create_unit(unit, env, work)
    assert created is False
    assert unit.read_text() == "custom unit, hands off"


def test_where_keys_reports_present_and_missing(tmp_path: Path):
    env = tmp_path / "hedger.env"
    install(envfile=str(env))
    env.write_text(
        "ANTHROPIC_API_KEY=sk-set\n"
        "ALPACA_API_KEY=\n"
        'ALPACA_SECRET_KEY="quoted-empty-equivalent"\n'
    )
    out = where_keys(envfile=str(env))

    assert "ANTHROPIC_API_KEY" in out  # listed as set
    # ALPACA_API_KEY has no value -> missing
    assert "missing:" in out
    missing_line = next(ln for ln in out.splitlines() if ln.startswith("missing:"))
    assert "ALPACA_API_KEY" in missing_line


def test_where_keys_when_envfile_absent(tmp_path: Path):
    env = tmp_path / "nope.env"
    out = where_keys(envfile=str(env))
    assert "not created yet" in out
    assert "hedger install" in out


@pytest.mark.parametrize("name", [
    "anthropic_api_key", "ALPACA_SECRET_KEY", "github_token",
    "db_password", "ANTHROPIC_FOO", "alpaca_anything",
])
def test_is_secret_key_name_positive(name: str):
    assert is_secret_key_name(name) is True


@pytest.mark.parametrize("name", [
    "timeframe", "universe", "max_position_weight", "broker", "tax_policy",
])
def test_is_secret_key_name_negative(name: str):
    assert is_secret_key_name(name) is False


def test_install_warns_about_missing_keys_in_next_steps(tmp_path: Path):
    env = tmp_path / "hedger.env"
    out = install(envfile=str(env))
    # Fresh envfile -> all known secrets are missing.
    assert "missing:" in out
    for name in KNOWN_SECRETS:
        assert name in out


def test_ambient_shadow_warns_on_mismatch(tmp_path: Path, monkeypatch, capsys):
    env = tmp_path / "hedger.env"
    env.write_text("ALPACA_API_KEY=file-key-3LC4\nALPACA_SECRET_KEY=file-sec-EMHA\n")
    monkeypatch.setenv("ALPACA_API_KEY", "shell-key-PY4Z")  # differs -> shadows
    monkeypatch.setenv("ALPACA_SECRET_KEY", "file-sec-EMHA")  # same -> not flagged
    assert warn_if_ambient_shadows_envfile(str(env)) is True
    err = capsys.readouterr().err
    assert "shadowing the envfile" in err
    assert "ALPACA_API_KEY" in err
    assert "ALPACA_SECRET_KEY" not in err  # matching key must not be reported
    assert "…PY4Z" in err  # last-4 fingerprint shown to disambiguate
    assert "shell-key-PY4Z" not in err  # but the full secret never leaks


def test_ambient_shadow_silent_when_matching_or_unset(tmp_path: Path, monkeypatch, capsys):
    env = tmp_path / "hedger.env"
    env.write_text("ALPACA_API_KEY=k\nALPACA_SECRET_KEY=s\n")
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    assert warn_if_ambient_shadows_envfile(str(env)) is False
    assert capsys.readouterr().err == ""


def test_default_editor_is_pico(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    env = tmp_path / "hedger.env"
    out = install(envfile=str(env))
    assert f"pico {env}" in out


def test_editor_respects_env_var(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EDITOR", "emacs")
    env = tmp_path / "hedger.env"
    out = install(envfile=str(env))
    assert f"emacs {env}" in out


def test_parse_envfile_handles_quotes_and_blanks():
    parsed = _parse_envfile(
        '\n# comment\nA=1\nB="two"\nC=\'three\'\nD=\nE\n'
    )
    assert parsed == {"A": "1", "B": "two", "C": "three"}


def test_load_envfile_sets_only_missing_vars(tmp_path: Path, monkeypatch):
    env = tmp_path / "hedger.env"
    env.write_text("ANTHROPIC_API_KEY=from-file\nALPACA_API_KEY=from-file\n")
    env.chmod(0o600)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-shell")  # pre-existing wins
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)

    applied = load_envfile_into_environ(env)

    assert applied == {"ALPACA_API_KEY": "from-file"}
    assert os.environ["ANTHROPIC_API_KEY"] == "from-shell"
    assert os.environ["ALPACA_API_KEY"] == "from-file"


def test_load_envfile_warns_on_lax_mode(tmp_path: Path, monkeypatch, capsys):
    env = tmp_path / "hedger.env"
    env.write_text("FOO=bar\n")
    env.chmod(0o644)
    monkeypatch.delenv("FOO", raising=False)

    load_envfile_into_environ(env)

    err = capsys.readouterr().err
    assert "should be 600" in err
    assert str(env) in err


def test_load_envfile_missing_file_is_noop(tmp_path: Path):
    applied = load_envfile_into_environ(tmp_path / "nonexistent")
    assert applied == {}


def test_where_keys_honors_hedger_envfile_var(tmp_path: Path, monkeypatch):
    env = tmp_path / "hedger.env"
    install(envfile=str(env))
    monkeypatch.setenv("HEDGER_ENVFILE", str(env))

    out = where_keys()  # no explicit --envfile

    assert str(env) in out
    # Sanity: would have shown a different path without the env var.
    monkeypatch.delenv("HEDGER_ENVFILE")
    out_default = where_keys()
    assert str(env) not in out_default


def test_load_envfile_resolves_via_hedger_envfile_var(tmp_path: Path, monkeypatch):
    env = tmp_path / "hedger.env"
    env.write_text("HEDGER_TEST_VAR=via-env\n")
    env.chmod(0o600)
    monkeypatch.delenv("HEDGER_TEST_VAR", raising=False)
    monkeypatch.setenv("HEDGER_ENVFILE", str(env))

    applied = load_envfile_into_environ()

    assert applied == {"HEDGER_TEST_VAR": "via-env"}


def test_install_systemd_flag_prints_activate_command(tmp_path: Path, monkeypatch):
    env = tmp_path / "hedger.env"
    # Force a writable unit path so the test never touches /etc.
    fake_unit = tmp_path / "hedger.service"
    monkeypatch.setattr("hedger.install.default_unit_path", lambda: fake_unit)

    out = install(systemd=True, envfile=str(env), workdir=str(tmp_path))
    assert fake_unit.exists()
    assert "systemctl" in out
    assert "hedger.service" in out
