"""Config refuses to load secrets that landed in config.toml by mistake."""

from __future__ import annotations

from pathlib import Path

import pytest

from hedger.config import load_config


def _write(p: Path, body: str) -> Path:
    p.write_text(body)
    return p


def test_secret_at_top_level_raises(tmp_path: Path):
    p = _write(tmp_path / "config.toml",
               'anthropic_api_key = "sk-leaked"\n')
    load_config.cache_clear()
    with pytest.raises(ValueError, match="Secret-shaped key"):
        load_config(p)


def test_secret_in_nested_table_raises(tmp_path: Path):
    p = _write(tmp_path / "config.toml",
               '[broker]\nname = "alpaca:paper"\nsecret_key = "leak"\n')
    load_config.cache_clear()
    with pytest.raises(ValueError, match="Secret-shaped key"):
        load_config(p)


def test_clean_config_loads(tmp_path: Path):
    p = _write(tmp_path / "config.toml",
               'timeframe = "1h"\nuniverse = ["SPY", "QQQ"]\n')
    load_config.cache_clear()
    cfg = load_config(p)
    assert cfg.timeframe == "1h"
    assert cfg.universe == ("SPY", "QQQ")


def test_alpaca_prefixed_key_is_refused(tmp_path: Path):
    # Catches `alpaca_api_key = "..."` even though it isn't a *_key suffix
    # match for the prefix family.
    p = _write(tmp_path / "config.toml", 'alpaca_anything = "x"\n')
    load_config.cache_clear()
    with pytest.raises(ValueError):
        load_config(p)
