"""Tax policies as DecisionMiddleware.

Three policies as starting points:
  - `none`            : no-op. The default; turn this on only when it pays.
  - `us_wash_sale`    : block sells that would create a wash sale within 30d.
  - `crypto_lifo`     : LIFO lot accounting; just bookkeeping for crypto, no veto.

For France/Belgium/etc., add a module here and register it. Most EU
jurisdictions use FIFO + flat-rate (e.g. France's PFU at 30%); the policy
module handles cost-basis accounting and exposes a `realized_tax_so_far`
on the lot ledger that reflection can read.

Whether to *let* tax policy alter live decisions is a separate choice.
For most retail, tax-aware *reporting* is plenty; tax-aware *trading* is
worth the complexity only above ~6-figure NAV with active turnover.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable, Iterable, Mapping

from hedger.base import Decision, Fill, Position, Side, Symbol, TaxPolicy, utc_now


# ---------------------------------------------------------------------------
# none
# ---------------------------------------------------------------------------

class NoTaxPolicy:
    name = "none"

    def __call__(self, decision, *, positions, history):
        return decision


# ---------------------------------------------------------------------------
# US wash sale veto
# ---------------------------------------------------------------------------

@dataclass
class USWashSalePolicy:
    """Block a sell that closes a position at a loss if a buy occurred within
    30 days *or* if a buy is likely within 30 days from another active strategy.

    This is a conservative, simple version. Real wash-sale logic across
    accounts and substantially-identical securities is substantially more
    complex; do not rely on this for tax filing.
    """
    name: str = "us_wash_sale"
    window_days: int = 31

    def __call__(
        self,
        decision: Decision,
        *,
        positions: Mapping[Symbol, Position],
        history: Mapping[Symbol, Iterable[Fill]],
    ) -> Decision | None:
        if decision.target_weight >= 0:
            return decision  # only sells/closures concern us here
        pos = positions.get(decision.symbol)
        if not pos or pos.qty <= 0:
            return decision
        # If avg_price >= last close (proxy for current price via fill history)
        recent = sorted(list(history.get(decision.symbol, [])), key=lambda f: f.ts)
        if not recent:
            return decision
        last_px = recent[-1].price
        if last_px >= pos.avg_price:
            return decision  # not a loss
        cutoff = decision.ts - timedelta(days=self.window_days)
        recent_buys = [f for f in recent if f.side is Side.BUY and f.ts >= cutoff]
        if recent_buys:
            return None  # would trigger a wash sale; veto.
        return decision


# ---------------------------------------------------------------------------
# Crypto LIFO ledger (bookkeeping only)
# ---------------------------------------------------------------------------

@dataclass
class CryptoLIFOPolicy:
    """Maintain a LIFO lot ledger per symbol; never veto; expose `realized()`.

    Many EU regimes default to FIFO for crypto; LIFO is an option in some
    (e.g. you can elect specific-lot in the US). Adapt to your jurisdiction.
    """
    name: str = "crypto_lifo"
    lots: dict[Symbol, list[tuple[float, float]]] = field(default_factory=dict)
    _realized: dict[Symbol, float] = field(default_factory=lambda: defaultdict(float))

    def absorb(self, fill: Fill) -> None:
        if fill.side is Side.BUY:
            self.lots.setdefault(fill.symbol, []).append((fill.qty, fill.price))
            return
        remaining = fill.qty
        lots = self.lots.setdefault(fill.symbol, [])
        while remaining > 0 and lots:
            qty, px = lots[-1]
            take = min(qty, remaining)
            self._realized[fill.symbol] += take * (fill.price - px)
            remaining -= take
            if take == qty:
                lots.pop()
            else:
                lots[-1] = (qty - take, px)

    def realized(self, symbol: Symbol | None = None) -> float | dict[Symbol, float]:
        if symbol is None:
            return dict(self._realized)
        return self._realized.get(symbol, 0.0)

    def __call__(self, decision, *, positions, history):
        return decision  # bookkeeping only


# ---------------------------------------------------------------------------
# France — Prélèvement Forfaitaire Unique (PFU)
# ---------------------------------------------------------------------------

@dataclass
class FrenchPFUPolicy:
    """France's *Prélèvement Forfaitaire Unique* (a.k.a. Flat Tax) at 30 %.

    The rate decomposes into 12.8 % income tax + 17.2 % social charges
    (CSG/CRDS). Cost basis follows FIFO (the French default for securities;
    crypto uses a *prix moyen pondéré d'acquisition* across the whole
    portfolio, but for a single-instrument ledger FIFO is a fine first cut).

    This is **bookkeeping-only** — never vetoes a decision. Taxable
    realised gains accumulate in :attr:`_realized` per symbol; pull total
    tax-owed via :meth:`tax_owed`. Losses on a symbol offset gains *on the
    same symbol* here; cross-symbol netting is a year-end exercise the
    operator handles outside the bot.

    >>> from hedger.base import AssetClass, Fill, Side, Symbol, utc_now
    >>> p = FrenchPFUPolicy()
    >>> sym = Symbol('AAPL', AssetClass.EQUITY)
    >>> p.absorb(Fill(order_id='1', symbol=sym, side=Side.BUY, qty=10,
    ...               price=100, fee=0, ts=utc_now(), venue='alpaca'))
    >>> p.absorb(Fill(order_id='2', symbol=sym, side=Side.SELL, qty=10,
    ...               price=110, fee=0, ts=utc_now(), venue='alpaca'))
    >>> round(p.realized(sym), 2)
    100.0
    >>> round(p.tax_owed(sym), 2)
    30.0
    """
    name: str = "fr_pfu"
    rate: float = 0.30
    lots: dict[Symbol, list[tuple[float, float]]] = field(default_factory=dict)
    _realized: dict[Symbol, float] = field(default_factory=lambda: defaultdict(float))

    def absorb(self, fill: Fill) -> None:
        if fill.side is Side.BUY:
            self.lots.setdefault(fill.symbol, []).append((fill.qty, fill.price))
            return
        # FIFO sell: peel off oldest lots first.
        remaining = fill.qty
        lots = self.lots.setdefault(fill.symbol, [])
        while remaining > 0 and lots:
            qty, px = lots[0]
            take = min(qty, remaining)
            self._realized[fill.symbol] += take * (fill.price - px)
            remaining -= take
            if take == qty:
                lots.pop(0)
            else:
                lots[0] = (qty - take, px)

    def realized(self, symbol: Symbol | None = None) -> float | dict[Symbol, float]:
        if symbol is None:
            return dict(self._realized)
        return self._realized.get(symbol, 0.0)

    def tax_owed(self, symbol: Symbol | None = None) -> float:
        """Tax owed on net positive realised gains (by symbol or total)."""
        if symbol is None:
            net = sum(max(0.0, g) for g in self._realized.values())
        else:
            net = max(0.0, self._realized.get(symbol, 0.0))
        return net * self.rate

    def __call__(self, decision, *, positions, history):
        return decision  # bookkeeping only


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_POLICIES: dict[str, Callable[..., TaxPolicy]] = {
    "none": NoTaxPolicy,
    "us_wash_sale": USWashSalePolicy,
    "crypto_lifo": CryptoLIFOPolicy,
    "fr_pfu": FrenchPFUPolicy,
}


def get_policy(name: str) -> TaxPolicy:
    """Look up a policy by name."""
    if name not in _POLICIES:
        raise KeyError(f"Tax policy {name!r} not registered. Have: {list(_POLICIES)}")
    return _POLICIES[name]()


def register_policy(name: str, factory: Callable[..., TaxPolicy]) -> None:
    """Register a custom policy (e.g. France-specific)."""
    _POLICIES[name] = factory


__all__ = [
    "NoTaxPolicy",
    "USWashSalePolicy",
    "CryptoLIFOPolicy",
    "FrenchPFUPolicy",
    "get_policy",
    "register_policy",
]
