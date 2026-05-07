"""Notifier seam — fan out important events to humans.

Notifiers are dataclasses with a single method: ``notify(level, message,
**context)``. The shipped impls are:

* :class:`LogNotifier` — writes a structured log line; the default. Free,
  no setup.
* :class:`WebhookNotifier` — POSTs ``{"text": ...}`` JSON to a URL. Drop-in
  for Slack incoming webhooks and Discord webhook URLs.
* :class:`TelegramNotifier` — sends via the Telegram Bot API given a
  bot token and chat id.
* :class:`MultiNotifier` — composes several notifiers with fan-out
  semantics; one failing notifier doesn't block the others.

Pick one by passing a spec to :func:`make_notifier` (see its docstring).

Spec mini-grammar:
    'log'                                 -> LogNotifier
    'webhook'                             -> WebhookNotifier(env HEDGER_WEBHOOK_URL)
    'webhook:https://.../...'             -> WebhookNotifier(literal URL)
    'telegram'                            -> TelegramNotifier(env vars)

Levels are advisory strings (``info``, ``warning``, ``error``). They are
not filtered here — every notify call goes out. If you need filtering,
wrap the notifier or use :class:`MultiNotifier` with selective routing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable, Protocol, runtime_checkable

from hedger.util import get_logger

log = get_logger("hedger.notify")


@runtime_checkable
class Notifier(Protocol):
    """One method: ``notify(level, message, **context) -> None``."""

    def notify(self, level: str, message: str, **context) -> None: ...


@dataclass
class LogNotifier:
    """Default notifier: structured log line. Free, always on."""

    name: str = "log"

    def notify(self, level: str, message: str, **context) -> None:
        getattr(log, level if level in {"info", "warning", "error"} else "info")(
            "notify",
            message=message,
            **context,
        )


@dataclass
class WebhookNotifier:
    """POST JSON to a webhook URL. Slack/Discord/Mattermost compatible.

    The payload is ``{"text": message, "level": level, **context}``. Slack
    and Discord both render the ``text`` field as the message body and
    ignore the rest, so this is a single shape that works for both.
    """

    url: str
    name: str = "webhook"
    timeout_s: float = 4.0

    def notify(self, level: str, message: str, **context) -> None:
        try:
            import requests
        except ImportError:  # pragma: no cover — requests pulled by alpaca-py
            log.warning("notify_requests_missing")
            return
        try:
            requests.post(
                self.url,
                json={"text": message, "level": level, **context},
                timeout=self.timeout_s,
            )
        except Exception as e:  # pragma: no cover — network
            log.warning("notify_webhook_failed", error=f"{type(e).__name__}: {e}")


@dataclass
class TelegramNotifier:
    """Telegram Bot API notifier.

    Set ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` in env, or pass
    them in. The bot must already be in a chat with the target user/group.
    """

    bot_token: str | None = None
    chat_id: str | None = None
    name: str = "telegram"
    timeout_s: float = 4.0

    def __post_init__(self):
        self.bot_token = self.bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = self.chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    def notify(self, level: str, message: str, **context) -> None:
        if not (self.bot_token and self.chat_id):
            log.warning("telegram_unconfigured")
            return
        try:
            import requests  # noqa: F401
        except ImportError:  # pragma: no cover
            return
        import requests

        ctx_str = " ".join(f"{k}={v}" for k, v in context.items())
        text = f"[{level.upper()}] {message}" + (f"\n{ctx_str}" if ctx_str else "")
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=self.timeout_s,
            )
        except Exception as e:  # pragma: no cover — network
            log.warning("notify_telegram_failed", error=f"{type(e).__name__}: {e}")


@dataclass
class MultiNotifier:
    """Fan out to a tuple of notifiers; failures are isolated per-notifier."""

    notifiers: tuple = field(default_factory=tuple)
    name: str = "multi"

    def notify(self, level: str, message: str, **context) -> None:
        for n in self.notifiers:
            try:
                n.notify(level, message, **context)
            except Exception as e:  # pragma: no cover
                log.warning(
                    "notify_subnotifier_failed",
                    notifier=getattr(n, "name", "?"),
                    error=f"{type(e).__name__}: {e}",
                )


def make_notifier(spec: str = "log") -> Notifier:
    """Parse a spec string and return a Notifier.

    >>> isinstance(make_notifier('log'), LogNotifier)
    True
    """
    if spec == "log":
        return LogNotifier()
    if spec == "webhook":
        url = os.environ.get("HEDGER_WEBHOOK_URL")
        if not url:
            raise RuntimeError(
                "spec='webhook' needs HEDGER_WEBHOOK_URL in env, "
                "or pass 'webhook:https://...' explicitly."
            )
        return WebhookNotifier(url=url)
    if spec.startswith("webhook:"):
        return WebhookNotifier(url=spec.split(":", 1)[1])
    if spec == "telegram":
        return TelegramNotifier()
    if spec.startswith("multi:"):
        sub = [make_notifier(s.strip()) for s in spec[len("multi:") :].split(",")]
        return MultiNotifier(notifiers=tuple(sub))
    raise ValueError(
        f"Unknown notifier spec: {spec!r}. Use 'log', 'webhook[:URL]', "
        "'telegram', or 'multi:<spec1,spec2,...>'."
    )


__all__ = [
    "Notifier",
    "LogNotifier",
    "WebhookNotifier",
    "TelegramNotifier",
    "MultiNotifier",
    "make_notifier",
]
