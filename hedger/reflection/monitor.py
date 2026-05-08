"""Reflection monitor: read the mall, summarise the day, save a brief.

The brief is what gets handed to Claude Code as context for the overnight
reflection session. Keep it small (~few KB) so the agent has a clean view.

Design contract (do not break without updating REFLECT_PROMPT in the
orchestrator and the reflection-cycle skill):

  {
    "window": {"start": iso, "end": iso},
    "counts": {"signals": int, "decisions": int, "fills": int,
               "orders": int, "news": int},
    "signals_by_strategy": {strategy_name: count},
    "by_symbol": {
        "SYM": {"signals": int, "fills": int, "buy_qty": float,
                "sell_qty": float, "notional_traded": float}
    },
    "approx_realized_cash_change": float,
    "unfilled_orders":  [{...}],   # orders with no matching fill in window
    "position_drifts":  [{...}],   # recent reconciliation drifts
    "latest_positions": [...],     # last positions snapshot, if any
    "fills_sample":     [...],
    "decisions_sample": [...],
    "recent_news":      [...]
  }
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping


def _coerce_ts(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _in_window(record_ts: str | None, start: datetime, end: datetime) -> bool:
    if not record_ts:
        return False
    ts = _coerce_ts(record_ts)
    return bool(ts and start <= ts <= end)


def _safe_values(mall: Mapping, slot: str):
    """Return mall[slot].values() or () if the slot is missing/unreadable."""
    try:
        return list(mall[slot].values())
    except (KeyError, TypeError, AttributeError):
        return []


def _safe_items(mall: Mapping, slot: str):
    try:
        return list(mall[slot].items())
    except (KeyError, TypeError, AttributeError):
        return []


def daily_brief(mall: Mapping, *, day: datetime | None = None) -> dict:
    """Compute a human-readable brief of the last 24h of activity.

    The agent uses this to decide what to investigate; the schema is
    intentionally minimal and stable so prompts don't break on changes.
    """
    end = day or datetime.now(tz=timezone.utc)
    start = end - timedelta(hours=24)

    signals = [v for v in _safe_values(mall, "signals") if _in_window(v.get("ts"), start, end)]
    decisions = [v for v in _safe_values(mall, "decisions") if _in_window(v.get("ts"), start, end)]
    orders = [v for v in _safe_values(mall, "orders")]  # orders may not have a top-level ts
    fills = [v for v in _safe_values(mall, "fills") if _in_window(v.get("ts"), start, end)]
    news = [v for v in _safe_values(mall, "news") if _in_window(v.get("created_at"), start, end)]

    # Realised cash change (rough: doesn't track lots).
    realized = 0.0
    by_sym: dict[str, list[dict]] = {}
    for f in fills:
        by_sym.setdefault(f["symbol"], []).append(f)
    for sym, fs in by_sym.items():
        fs = sorted(fs, key=lambda x: x["ts"])
        for f in fs:
            sign = 1 if f["side"] == "buy" else -1
            realized -= sign * f["qty"] * f["price"]
            realized -= f["fee"]

    by_strategy: dict[str, int] = {}
    for s in signals:
        by_strategy[s["strategy"]] = by_strategy.get(s["strategy"], 0) + 1

    # Per-symbol activity rollup.
    by_symbol_stats: dict[str, dict] = {}
    for s in signals:
        d = by_symbol_stats.setdefault(s["symbol"], _empty_symbol_stats())
        d["signals"] += 1
    for fl in fills:
        d = by_symbol_stats.setdefault(fl["symbol"], _empty_symbol_stats())
        d["fills"] += 1
        notional = float(fl["qty"]) * float(fl["price"])
        d["notional_traded"] += notional
        if fl["side"] == "buy":
            d["buy_qty"] += float(fl["qty"])
        else:
            d["sell_qty"] += float(fl["qty"])

    # Unfilled-order anomaly: orders we logged but never saw a fill for.
    # We match by client_order_id substring against fill.order_id (Alpaca
    # exposes a different broker id from our client_order_id; stale-fill
    # detection here is a heuristic, not a guarantee).
    fill_order_ids: set[str] = {f["order_id"] for f in fills}
    fill_symbols_recent: set[tuple[str, str]] = {(f["symbol"], f["side"]) for f in fills}
    unfilled: list[dict] = []
    for ord_dict in orders:
        # Orders are keyed by (run_id, client_order_id); the value is the
        # serialised Order dict. We only flag unfilled where:
        # (a) the order's client_order_id is in our window (best-effort), and
        # (b) we have no fill for the same symbol+side within the window.
        if (ord_dict["symbol"], ord_dict["side"]) not in fill_symbols_recent:
            unfilled.append(
                {
                    "symbol": ord_dict["symbol"],
                    "side": ord_dict["side"],
                    "qty": ord_dict.get("qty"),
                    "client_order_id": ord_dict.get("client_order_id"),
                }
            )

    # Recent reconciliation drift events (from mall["drifts"]).
    position_drifts: list[dict] = [
        v
        for v in _safe_values(mall, "drifts")
        if isinstance(v, dict) and _in_window(v.get("ts"), start, end)
    ]
    latest_snapshot = _latest_positions_snapshot(mall)

    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "counts": {
            "signals": len(signals),
            "decisions": len(decisions),
            "orders": len(orders),
            "fills": len(fills),
            "news": len(news),
        },
        "signals_by_strategy": by_strategy,
        "by_symbol": by_symbol_stats,
        "approx_realized_cash_change": round(realized, 4),
        "unfilled_orders": unfilled[:20],
        "position_drifts": position_drifts[:20],
        "latest_positions": (latest_snapshot or {}).get("positions", []),
        "latest_nav": (latest_snapshot or {}).get("nav"),
        "fills_sample": fills[:20],
        "decisions_sample": decisions[:20],
        "recent_news": [
            {
                "headline": n.get("headline", ""),
                "symbols": n.get("symbols", []),
                "created_at": n.get("created_at"),
            }
            for n in news[:20]
        ],
    }


def _empty_symbol_stats() -> dict:
    return {"signals": 0, "fills": 0, "buy_qty": 0.0, "sell_qty": 0.0, "notional_traded": 0.0}


def _latest_positions_snapshot(mall: Mapping) -> dict | None:
    try:
        store = mall["positions"]
    except (KeyError, TypeError):
        return None
    keys = []
    try:
        keys = list(store)
    except Exception:
        return None
    if not keys:
        return None
    latest = max(keys, key=lambda k: k[0] if isinstance(k, tuple) else str(k))
    try:
        return store[latest]
    except KeyError:
        return None


def write_brief(mall: Mapping, *, out_dir: str | Path | None = None) -> Path:
    """Write today's brief to disk and return the path.

    Default ``out_dir`` is the user's hedger state directory under ``briefs/``.
    Override by passing an explicit path or via ``HEDGER_STATE_DIR``.
    """
    if out_dir is None:
        from hedger._paths import state_dir

        out_dir = state_dir() / "briefs"
    p = Path(out_dir)
    p.mkdir(parents=True, exist_ok=True)
    brief = daily_brief(mall)
    out = p / f"brief-{datetime.now(tz=timezone.utc).date().isoformat()}.json"
    out.write_text(json.dumps(brief, indent=2, default=str))
    return out


__all__ = ["daily_brief", "write_brief"]
