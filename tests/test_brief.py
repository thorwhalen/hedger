"""Tests for the daily reflection brief."""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

from hedger.data.stores import mall, positions_to_snapshot
from hedger.base import AssetClass, Position, Symbol
from hedger.reflection.monitor import daily_brief, write_brief


def _now_iso(offset_h: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=offset_h)).isoformat()


def _populate(m):
    m["signals"][("sma_crossover", "default:SPY", _now_iso(1))] = {
        "ts": _now_iso(1), "score": 1.0, "strategy": "sma_crossover",
        "symbol": "default:SPY",
    }
    m["signals"][("sma_crossover", "default:QQQ", _now_iso(1))] = {
        "ts": _now_iso(1), "score": 0.0, "strategy": "sma_crossover",
        "symbol": "default:QQQ",
    }
    m["signals"][("llm_news", "default:SPY", _now_iso(1))] = {
        "ts": _now_iso(1), "score": 0.5, "strategy": "llm_news",
        "symbol": "default:SPY",
    }
    m["decisions"][("run-1", "default:SPY", _now_iso(0.5))] = {
        "ts": _now_iso(0.5), "symbol": "default:SPY", "target_weight": 0.05,
        "rationale": "test",
    }
    m["orders"][("run-1", "cid-spy")] = {
        "symbol": "default:SPY", "side": "buy", "qty": 10.0,
        "client_order_id": "cid-spy",
    }
    m["orders"][("run-1", "cid-msft-stuck")] = {
        "symbol": "default:MSFT", "side": "buy", "qty": 5.0,
        "client_order_id": "cid-msft-stuck",
    }
    m["fills"][("run-1", "broker-id-1")] = {
        "ts": _now_iso(0.4), "order_id": "broker-id-1", "symbol": "default:SPY",
        "side": "buy", "qty": 10.0, "price": 500.0, "fee": 0.0, "venue": "alpaca",
    }
    m["news"][(_now_iso(2), "n1")] = {
        "id": "n1", "headline": "Markets rally", "summary": "",
        "symbols": ["SPY"], "created_at": _now_iso(2), "url": None, "author": None,
    }
    sym = Symbol("SPY", AssetClass.EQUITY)
    snap = positions_to_snapshot(
        {sym: Position(symbol=sym, qty=10, avg_price=500)},
        nav=100_000.0,
        ts=_now_iso(0),
    )
    m["positions"][(_now_iso(0), "tick:t0")] = snap


def test_brief_counts_and_per_strategy():
    m = mall(tempfile.mkdtemp())
    _populate(m)
    b = daily_brief(m)
    assert b["counts"]["signals"] == 3
    assert b["counts"]["decisions"] == 1
    assert b["counts"]["orders"] == 2
    assert b["counts"]["fills"] == 1
    assert b["counts"]["news"] == 1
    assert b["signals_by_strategy"]["sma_crossover"] == 2
    assert b["signals_by_strategy"]["llm_news"] == 1


def test_brief_per_symbol_rollup():
    m = mall(tempfile.mkdtemp())
    _populate(m)
    b = daily_brief(m)
    spy = b["by_symbol"]["default:SPY"]
    assert spy["signals"] == 2
    assert spy["fills"] == 1
    assert spy["buy_qty"] == 10.0
    assert spy["notional_traded"] == 5000.0


def test_brief_flags_unfilled_orders():
    """An order whose symbol+side has no fill in the window should be flagged."""
    m = mall(tempfile.mkdtemp())
    _populate(m)
    b = daily_brief(m)
    flagged = {u["client_order_id"] for u in b["unfilled_orders"]}
    assert "cid-msft-stuck" in flagged  # MSFT order with no MSFT fill
    assert "cid-spy" not in flagged     # SPY order is matched by SPY fill


def test_brief_includes_latest_positions_snapshot():
    m = mall(tempfile.mkdtemp())
    _populate(m)
    b = daily_brief(m)
    assert b["latest_nav"] == 100_000.0
    assert b["latest_positions"][0]["symbol"] == "default:SPY"


def test_brief_handles_empty_mall_safely():
    m = mall(tempfile.mkdtemp())
    b = daily_brief(m)
    assert b["counts"]["signals"] == 0
    assert b["by_symbol"] == {}
    assert b["unfilled_orders"] == []
    assert b["latest_positions"] == []


def test_brief_includes_position_drifts():
    m = mall(tempfile.mkdtemp())
    _populate(m)
    # Persist a synthetic drift in the window.
    m["drifts"][(_now_iso(0), "tick:t1", "0")] = {
        "symbol": "default:SPY", "prev_qty": 10.0, "broker_qty": 11.0,
        "ts": _now_iso(0), "label": "tick:t1",
    }
    b = daily_brief(m)
    assert len(b["position_drifts"]) == 1
    assert b["position_drifts"][0]["symbol"] == "default:SPY"


def test_write_brief_round_trip():
    import json
    m = mall(tempfile.mkdtemp())
    _populate(m)
    out = write_brief(m, out_dir=tempfile.mkdtemp())
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["counts"]["signals"] == 3


def test_brief_windows_orders_by_tick_in_client_order_id():
    """Orders from long ago are neither counted nor listed as unfilled."""
    m = mall(tempfile.mkdtemp())
    run = "2026-05-07T18:00:57+00:00"
    old_cid = f"{run}:2026-05-07T19:00:57+00:00:default:QQQ"
    m["orders"][(run, old_cid)] = {
        "symbol": "default:QQQ", "side": "buy", "qty": 14.0, "client_order_id": old_cid,
    }
    new_cid = f"{run}:{_now_iso(2)}:default:QQQ"
    m["orders"][(run, new_cid)] = {
        "symbol": "default:QQQ", "side": "sell", "qty": 1.0, "client_order_id": new_cid,
    }
    b = daily_brief(m)
    assert b["counts"]["orders"] == 1
    assert [o["client_order_id"] for o in b["unfilled_orders"]] == [new_cid]


def test_brief_counts_a_twice_journalled_fill_once():
    """A fill re-journalled under a restart's run id is one fill, not two."""
    m = mall(tempfile.mkdtemp())
    row = {
        "ts": _now_iso(1), "order_id": "broker-id-1", "symbol": "default:BTC/USD",
        "side": "buy", "qty": 0.01, "price": 80_000.0, "fee": 0.0, "venue": "alpaca",
    }
    m["fills"][("run-1", "broker-id-1")] = row
    m["fills"][("run-2", "broker-id-1")] = row
    b = daily_brief(m)
    assert b["counts"]["fills"] == 1
    assert b["by_symbol"]["default:BTC/USD"]["fills"] == 1
    assert b["approx_realized_cash_change"] == -800.0
