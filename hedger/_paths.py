"""Per-user directory resolution for hedger.

Where does hedger keep its files? This module is the single source of
truth.

Defaults follow platform conventions via ``platformdirs``:

  =======  =======================================  =======================
  Kind     Linux (XDG)                              macOS / Windows
  =======  =======================================  =======================
  config   ``~/.config/hedger/``                    platformdirs default
  data     ``~/.local/share/hedger/``               platformdirs default
  state    ``~/.local/state/hedger/``               platformdirs default
  =======  =======================================  =======================

Each kind can be overridden with an environment variable, in priority order:

  1. The kind-specific env var: ``HEDGER_CONFIG_DIR``, ``HEDGER_DATA_DIR``,
     ``HEDGER_STATE_DIR``.
  2. ``HEDGER_HOME``, which (if set) places all three under one root —
     ``$HEDGER_HOME/config``, ``$HEDGER_HOME/data``, ``$HEDGER_HOME/state``.
  3. The platformdirs default.

Directories are created on access; callers receive a ready-to-use ``Path``.

Use ``state`` for runtime artifacts that would be regenerated on a fresh
run (briefs, cycle logs). Use ``data`` for things you would be sad to lose
(bars, decisions, fills, positions). hedger has no ``cache_dir`` because
its bar parquet files are immutable history from a rate-limited feed,
not regenerable cache.
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import PlatformDirs

_APP = "hedger"
_dirs = PlatformDirs(appname=_APP, appauthor=False)


def _resolve(kind: str, env_kind: str, default: Path) -> Path:
    """Pick a directory by env override, then ``HEDGER_HOME``, then default."""
    raw = os.environ.get(env_kind)
    if raw:
        p = Path(raw).expanduser()
    elif home := os.environ.get("HEDGER_HOME"):
        p = Path(home).expanduser() / kind
    else:
        p = default
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    """User config directory. Override: ``HEDGER_CONFIG_DIR`` or ``HEDGER_HOME``."""
    return _resolve("config", "HEDGER_CONFIG_DIR", Path(_dirs.user_config_dir))


def data_dir() -> Path:
    """User data directory (bars, jsonls). Override: ``HEDGER_DATA_DIR`` or ``HEDGER_HOME``."""
    return _resolve("data", "HEDGER_DATA_DIR", Path(_dirs.user_data_dir))


def state_dir() -> Path:
    """Runtime state (briefs, cycle logs). Override: ``HEDGER_STATE_DIR`` or ``HEDGER_HOME``."""
    return _resolve("state", "HEDGER_STATE_DIR", Path(_dirs.user_state_dir))


__all__ = ["config_dir", "data_dir", "state_dir"]
