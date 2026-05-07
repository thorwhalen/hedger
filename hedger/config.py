"""Configuration — single source of truth.

Loads `config.toml` from the working directory (or a path given by
HEDGER_CONFIG env var), with environment variable overrides for secrets.

Convention: secrets in env (ANTHROPIC_API_KEY, ALPACA_API_KEY, …);
everything else in TOML so it's diffable and version-controlled.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import tomli


@dataclass(frozen=True, slots=True)
class BrokerConfig:
    """Broker selection and credentials reference (not the secret itself).

    Default is ``alpaca:paper`` — the recommended starting target. Use ``paper``
    (no colon) for the in-memory broker (backtesting / offline development).
    """
    name: str = "alpaca:paper"     # 'paper' | 'alpaca:paper' | 'alpaca:live' | 'ccxt:binance'
    paper: bool = True
    base_url: str | None = None    # e.g. https://paper-api.alpaca.markets


@dataclass(frozen=True, slots=True)
class DataConfig:
    """Where bars come from and where they're cached."""
    primary: str = "alpaca"         # 'alpaca' | 'ccxt:kraken' | 'yfinance'
    cache_dir: str = ".hedger/cache"
    timeframe: str = "1h"


@dataclass(frozen=True, slots=True)
class ReflectionConfig:
    """Overnight self-improvement loop settings.

    Cost controls (Claude Code session per reflection):

    - ``max_minutes``  wall-clock timeout (kills subprocess).
    - ``max_turns``    pre-emptive coarse cap. Passed to Claude Code as
                       ``--max-turns``; each turn is one model call.
                       ``None`` disables the flag.
    - ``max_usd``      post-hoc soft cap. The orchestrator parses
                       ``total_cost_usd`` from Claude Code's JSON output
                       and, if exceeded, logs a warning, fires the
                       configured notifier, and records the overage in the
                       mall. ``None`` disables the check.

    Rough Opus 4 pricing as of 2026-Q1: ~$15 / 1M input tokens,
    ~$75 / 1M output tokens (with prompt-caching, effective input cost can
    drop 5-10x). A typical reflection session reading the brief, editing
    one file, and running tests lands around $1-3.
    """
    enabled: bool = False
    cron: str = "0 22 * * *"         # 22:00 every day in local TZ
    timezone: str = "Europe/Paris"   # CET/CEST
    max_minutes: int = 480           # 8h wall-clock budget
    max_turns: int | None = 50       # pre-emptive coarse cap (turns ~= model calls)
    max_usd: float | None = 5.0      # post-hoc soft cap on session cost
    claude_code_cmd: str = "claude"  # path to the claude-code binary
    skills_dir: str = ".claude/skills"


@dataclass(frozen=True, slots=True)
class RiskConfig:
    max_gross_exposure: float = 1.0      # 100% of NAV
    max_position_weight: float = 0.10    # no single position > 10% NAV
    max_daily_loss: float = 0.02         # circuit-breaker at -2% intraday
    max_open_orders: int = 50


@dataclass(frozen=True, slots=True)
class NotifyConfig:
    """Where to send out-of-band alerts (vetoes, drawdown, rolled-back reflection)."""
    kind: str = "log"                   # 'log' | 'webhook[:URL]' | 'telegram' | 'multi:...'
    drawdown_alert_pct: float = 0.01    # notify on >= 1% intraday loss


@dataclass(frozen=True, slots=True)
class Config:
    universe: tuple[str, ...] = ("SPY", "QQQ", "BTC/USD")
    timeframe: str = "1h"
    base_currency: str = "USD"
    tax_policy: str = "none"           # 'none' | 'us_wash_sale' | 'fr_pfu' | 'crypto_lifo'
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    data: DataConfig = field(default_factory=DataConfig)
    reflection: ReflectionConfig = field(default_factory=ReflectionConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def anthropic_api_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY")

    @property
    def alpaca_credentials(self) -> tuple[str | None, str | None]:
        return os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")


def _check_no_secrets_in_toml(d: dict[str, Any], _path: str = "") -> None:
    """Raise if any key in *d* (recursively) looks like a secret.

    Secrets must come from the envfile (`hedger install`), never config.toml.
    """
    from hedger.install import is_secret_key_name
    for k, v in d.items():
        full = f"{_path}.{k}" if _path else k
        if is_secret_key_name(k):
            raise ValueError(
                f"Secret-shaped key {full!r} found in config.toml. "
                "Secrets must live in an envfile, not the TOML config. "
                "Run `hedger install` and `hedger where-keys` to set this up."
            )
        if isinstance(v, dict):
            _check_no_secrets_in_toml(v, full)


def _coerce(d: dict[str, Any], cls):
    """Build a dataclass `cls` from a dict, recursing into nested dataclasses."""
    if not d:
        return cls()
    fields = {f.name: f for f in cls.__dataclass_fields__.values()}
    kwargs = {}
    for k, v in d.items():
        if k not in fields:
            kwargs.setdefault("extras", {})[k] = v
            continue
        ft = fields[k].type
        # nested dataclass identified by class object on the field
        default = fields[k].default_factory() if callable(getattr(fields[k], "default_factory", None)) and fields[k].default_factory is not field else fields[k].default
        if hasattr(default, "__dataclass_fields__") and isinstance(v, dict):
            kwargs[k] = _coerce(v, type(default))
        else:
            kwargs[k] = v
    # tuples stay tuples
    if "universe" in kwargs and isinstance(kwargs["universe"], list):
        kwargs["universe"] = tuple(kwargs["universe"])
    return cls(**kwargs)


@cache
def load_config(path: str | Path | None = None) -> Config:
    """Load `config.toml`. Falls back to defaults if the file is missing.

    >>> isinstance(load_config('/nonexistent/path.toml'), Config)
    True
    """
    p = Path(path or os.environ.get("HEDGER_CONFIG", "config.toml"))
    if not p.exists():
        return Config()
    with p.open("rb") as f:
        data = tomli.load(f)
    _check_no_secrets_in_toml(data)
    return _coerce(data, Config)


__all__ = ["Config", "BrokerConfig", "DataConfig", "ReflectionConfig",
           "RiskConfig", "NotifyConfig", "load_config"]
