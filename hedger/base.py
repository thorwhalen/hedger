"""Foundational types for hedger.

This module is the **SSOT for the data shapes** that flow through the pipeline:

    Bar -> Features -> Signal -> Decision -> Order -> Fill -> Position

Everything else in hedger is composed against these shapes. If a strategy returns
a Signal, any backtester or live runner can consume it; if a broker returns a
Fill, any portfolio tracker can absorb it. The pipeline is a sequence of pure
functions over these dataclasses, with stores (Mappings) and middleware
(decorators) handling side effects and policy.

Doctests are minimal on purpose; complex flows are tested in tests/.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any,
    Callable,
    Iterable,
    Mapping,
    MutableMapping,
    Protocol,
    runtime_checkable,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Side(str, Enum):
    """Direction of an order or position."""
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    """Subset of order types we expose. Brokers may support more."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"
    FOK = "fok"


class AssetClass(str, Enum):
    EQUITY = "equity"
    ETF = "etf"
    CRYPTO = "crypto"
    FX = "fx"
    OPTION = "option"


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Symbol:
    """A tradable instrument key.

    >>> s = Symbol(ticker='BTC/USD', asset_class=AssetClass.CRYPTO, venue='coinbase')
    >>> str(s)
    'coinbase:BTC/USD'
    """
    ticker: str
    asset_class: AssetClass
    venue: str = "default"

    def __str__(self) -> str:
        return f"{self.venue}:{self.ticker}"


# ---------------------------------------------------------------------------
# Pipeline payloads
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Bar:
    """OHLCV bar at a fixed cadence.

    >>> b = Bar(symbol=Symbol('AAPL', AssetClass.EQUITY), ts=datetime(2026, 1, 2),
    ...         open=180.0, high=182.0, low=179.5, close=181.5, volume=1_000_000)
    >>> b.close
    181.5
    """
    symbol: Symbol
    ts: datetime  # bar close timestamp, tz-aware
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class Signal:
    """A strategy's view on a symbol at a moment in time.

    `score` is in [-1, 1]; sign = direction, magnitude = conviction.
    `meta` carries strategy-specific provenance (which features fired, LLM
    rationale, model version, …) so the reflection loop can audit decisions.
    """
    symbol: Symbol
    ts: datetime
    score: float
    strategy: str
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Decision:
    """A position-sizing decision derived from one or more Signals.

    Sized in *target weight* (fraction of portfolio NAV). The execution layer
    converts weight deltas to orders given current positions and prices.
    """
    symbol: Symbol
    ts: datetime
    target_weight: float
    rationale: str
    risk_budget: float = 1.0  # 0..1, scales position size at risk-mgmt time
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Order:
    """An order the bot wants the broker to place."""
    symbol: Symbol
    side: Side
    qty: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    client_order_id: str | None = None  # for idempotency
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Fill:
    """Confirmation that part or all of an order executed."""
    order_id: str
    symbol: Symbol
    side: Side
    qty: float
    price: float
    fee: float
    ts: datetime
    venue: str
    meta: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Position:
    """Current holding in a symbol. Mutable because it accumulates fills."""
    symbol: Symbol
    qty: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    def apply(self, fill: Fill) -> None:
        """Absorb a fill into the position. Lots-tracking lives in tax/policies."""
        signed = fill.qty if fill.side is Side.BUY else -fill.qty
        new_qty = self.qty + signed
        if self.qty * new_qty < 0 or self.qty == 0:  # crossing zero or new
            self.realized_pnl += signed * (self.avg_price - fill.price) if self.qty else 0.0
            self.avg_price = fill.price
        elif abs(new_qty) > abs(self.qty):  # adding to position
            self.avg_price = (
                self.avg_price * self.qty + fill.price * signed
            ) / new_qty
        else:  # reducing
            self.realized_pnl += -signed * (fill.price - self.avg_price)
        self.qty = new_qty


# ---------------------------------------------------------------------------
# Protocols (the seams of the system)
# ---------------------------------------------------------------------------

@runtime_checkable
class DataSource(Protocol):
    """Anything that can yield Bars for a symbol/timeframe.

    Implementations: AlpacaSource, CCXTSource, YFinanceSource, ParquetSource.
    """

    def bars(
        self,
        symbol: Symbol,
        *,
        start: datetime,
        end: datetime,
        timeframe: str,  # '1m', '5m', '1h', '1d'
    ) -> Iterable[Bar]: ...


@runtime_checkable
class Strategy(Protocol):
    """A strategy maps a window of Bars (and any context) to Signals.

    Strategies are *pure* over their inputs; state lives in the runner/store.
    A strategy returning an empty iterable means "no opinion right now".
    """

    name: str

    def __call__(
        self,
        bars: Mapping[Symbol, Iterable[Bar]],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> Iterable[Signal]: ...


@runtime_checkable
class Sizer(Protocol):
    """Turns Signals into Decisions (target weights). Pluggable per portfolio."""

    def __call__(
        self,
        signals: Iterable[Signal],
        *,
        positions: Mapping[Symbol, Position],
        nav: float,
    ) -> Iterable[Decision]: ...


@runtime_checkable
class Broker(Protocol):
    """Anything that can place orders and report fills/positions/NAV.

    The same Strategy code runs in backtest, paper, and live by swapping the
    Broker. PaperBroker simulates fills against a DataSource; AlpacaBroker
    forwards to the real Alpaca API.
    """

    name: str

    def submit(self, order: Order) -> str: ...     # returns broker order id
    def cancel(self, order_id: str) -> None: ...
    def fills(self) -> Iterable[Fill]: ...         # since last call
    def positions(self) -> Mapping[Symbol, Position]: ...
    def nav(self) -> float: ...


@runtime_checkable
class TaxPolicy(Protocol):
    """Jurisdiction-specific tax rules. Veto or annotate decisions.

    Returns the same Decision (possibly with adjusted size or extra meta), or
    None if the decision should be skipped (e.g. would trigger a wash sale).
    """

    name: str

    def __call__(
        self,
        decision: Decision,
        *,
        positions: Mapping[Symbol, Position],
        history: Mapping[Symbol, Iterable[Fill]],
    ) -> Decision | None: ...


# Middleware: a Decision -> Decision | None pipeline. Risk, tax, compliance.
DecisionMiddleware = Callable[[Decision], "Decision | None"]


# ---------------------------------------------------------------------------
# Store key conventions
# ---------------------------------------------------------------------------
# Stores are Mappings keyed by tuples for natural slicing. See data/stores.py.
# Conventions:
#   bars:       (symbol_str, timeframe, ts_iso)         -> Bar
#   signals:    (strategy, symbol_str, ts_iso)          -> Signal
#   decisions:  (run_id, symbol_str, ts_iso)            -> Decision
#   orders:     (run_id, client_order_id)               -> Order
#   fills:      (run_id, broker_order_id)               -> Fill
#   reflections:(date_iso, topic)                       -> str (markdown)


def utc_now() -> datetime:
    """Return tz-aware UTC now. Don't use naive datetimes anywhere in hedger."""
    return datetime.now(tz=timezone.utc)


__all__ = [
    "Side",
    "OrderType",
    "TimeInForce",
    "AssetClass",
    "Symbol",
    "Bar",
    "Signal",
    "Decision",
    "Order",
    "Fill",
    "Position",
    "DataSource",
    "Strategy",
    "Sizer",
    "Broker",
    "TaxPolicy",
    "DecisionMiddleware",
    "utc_now",
]
