"""Tests for the France PFU tax policy."""

from __future__ import annotations

from hedger.base import AssetClass, Decision, Fill, Side, Symbol, utc_now
from hedger.tax.policies import FrenchPFUPolicy
from hedger.tax import get_policy


_SYM = Symbol("AAPL", AssetClass.EQUITY)


def _fill(side: Side, qty: float, price: float) -> Fill:
    return Fill(order_id="x", symbol=_SYM, side=side, qty=qty, price=price,
                fee=0.0, ts=utc_now(), venue="alpaca")


def test_default_rate_is_30pct():
    p = FrenchPFUPolicy()
    assert p.rate == 0.30


def test_buy_then_sell_gain_taxed_at_30():
    p = FrenchPFUPolicy()
    p.absorb(_fill(Side.BUY, 10, 100))
    p.absorb(_fill(Side.SELL, 10, 110))
    assert p.realized(_SYM) == 100.0
    assert p.tax_owed(_SYM) == 30.0


def test_loss_does_not_owe_tax():
    p = FrenchPFUPolicy()
    p.absorb(_fill(Side.BUY, 10, 100))
    p.absorb(_fill(Side.SELL, 10, 90))
    assert p.realized(_SYM) == -100.0
    assert p.tax_owed(_SYM) == 0.0  # losses don't owe tax


def test_fifo_order_for_sells():
    """FIFO: oldest lots sold first."""
    p = FrenchPFUPolicy()
    p.absorb(_fill(Side.BUY, 10, 100))   # lot 1: 10 @ 100
    p.absorb(_fill(Side.BUY, 10, 200))   # lot 2: 10 @ 200
    p.absorb(_fill(Side.SELL, 10, 250))  # sells lot 1 (oldest)
    # gain = 10 * (250 - 100) = 1500
    assert p.realized(_SYM) == 1500.0
    # remaining lot is the 200-priced one, untouched
    assert p.lots[_SYM] == [(10, 200)]


def test_partial_sell_consumes_only_what_it_needs():
    p = FrenchPFUPolicy()
    p.absorb(_fill(Side.BUY, 10, 100))
    p.absorb(_fill(Side.SELL, 4, 150))
    # gain = 4 * (150 - 100) = 200
    assert p.realized(_SYM) == 200.0
    assert p.lots[_SYM] == [(6, 100)]


def test_total_tax_owed_sums_positive_gains_only():
    sym2 = Symbol("MSFT", AssetClass.EQUITY)
    p = FrenchPFUPolicy()
    p.absorb(_fill(Side.BUY, 10, 100))
    p.absorb(_fill(Side.SELL, 10, 110))                      # +100 on AAPL
    p.absorb(Fill(order_id="x", symbol=sym2, side=Side.BUY, qty=10,
                  price=100, fee=0, ts=utc_now(), venue="alpaca"))
    p.absorb(Fill(order_id="x", symbol=sym2, side=Side.SELL, qty=10,
                  price=80, fee=0, ts=utc_now(), venue="alpaca"))   # -200 on MSFT
    # Cross-symbol netting is operator's call; per-symbol tax_owed only
    # counts positive gains.
    assert p.tax_owed() == 30.0


def test_policy_does_not_veto_decisions():
    p = FrenchPFUPolicy()
    d = Decision(symbol=_SYM, ts=utc_now(), target_weight=0.05,
                 rationale="test")
    assert p(d, positions={}, history={}) is d


def test_registered_under_fr_pfu():
    p = get_policy("fr_pfu")
    assert isinstance(p, FrenchPFUPolicy)
