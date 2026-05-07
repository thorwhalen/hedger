"""Tests for the notifier seam."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from hedger.notify import (
    LogNotifier,
    MultiNotifier,
    TelegramNotifier,
    WebhookNotifier,
    make_notifier,
)


class _RecordingNotifier:
    name = "recording"

    def __init__(self):
        self.calls: list[tuple] = []

    def notify(self, level, message, **context):
        self.calls.append((level, message, context))


def test_log_notifier_doesnt_raise():
    LogNotifier().notify("info", "hello", x=1)


def test_make_notifier_log():
    assert isinstance(make_notifier("log"), LogNotifier)


def test_make_notifier_webhook_explicit_url():
    n = make_notifier("webhook:https://example.com/hook")
    assert isinstance(n, WebhookNotifier)
    assert n.url == "https://example.com/hook"


def test_make_notifier_webhook_env(monkeypatch):
    monkeypatch.setenv("HEDGER_WEBHOOK_URL", "https://example.com/x")
    n = make_notifier("webhook")
    assert isinstance(n, WebhookNotifier)
    assert n.url == "https://example.com/x"


def test_make_notifier_webhook_env_missing(monkeypatch):
    monkeypatch.delenv("HEDGER_WEBHOOK_URL", raising=False)
    with pytest.raises(RuntimeError, match="HEDGER_WEBHOOK_URL"):
        make_notifier("webhook")


def test_make_notifier_unknown():
    with pytest.raises(ValueError, match="Unknown notifier spec"):
        make_notifier("bogus")


def test_make_notifier_multi(monkeypatch):
    monkeypatch.setenv("HEDGER_WEBHOOK_URL", "https://example.com/x")
    n = make_notifier("multi:log,webhook")
    assert isinstance(n, MultiNotifier)
    assert len(n.notifiers) == 2


def test_multi_notifier_isolates_failures():
    bad = MagicMock()
    bad.notify.side_effect = RuntimeError("boom")
    bad.name = "bad"
    good = _RecordingNotifier()
    multi = MultiNotifier(notifiers=(bad, good))
    multi.notify("info", "hello")
    assert good.calls == [("info", "hello", {})]


def test_telegram_unconfigured_does_not_raise(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    # Just must not raise.
    TelegramNotifier().notify("info", "test")


def test_webhook_notifier_calls_requests_post():
    n = WebhookNotifier(url="https://example.com/hook")
    with patch("requests.post") as post:
        n.notify("warning", "drawdown", loss=0.02)
        assert post.called
        kwargs = post.call_args.kwargs
        assert kwargs["json"]["text"] == "drawdown"
        assert kwargs["json"]["level"] == "warning"
        assert kwargs["json"]["loss"] == 0.02


def test_runner_uses_notifier_for_drawdown(monkeypatch):
    """Synthetic tick: open NAV is set high; broker reports loss; notifier fires."""
    import tempfile

    from hedger.base import AssetClass, Symbol
    from hedger.config import Config, DataConfig, NotifyConfig, RiskConfig
    from hedger.data.stores import mall
    from hedger.live.runner import Runner

    class StaticBroker:
        name = "static"

        def __init__(self, equity):
            self.equity = equity

        def submit(self, o):  # pragma: no cover
            return "id"

        def cancel(self, oid):  # pragma: no cover
            pass

        def fills(self):
            return iter(())

        def positions(self):
            return {}

        def nav(self):
            return self.equity

    class NoopStrategy:
        name = "noop"

        def __call__(self, bars, *, context=None):
            return ()

    rec = _RecordingNotifier()
    cfg = Config(
        universe=("SPY",),
        timeframe="1h",
        data=DataConfig(primary="alpaca", timeframe="1h"),
        notify=NotifyConfig(kind="log", drawdown_alert_pct=0.005),
        risk=RiskConfig(),
    )
    broker = StaticBroker(equity=100_000.0)
    runner = Runner(config=cfg, strategy=NoopStrategy(), broker=broker,
                    source=None, mall=mall(tempfile.mkdtemp()),  # type: ignore[arg-type]
                    notifier=rec)
    # First tick: establishes today's open nav.
    monkeypatch.setattr(runner, "fetch_window", lambda s, lookback_bars=200: [])
    runner.tick()
    # Now broker reports a loss > 0.5%.
    broker.equity = 99_000.0
    runner.tick()
    drawdown_alerts = [c for c in rec.calls if "drawdown" in c[1]]
    assert len(drawdown_alerts) >= 1
    assert drawdown_alerts[0][2]["loss_pct"] >= 0.005
