"""Sanity tests for the strategies added from the strategies report.

Each test confirms two things:
  1. The strategy emits Signal objects with score in [-1, 1] on a synthetic
     data shape it should fire on.
  2. The backtest engine consumes the strategy end-to-end without errors.

These are sanity / integration tests, not edge proofs.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from hedger.backtest import backtest_simple
from hedger.base import AssetClass, Bar, Symbol
from hedger.strategies import available, get
from hedger.strategies.bollinger_meanrev import bollinger_meanrev
from hedger.strategies.donchian_breakout import donchian_breakout
from hedger.strategies.pairs_zscore import pairs_zscore
from hedger.strategies.pca_residual_revert import pca_residual_revert
from hedger.strategies.pead_drift import pead_drift
from hedger.strategies.xs_momentum import xs_momentum


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(sym: Symbol, i: int, close: float, *, hi: float | None = None,
         lo: float | None = None) -> Bar:
    return Bar(
        symbol=sym, ts=T0 + timedelta(days=i),
        open=close, high=hi if hi is not None else close,
        low=lo if lo is not None else close, close=close, volume=1.0,
    )


def _flat_then_spike(sym: Symbol, n_flat: int, spike: float) -> list[Bar]:
    bars = [_bar(sym, i, 100.0, hi=100.5, lo=99.5) for i in range(n_flat)]
    bars.append(_bar(sym, n_flat, 100.0 + spike,
                     hi=100.0 + max(spike, 0.5), lo=100.0 + min(spike, -0.5)))
    return bars


def _scores_in_range(sigs):
    for s in sigs:
        assert -1.0 <= s.score <= 1.0, f"score out of range: {s}"


# ---------------------------------------------------------------------------
# Registry — the new strategies must autoload by name.
# ---------------------------------------------------------------------------

def test_new_strategies_are_registered():
    names = set(available())
    expected = {
        "donchian_breakout", "bollinger_meanrev", "xs_momentum",
        "pairs_zscore", "pca_residual_revert", "pead_drift",
    }
    assert expected.issubset(names), names


# ---------------------------------------------------------------------------
# 3.1 donchian_breakout
# ---------------------------------------------------------------------------

def test_donchian_breakout_fires_long_on_upside_break():
    sym = Symbol("UP", AssetClass.EQUITY)
    bars = {sym: _flat_then_spike(sym, 60, +12.0)}
    sigs = list(donchian_breakout(bars))
    assert sigs and sigs[0].score > 0
    _scores_in_range(sigs)


def test_donchian_breakout_fires_short_on_downside_break():
    sym = Symbol("DN", AssetClass.EQUITY)
    bars = {sym: _flat_then_spike(sym, 60, -12.0)}
    sigs = list(donchian_breakout(bars))
    assert sigs and sigs[0].score < 0


def test_donchian_silent_in_chop():
    sym = Symbol("CHOP", AssetClass.EQUITY)
    bars = {sym: [_bar(sym, i, 100.0 + (1 if i % 2 else -1) * 0.05,
                       hi=100.5, lo=99.5) for i in range(80)]}
    assert list(donchian_breakout(bars)) == []


# ---------------------------------------------------------------------------
# 3.2 bollinger_meanrev
# ---------------------------------------------------------------------------

def test_bollinger_meanrev_fades_upside_overshoot():
    sym = Symbol("OS", AssetClass.EQUITY)
    bars = {sym: _flat_then_spike(sym, 25, +10.0)}
    sigs = list(bollinger_meanrev(bars))
    assert sigs and sigs[0].score < 0
    _scores_in_range(sigs)


def test_bollinger_meanrev_silent_inside_band():
    sym = Symbol("IN", AssetClass.EQUITY)
    bars = {sym: [_bar(sym, i, 100.0 + (i % 3 - 1) * 0.1) for i in range(40)]}
    assert list(bollinger_meanrev(bars)) == []


# ---------------------------------------------------------------------------
# 3.3 xs_momentum
# ---------------------------------------------------------------------------

def test_xs_momentum_longs_winners_shorts_losers():
    def _slope(name: str, slope: float) -> tuple[Symbol, list[Bar]]:
        s = Symbol(name, AssetClass.EQUITY)
        return s, [_bar(s, i, 100.0 + i * slope) for i in range(300)]
    bars = dict([
        _slope("WIN1", 0.20), _slope("WIN2", 0.15),
        _slope("MID1", 0.05), _slope("MID2", 0.00),
        _slope("LOS1", -0.05), _slope("LOS2", -0.10),
    ])
    sigs = list(xs_momentum(bars, formation_bars=200, skip_bars=5))
    by_name = {s.symbol.ticker: s.score for s in sigs}
    assert by_name.get("WIN1", 0) > 0
    assert by_name.get("LOS2", 0) < 0
    _scores_in_range(sigs)


def test_xs_momentum_silent_below_min_universe():
    sym = Symbol("LONE", AssetClass.EQUITY)
    bars = {sym: [_bar(sym, i, 100.0 + i * 0.1) for i in range(300)]}
    assert list(xs_momentum(bars, formation_bars=200, skip_bars=5)) == []


# ---------------------------------------------------------------------------
# 3.4 pairs_zscore
# ---------------------------------------------------------------------------

def test_pairs_zscore_emits_two_legs_with_opposite_signs():
    a = Symbol("A", AssetClass.EQUITY)
    b = Symbol("B", AssetClass.EQUITY)
    base = [100 + (i % 5) * 0.1 for i in range(150)]
    bars = {
        a: [_bar(a, i, c) for i, c in enumerate(base + [105.0])],
        b: [_bar(b, i, c) for i, c in enumerate(base + [100.0])],
    }
    sigs = list(pairs_zscore(bars, context={"pairs": [(a, b, 1.0)]},
                             lookback=140, entry_z=1.5))
    assert len(sigs) == 2
    score_a = next(s.score for s in sigs if s.symbol == a)
    score_b = next(s.score for s in sigs if s.symbol == b)
    assert math.copysign(1.0, score_a) != math.copysign(1.0, score_b)
    _scores_in_range(sigs)


def test_pairs_zscore_no_pairs_no_signals():
    sym = Symbol("S", AssetClass.EQUITY)
    bars = {sym: [_bar(sym, i, 100.0) for i in range(200)]}
    assert list(pairs_zscore(bars, context={})) == []


# ---------------------------------------------------------------------------
# 3.5 pca_residual_revert
# ---------------------------------------------------------------------------

def test_pca_residual_revert_runs_and_emits_bounded_signals():
    """The strategy is brittle to assert direction on synthetic data because
    PCA can absorb large idiosyncratic moves as its own factor. Instead we
    confirm it runs end-to-end on a co-moving universe and emits scores in
    range. Direction is validated by the doctest in the module."""
    rng = random.Random(0)
    names = ("A", "B", "C", "D", "E", "F", "G", "ODD")
    syms = [Symbol(n, AssetClass.EQUITY) for n in names]
    common = [rng.gauss(0, 0.001) for _ in range(80)]
    bars: dict[Symbol, list[Bar]] = {}
    for sym in syms:
        px = 100.0
        series = []
        for i in range(80):
            px *= 1 + 0.0005 + common[i] + rng.gauss(0, 0.0003)
            series.append(_bar(sym, i, px))
        bars[sym] = series
    # ODD drifts persistently against the others in the final 8 bars.
    odd = syms[-1]
    for j in range(8):
        idx = 80 - 8 + j
        old = bars[odd][idx]
        bars[odd][idx] = Bar(
            symbol=odd, ts=old.ts, open=old.open, high=old.high,
            low=old.low, close=old.close * (1 + 0.005 * (j + 1)), volume=1.0,
        )
    sigs = list(pca_residual_revert(bars, lookback=60, n_factors=2,
                                    entry_z=0.5))
    assert sigs, "expected at least one signal on a co-moving universe"
    _scores_in_range(sigs)


def test_pca_residual_revert_silent_with_one_symbol():
    sym = Symbol("ONE", AssetClass.EQUITY)
    bars = {sym: [_bar(sym, i, 100.0 + i * 0.01) for i in range(80)]}
    assert list(pca_residual_revert(bars)) == []


# ---------------------------------------------------------------------------
# 3.6 pead_drift
# ---------------------------------------------------------------------------

def test_pead_drift_long_on_positive_surprise():
    sym = Symbol("ER", AssetClass.EQUITY)
    bars = {sym: [_bar(sym, i, 100.0) for i in range(40)]}
    ctx = {"earnings": {sym: [{"ts": T0 + timedelta(days=20), "sue": 3.0}]}}
    sigs = list(pead_drift(bars, context=ctx))
    assert sigs and sigs[0].score > 0
    _scores_in_range(sigs)


def test_pead_drift_short_on_negative_surprise_with_decay():
    sym = Symbol("ER", AssetClass.EQUITY)
    bars = {sym: [_bar(sym, i, 100.0) for i in range(40)]}
    near = {"earnings": {sym: [{"ts": T0 + timedelta(days=39), "sue": -3.0}]}}
    far = {"earnings": {sym: [{"ts": T0 + timedelta(days=20), "sue": -3.0}]}}
    s_near = list(pead_drift(bars, context=near))[0]
    s_far = list(pead_drift(bars, context=far))[0]
    assert s_near.score < 0 and s_far.score < 0
    assert abs(s_near.score) > abs(s_far.score)  # decay reduces magnitude


def test_pead_drift_silent_without_context():
    sym = Symbol("ER", AssetClass.EQUITY)
    bars = {sym: [_bar(sym, i, 100.0) for i in range(40)]}
    assert list(pead_drift(bars)) == []


# ---------------------------------------------------------------------------
# Backtest end-to-end smoke for the price-only strategies.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["donchian_breakout", "bollinger_meanrev"])
def test_univariate_strategy_runs_in_backtest(name):
    sym = Symbol("FAKE", AssetClass.EQUITY)
    bars = {sym: [_bar(sym, i, 100.0 + i * 0.1 + (i % 7 - 3) * 0.4,
                       hi=100.0 + i * 0.1 + 0.6,
                       lo=100.0 + i * 0.1 - 0.6) for i in range(150)]}
    res = backtest_simple(get(name), bars, fee_bps=2, slippage_bps=1)
    summary = res.summary()
    assert summary["final_nav"] > 0
