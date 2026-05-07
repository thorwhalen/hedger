"""Brokers — same protocol, different homes for the orders.

Two implementations ship:

* :class:`PaperBroker` — fast, deterministic, in-memory. Used by the
  backtester and as a development sandbox; **not** the recommended live
  broker even for paper trading (use ``alpaca:paper`` for that, since it
  exercises the full broker round-trip).

* :class:`AlpacaBroker` — thin wrapper around ``alpaca-py``. Same code path
  for paper and live (toggle with ``paper=True/False``).
"""

from __future__ import annotations

import os
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Optional

from hedger.base import (
    AssetClass,
    Broker,
    Fill,
    Order,
    OrderType,
    Position,
    Side,
    Symbol,
    TimeInForce,
    utc_now,
)


# ---------------------------------------------------------------------------
# PaperBroker — fast, deterministic, fee-aware. Use for backtest AND paper.
# ---------------------------------------------------------------------------

@dataclass
class PaperBroker:
    """In-memory broker that fills market orders against a price oracle.

    `price_fn(symbol) -> float` injects a price source so the same code runs
    in backtest (price = bar close) and live paper (price = latest quote).
    """
    name: str = "paper"
    starting_cash: float = 100_000.0
    fee_bps: float = 5.0           # 0.05% per side, tune to your venue
    slippage_bps: float = 2.0      # 0.02% adverse move on market orders

    cash: float = field(init=False)
    _positions: dict[Symbol, Position] = field(default_factory=dict, init=False)
    _fills: deque[Fill] = field(default_factory=deque, init=False)
    _open_orders: dict[str, Order] = field(default_factory=dict, init=False)
    price_fn: Callable[[Symbol], float] = field(default=lambda s: 0.0)

    def __post_init__(self):
        self.cash = self.starting_cash

    def submit(self, order: Order) -> str:
        order_id = order.client_order_id or f"paper-{uuid.uuid4().hex[:12]}"
        # Only market orders are auto-filled; limit/stop are queued.
        if order.order_type is OrderType.MARKET:
            self._execute(order_id, order)
        else:
            self._open_orders[order_id] = order
        return order_id

    def _execute(self, order_id: str, order: Order) -> None:
        px = self.price_fn(order.symbol)
        if not px:
            raise RuntimeError(f"PaperBroker has no price for {order.symbol}")
        slip = px * self.slippage_bps / 10_000
        fill_px = px + slip if order.side is Side.BUY else px - slip
        fee = abs(order.qty) * fill_px * self.fee_bps / 10_000
        notional = order.qty * fill_px
        self.cash -= notional + fee if order.side is Side.BUY else -notional + fee  # noqa: E501
        # equivalent: cash -= signed_notional + fee
        pos = self._positions.setdefault(order.symbol, Position(symbol=order.symbol))
        fill = Fill(
            order_id=order_id, symbol=order.symbol, side=order.side,
            qty=order.qty, price=fill_px, fee=fee, ts=utc_now(),
            venue=self.name, meta={"slippage_bps": self.slippage_bps},
        )
        pos.apply(fill)
        self._fills.append(fill)

    def cancel(self, order_id: str) -> None:
        self._open_orders.pop(order_id, None)

    def fills(self) -> Iterable[Fill]:
        while self._fills:
            yield self._fills.popleft()

    def positions(self) -> Mapping[Symbol, Position]:
        return dict(self._positions)

    def nav(self) -> float:
        equity = sum(p.qty * (self.price_fn(p.symbol) or p.avg_price)
                     for p in self._positions.values())
        return self.cash + equity


# ---------------------------------------------------------------------------
# AlpacaBroker — thin wrapper around alpaca-py
# ---------------------------------------------------------------------------

# Crypto-only TIFs allowed by Alpaca.
_CRYPTO_TIFS = {TimeInForce.GTC, TimeInForce.IOC, TimeInForce.FOK}


def _alpaca_to_asset_class(raw: str) -> AssetClass:
    """Map Alpaca's asset_class string to our enum (best-effort)."""
    raw = (raw or "").lower()
    if "crypto" in raw:
        return AssetClass.CRYPTO
    if "option" in raw:
        return AssetClass.OPTION
    return AssetClass.EQUITY


