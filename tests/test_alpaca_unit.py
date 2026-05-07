"""Unit tests for the Alpaca-facing pieces (no live API calls).

These exercise the wiring around alpaca-py — TIF coercion for crypto, the
fractional-via-notional escape hatch, the asset-class round-trip, and the
make_broker / make_source factories — by injecting fakes for the SDK.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from hedger.base import (
    AssetClass,
    Order,
    OrderType,
    Side,
    Symbol,
    TimeInForce,
)
from hedger.execution.brokers import AlpacaBroker, _alpaca_to_asset_class, make_broker


@pytest.fixture
def fake_alpaca_env(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "fake-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "fake-secret")


def _make_broker_with_fake_client(monkeypatch):
    """Patch alpaca.trading.client.TradingClient at import time."""
    fake_client = MagicMock()
    fake_client.submit_order.return_value = SimpleNamespace(id="srv-id-123")
    fake_client.get_account.return_value = SimpleNamespace(equity="50000")
    fake_client.get_all_positions.return_value = []
    fake_client.get_orders.return_value = []
    fake_client.get_clock.return_value = SimpleNamespace(is_open=True)
    fake_module = SimpleNamespace(TradingClient=lambda *a, **k: fake_client)
    monkeypatch.setitem(__import__("sys").modules, "alpaca.trading.client", fake_module)
    broker = AlpacaBroker(api_key="x", secret="y", paper=True)
    return broker, fake_client


def test_alpaca_to_asset_class_mapping():
    assert _alpaca_to_asset_class("us_equity") is AssetClass.EQUITY
    assert _alpaca_to_asset_class("crypto") is AssetClass.CRYPTO
    assert _alpaca_to_asset_class("us_option") is AssetClass.OPTION
    assert _alpaca_to_asset_class("") is AssetClass.EQUITY  # safe default


def test_make_broker_paper_returns_paper_broker():
    from hedger.execution.brokers import PaperBroker
    b = make_broker("paper", price_fn=lambda s: 1.0)
    assert isinstance(b, PaperBroker)


def test_make_broker_alpaca_paper_flag(fake_alpaca_env, monkeypatch):
    fake = SimpleNamespace(TradingClient=lambda *a, **k: MagicMock())
    monkeypatch.setitem(__import__("sys").modules, "alpaca.trading.client", fake)
    b = make_broker("alpaca:paper")
    assert isinstance(b, AlpacaBroker)
    assert b.paper is True
    b2 = make_broker("alpaca:live")
    assert b2.paper is False


def test_alpaca_broker_crypto_coerces_day_tif_to_gtc(fake_alpaca_env, monkeypatch):
    """A crypto order with TIF=DAY must be silently upgraded to GTC."""
    broker, fake_client = _make_broker_with_fake_client(monkeypatch)
    order = Order(
        symbol=Symbol("BTC/USD", AssetClass.CRYPTO),
        side=Side.BUY,
        qty=0.001,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        client_order_id="test-1",
    )
    broker.submit(order)
    submitted_req = fake_client.submit_order.call_args[0][0]
    # alpaca-py's TimeInForce enum value for GTC is "gtc"
    assert str(submitted_req.time_in_force).lower().endswith("gtc")


def test_alpaca_broker_equity_keeps_day_tif(fake_alpaca_env, monkeypatch):
    broker, fake_client = _make_broker_with_fake_client(monkeypatch)
    order = Order(
        symbol=Symbol("SPY", AssetClass.EQUITY),
        side=Side.BUY,
        qty=1.0,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
        client_order_id="test-2",
    )
    broker.submit(order)
    submitted_req = fake_client.submit_order.call_args[0][0]
    assert str(submitted_req.time_in_force).lower().endswith("day")


def test_alpaca_broker_notional_meta_overrides_qty(fake_alpaca_env, monkeypatch):
    """Passing meta['notional'] should produce a notional-sized request."""
    broker, fake_client = _make_broker_with_fake_client(monkeypatch)
    order = Order(
        symbol=Symbol("SPY", AssetClass.EQUITY),
        side=Side.BUY,
        qty=999.0,  # ignored when notional is set
        order_type=OrderType.MARKET,
        client_order_id="test-3",
        meta={"notional": 250.0},
    )
    broker.submit(order)
    req = fake_client.submit_order.call_args[0][0]
    assert getattr(req, "notional", None) == 250.0
    # qty should not be set when notional is used
    assert getattr(req, "qty", None) in (None, "")


def test_alpaca_broker_no_keys_raises(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ALPACA_API_KEY"):
        AlpacaBroker()


def test_check_requirements_paper_only_no_alpaca_check(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    from hedger.util import check_requirements
    missing = check_requirements(broker="paper", llm=True)
    # Without alpaca selected, ALPACA_* keys should not be flagged.
    assert "ALPACA_API_KEY" not in missing
    assert "ANTHROPIC_API_KEY" not in missing


def test_check_requirements_alpaca_flags_missing_keys(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    from hedger.util import check_requirements
    missing = check_requirements(broker="alpaca", llm=False)
    assert "ALPACA_API_KEY" in missing
    assert "ALPACA_SECRET_KEY" in missing


def test_make_source_default_is_alpaca():
    """make_source() with no args should return AlpacaSource (Alpaca-first)."""
    from hedger.data.sources import AlpacaSource, make_source
    src = make_source()  # no args
    assert isinstance(src, AlpacaSource)


def test_make_source_yfinance_still_works():
    from hedger.data.sources import YFinanceSource, make_source
    assert isinstance(make_source("yfinance"), YFinanceSource)


def test_make_source_unknown_spec_raises():
    from hedger.data.sources import make_source
    with pytest.raises(ValueError, match="Unknown source spec"):
        make_source("does_not_exist")


def test_alpaca_broker_fills_uses_watermark(fake_alpaca_env, monkeypatch):
    """Each fills() call should pass an `after=` to GetOrdersRequest."""
    broker, fake_client = _make_broker_with_fake_client(monkeypatch)
    # Capture every call's `after` kwarg.
    captured_after = []

    def capture(filter):
        captured_after.append(getattr(filter, "after", None))
        return []

    fake_client.get_orders.side_effect = capture
    list(broker.fills())  # first call: watermark None -> falls back to ~24h
    list(broker.fills())  # second call: still None (no new fills) -> ~24h
    assert len(captured_after) == 2
    assert all(a is not None for a in captured_after)


def test_fill_stream_watchdog_reconnects_on_death(fake_alpaca_env, monkeypatch):
    """A failing TradingStream.run() must trigger reconnect via on_stream_event."""
    import threading
    import time

    events = []
    event_lock = threading.Lock()
    started_event = threading.Event()
    saw_two_starts = threading.Event()

    class FlakyStream:
        run_count = 0

        def __init__(self, *a, **k):
            pass

        def subscribe_trade_updates(self, h):
            pass

        def run(self):
            FlakyStream.run_count += 1
            if FlakyStream.run_count == 1:
                raise RuntimeError("connection lost")
            # Second run: stay alive long enough for the test to read events.
            time.sleep(0.5)

        def stop(self):
            pass

    from types import SimpleNamespace
    fake_module = SimpleNamespace(TradingStream=FlakyStream)
    monkeypatch.setitem(__import__("sys").modules, "alpaca.trading.stream", fake_module)

    broker, _ = _make_broker_with_fake_client(monkeypatch)

    def on_event(name, ctx):
        with event_lock:
            events.append((name, ctx))
            if name == "started" and len([e for e in events if e[0] == "started"]) >= 2:
                saw_two_starts.set()

    broker.start_fill_stream(on_stream_event=on_event, max_backoff_s=0.05)
    assert saw_two_starts.wait(timeout=3.0), f"watchdog never reconnected; events={events}"
    broker.stop_fill_stream()
    names = [e[0] for e in events]
    assert names.count("started") >= 2
    assert "died" in names
    assert "reconnecting" in names


def test_seed_fill_watermark_advances_only_forward():
    from datetime import datetime, timedelta, timezone
    from hedger.execution.brokers import AlpacaBroker
    # Bypass __post_init__ by constructing via object.__new__
    b = object.__new__(AlpacaBroker)
    b._fill_watermark = None
    early = datetime(2026, 1, 1, tzinfo=timezone.utc)
    late = early + timedelta(days=10)
    AlpacaBroker.seed_fill_watermark(b, late)
    assert b._fill_watermark == late
    AlpacaBroker.seed_fill_watermark(b, early)
    assert b._fill_watermark == late  # earlier seed must NOT regress watermark
