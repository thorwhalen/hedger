"""Internal utilities: env checks, structured logging, small helpers."""

from __future__ import annotations

import os
import shutil
import sys
from typing import Any

import structlog


def check_requirements(*, broker: str = "alpaca", llm: bool = True) -> dict[str, str]:
    """Check that the user has what they need; return a {what: how_to_fix} dict.

    Default broker is ``alpaca`` (the recommended target — see ALPACA.md).
    Pass ``broker='paper'`` to skip Alpaca-specific checks when you only need
    the in-memory broker for backtesting.

    Mirrors Thor's package-UX convention: be informative when the env is wrong.

    >>> isinstance(check_requirements(broker='paper', llm=False), dict)
    True
    """
    missing: dict[str, str] = {}

    if llm and not os.environ.get("ANTHROPIC_API_KEY"):
        missing["ANTHROPIC_API_KEY"] = (
            "Get a key at https://console.anthropic.com/ and "
            "`export ANTHROPIC_API_KEY=...`."
        )

    if broker.startswith("alpaca"):
        key = os.environ.get("ALPACA_API_KEY")
        sec = os.environ.get("ALPACA_SECRET_KEY")
        if not key:
            missing["ALPACA_API_KEY"] = (
                "Sign up at https://alpaca.markets/ (paper account is free), "
                "create an API key, then `export ALPACA_API_KEY=...` and "
                "`export ALPACA_SECRET_KEY=...`."
            )
        if not sec:
            missing["ALPACA_SECRET_KEY"] = (
                "Generate the secret on the Alpaca dashboard (shown once); "
                "then `export ALPACA_SECRET_KEY=...`."
            )
        # Round-trip check: prove the keys actually authenticate. Fail-soft.
        if key and sec:
            paper = ":live" not in broker
            try:
                from alpaca.trading.client import TradingClient
                acc = TradingClient(key, sec, paper=paper).get_account()
                if str(getattr(acc, "status", "")).lower().endswith("active") is False:
                    missing["alpaca_account_status"] = (
                        f"Account status is {acc.status!r}, not ACTIVE — "
                        "complete onboarding at https://app.alpaca.markets/."
                    )
            except ImportError:
                missing["alpaca-py"] = "`pip install alpaca-py` (already a hedger dep)."
            except Exception as e:
                missing["alpaca_auth"] = (
                    f"Alpaca {'paper' if paper else 'live'} auth failed: "
                    f"{type(e).__name__}: {e}. Verify keys at "
                    "https://app.alpaca.markets/."
                )

    if broker.startswith("ccxt:"):
        venue = broker.split(":", 1)[1].upper()
        for k in (f"{venue}_API_KEY", f"{venue}_SECRET"):
            if not os.environ.get(k):
                missing[k] = f"Create read+trade API keys on {venue.title()} and export."

    if not shutil.which("claude"):
        missing["claude-code-cli"] = (
            "Install Claude Code: https://docs.claude.com/claude-code "
            "(needed only if you want the overnight reflection loop)."
        )

    return missing


def configure_logging(level: str = "INFO") -> None:
    """Configure structlog to print JSON-ish, colourised in TTY."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer() if sys.stderr.isatty()
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(__import__("logging"), level)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "hedger") -> Any:
    """Return a structlog logger. Lazy-configure on first call."""
    if not getattr(get_logger, "_configured", False):
        configure_logging()
        get_logger._configured = True  # type: ignore[attr-defined]
    return structlog.get_logger(name)


__all__ = ["check_requirements", "configure_logging", "get_logger"]