@dataclass
class AlpacaBroker:
    """Live or paper Alpaca via the official SDK.

    Pass ``paper=True`` (default) to hit the paper endpoint. Same code is used
    for live trading by flipping the flag and providing live keys.

    Fractional shares are supported transparently — ``Order.qty`` may be a
    non-integer for fractionable assets (Alpaca currently fractionalises most
    US stocks/ETFs and crypto).

    To enable real-time fills, call :meth:`start_fill_stream` after
    construction; otherwise :meth:`fills` polls ``GetOrdersRequest`` for
    recently-filled orders. Polling works for paper trading where market
    orders fill near-instantly; live trading should prefer the stream so
    partial fills are not missed.
    """
    name: str = "alpaca"
    paper: bool = True
    api_key: str | None = None
    secret: str | None = None

    _client: object = field(init=False, default=None)
    _seen_fills: set[str] = field(default_factory=set, init=False)
    _fill_watermark: Optional[datetime] = field(default=None, init=False)
    _streamed_fills: deque[Fill] = field(default_factory=deque, init=False)
    _stream_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _stream_thread: Optional[threading.Thread] = field(default=None, init=False)
    _stream_obj: object = field(default=None, init=False)
    _stream_stopping: bool = field(default=False, init=False)
    _stream_restart_count: int = field(default=0, init=False)

    def __post_init__(self):
        try:
            from alpaca.trading.client import TradingClient
        except ImportError as e:
            raise ImportError("`pip install alpaca-py` for AlpacaBroker.") from e
        key = self.api_key or os.environ.get("ALPACA_API_KEY")
        sec = self.secret or os.environ.get("ALPACA_SECRET_KEY")
        if not (key and sec):
            raise RuntimeError(
                "AlpacaBroker needs ALPACA_API_KEY and ALPACA_SECRET_KEY."
            )
        self.api_key = key
        self.secret = sec
        self._client = TradingClient(key, sec, paper=self.paper)

    # -- order submission -----------------------------------------------------

    def submit(self, order: Order) -> str:
        """Submit an order. Returns the broker order id (string).

        Crypto venues only accept ``gtc``/``ioc``/``fok`` time-in-force; if the
        caller passes ``DAY`` for a crypto order, we silently coerce to
        ``GTC`` rather than failing — this is the canonical Alpaca workaround
        and matches the documented gotcha.
        """
        from alpaca.trading.enums import OrderSide, TimeInForce as AlpacaTif
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        side = OrderSide.BUY if order.side is Side.BUY else OrderSide.SELL
        tif_value = order.time_in_force
        is_crypto = (
            order.symbol.asset_class is AssetClass.CRYPTO
            or "/" in order.symbol.ticker
        )
        if is_crypto and tif_value not in _CRYPTO_TIFS:
            tif_value = TimeInForce.GTC
        tif = AlpacaTif(tif_value.value)

        # Allow callers to pass dollar notional via order.meta['notional'] for
        # fractional dollar-sized orders. Otherwise qty wins.
        notional = (order.meta or {}).get("notional")
        common = {
            "symbol": order.symbol.ticker,
            "side": side,
            "time_in_force": tif,
            "client_order_id": order.client_order_id,
        }
        if order.order_type is OrderType.MARKET:
            if notional:
                req = MarketOrderRequest(notional=float(notional), **common)
            else:
                req = MarketOrderRequest(qty=order.qty, **common)
        else:
            req = LimitOrderRequest(
                qty=order.qty, limit_price=order.limit_price, **common,
            )
        resp = self._client.submit_order(req)
        return str(resp.id)

    def cancel(self, order_id: str) -> None:
        self._client.cancel_order_by_id(order_id)

    # -- fill reconciliation --------------------------------------------------

    def fills(self) -> Iterable[Fill]:
        """Yield fills observed since the last call.

        Drains the streaming queue first (if a stream was started), then polls
        ``GetOrdersRequest(after=watermark)`` for anything missed. The
        watermark advances to the most recent ``filled_at`` we've seen, so
        steady-state polls are O(new fills) rather than O(200). Both paths
        dedupe via :attr:`_seen_fills` so calling repeatedly is safe.

        On a fresh broker (no watermark yet) the first poll falls back to a
        24-hour lookback — long enough to recover from a process restart, short
        enough that a busy account isn't penalised.
        """
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        # Streamed fills first (cheap).
        with self._stream_lock:
            while self._streamed_fills:
                f = self._streamed_fills.popleft()
                if f.order_id in self._seen_fills:
                    continue
                self._seen_fills.add(f.order_id)
                if f.ts and (self._fill_watermark is None
                             or f.ts > self._fill_watermark):
                    self._fill_watermark = f.ts
                yield f

        # Poll recent closed orders for anything we missed.
        if self._fill_watermark is not None:
            # Tiny overlap protects against the boundary including a fill
            # that was already partially processed.
            after = self._fill_watermark - timedelta(seconds=1)
        else:
            after = utc_now() - timedelta(days=1)
        req = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED, limit=200, after=after,
        )
        for o in self._client.get_orders(filter=req):
            if not o.filled_qty or float(o.filled_qty) <= 0:
                continue
            if str(o.id) in self._seen_fills:
                continue
            self._seen_fills.add(str(o.id))
            ts = o.filled_at or utc_now()
            if self._fill_watermark is None or ts > self._fill_watermark:
                self._fill_watermark = ts
            yield Fill(
                order_id=str(o.id),
                symbol=Symbol(
                    ticker=o.symbol,
                    asset_class=_alpaca_to_asset_class(getattr(o, "asset_class", "")),
                ),
                side=Side.BUY if str(o.side).lower().endswith("buy") else Side.SELL,
                qty=float(o.filled_qty),
                price=float(o.filled_avg_price or 0.0),
                fee=0.0,  # Alpaca is commission-free for equities/ETF
                ts=ts,
                venue=self.name,
            )

    def seed_fill_watermark(self, watermark: datetime) -> None:
        """Set the watermark from outside (e.g. mall["fills"] on restart)."""
        if self._fill_watermark is None or watermark > self._fill_watermark:
            self._fill_watermark = watermark

    def start_fill_stream(
        self,
        *,
        on_stream_event: Optional[Callable[[str, dict], None]] = None,
        max_backoff_s: float = 60.0,
    ) -> None:
        """Spin up a background TradingStream with auto-reconnect.

        The stream subscribes to trade updates and pushes Fills into a
        thread-safe queue (see :meth:`fills`). If the connection drops,
        the watchdog rebuilds the stream and re-subscribes after an
        exponential backoff (capped at ``max_backoff_s``).

        ``on_stream_event(event_name, context)`` is called for lifecycle
        events ('started', 'died', 'reconnecting'); the runner uses this to
        notify humans. Per-fill events go through the normal fills() path.

        Calling twice is a no-op if the previous thread is still alive.
        Polling via :meth:`fills` continues to work if the stream is dead.
        """
        if self._stream_thread is not None and self._stream_thread.is_alive():
            return
        try:
            from alpaca.trading.stream import TradingStream
        except ImportError:
            return
        import time as _time

        self._stream_stopping = False

        async def handler(data):  # pragma: no cover — needs live socket
            ev = getattr(data, "event", None)
            if ev not in ("fill", "partial_fill"):
                return
            order = getattr(data, "order", None)
            if order is None:
                return
            sym = Symbol(
                ticker=order.symbol,
                asset_class=_alpaca_to_asset_class(getattr(order, "asset_class", "")),
            )
            fill = Fill(
                order_id=str(order.id),
                symbol=sym,
                side=Side.BUY if str(order.side).lower().endswith("buy") else Side.SELL,
                qty=float(order.filled_qty or 0.0),
                price=float(order.filled_avg_price or 0.0),
                fee=0.0,
                ts=getattr(order, "filled_at", None) or utc_now(),
                venue=self.name,
                meta={"event": ev},
            )
            with self._stream_lock:
                self._streamed_fills.append(fill)

        def _runner():  # pragma: no cover — runs forever in background
            backoff = 1.0
            while not self._stream_stopping:
                try:
                    stream = TradingStream(self.api_key, self.secret,
                                           paper=self.paper)
                    self._stream_obj = stream
                    stream.subscribe_trade_updates(handler)
                    if on_stream_event:
                        on_stream_event("started", {})
                    backoff = 1.0
                    stream.run()
                    # Clean exit only happens if stop() was called.
                    if self._stream_stopping:
                        break
                except Exception as e:
                    if on_stream_event:
                        on_stream_event("died", {
                            "error": f"{type(e).__name__}: {e}",
                            "restart_count": self._stream_restart_count,
                        })
                if self._stream_stopping:
                    break
                _time.sleep(min(backoff, max_backoff_s))
                backoff = min(backoff * 2, max_backoff_s)
                self._stream_restart_count += 1
                if on_stream_event:
                    on_stream_event("reconnecting", {
                        "restart_count": self._stream_restart_count,
                        "backoff_s": backoff,
                    })

        t = threading.Thread(target=_runner, name="alpaca-fill-stream", daemon=True)
        t.start()
        self._stream_thread = t

    def stop_fill_stream(self) -> None:  # pragma: no cover — called on shutdown
        self._stream_stopping = True
        if self._stream_obj is None:
            return
        try:
            self._stream_obj.stop()
        except Exception:
            pass
        self._stream_obj = None
        self._stream_thread = None

    # -- account state --------------------------------------------------------

    def positions(self) -> Mapping[Symbol, Position]:
        out: dict[Symbol, Position] = {}
        for p in self._client.get_all_positions():
            sym = Symbol(
                ticker=p.symbol,
                asset_class=_alpaca_to_asset_class(getattr(p, "asset_class", "")),
            )
            out[sym] = Position(
                symbol=sym, qty=float(p.qty), avg_price=float(p.avg_entry_price),
            )
        return out

    def nav(self) -> float:
        acc = self._client.get_account()
        return float(acc.equity)

    def is_market_open(self) -> bool:
        """Convenience: True iff the equity market is currently open per Alpaca."""
        try:
            return bool(self._client.get_clock().is_open)
        except Exception:
            return True  # fail-open: better than vetoing every tick on a glitch


def make_broker(spec: str = "alpaca:paper", **kwargs) -> Broker:
    """Factory: 'paper' | 'alpaca' | 'alpaca:paper' | 'alpaca:live' -> Broker.

    Default is ``alpaca:paper`` (the recommended starting target). ``paper``
    (no colon) returns the in-memory :class:`PaperBroker` used by the
    backtester.

    >>> isinstance(make_broker('paper', price_fn=lambda s: 1.0), PaperBroker)
    True
    """
    if spec == "paper":
        return PaperBroker(**kwargs)
    if spec.startswith("alpaca"):
        paper = ":live" not in spec
        return AlpacaBroker(paper=paper, **kwargs)
    raise ValueError(f"Unknown broker spec: {spec!r}")


__all__ = ["PaperBroker", "AlpacaBroker", "make_broker"]
