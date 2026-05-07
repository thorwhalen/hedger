"""Tests for hedger.research — metrics, cointegration, factors, tearsheet."""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from hedger.base import AssetClass, Bar, Signal, Symbol
from hedger.research import (
    find_cointegrated_pairs,
    html_tearsheet,
    performance_summary,
    signal_ic,
)
from hedger.research._optional import require


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(sym: Symbol, i: int, close: float) -> Bar:
    return Bar(symbol=sym, ts=T0 + timedelta(days=i), open=close,
               high=close, low=close, close=close, volume=1.0)


# ---------------------------------------------------------------------------
# _optional.require
# ---------------------------------------------------------------------------

def test_require_imports_available_module():
    m = require("json")
    assert m.__name__ == "json"


def test_require_raises_with_install_hint():
    with pytest.raises(ImportError) as exc:
        require("definitely_not_a_real_module_zzz")
    msg = str(exc.value)
    assert "[research]" in msg
    assert "definitely_not_a_real_module_zzz" in msg


# ---------------------------------------------------------------------------
# performance_summary
# ---------------------------------------------------------------------------

def _trending_nav(n: int = 250, drift: float = 0.0008, vol: float = 0.005,
                  seed: int = 0) -> pd.Series:
    rng = random.Random(seed)
    rets = [rng.gauss(drift, vol) for _ in range(n)]
    nav = [100.0]
    for r in rets:
        nav.append(nav[-1] * (1 + r))
    idx = pd.date_range("2026-01-01", periods=n + 1, freq="D")
    return pd.Series(nav, index=idx)


def test_performance_summary_fallback_returns_headline_metrics():
    nav = _trending_nav()
    s = performance_summary(nav, use_empyrical=False)
    for key in ("annual_return", "annual_volatility", "sharpe", "sortino",
                "calmar", "max_drawdown", "n_observations"):
        assert key in s
    assert s["n_observations"] == 250
    assert s["annual_return"] > 0  # built with positive drift


def test_performance_summary_empyrical_path_returns_extra_metrics():
    """Empyrical uses geometric annualisation, the fallback uses arithmetic,
    so they don't agree numerically — but both should be positive on a
    positive-drift series and empyrical adds ``omega`` and ``tail_ratio``."""
    pytest.importorskip("empyrical")
    nav = _trending_nav(seed=1)
    fallback = performance_summary(nav, use_empyrical=False)
    empy = performance_summary(nav, use_empyrical=True)
    assert fallback["sharpe"] > 0 and empy["sharpe"] > 0
    assert "omega" in empy
    assert "tail_ratio" in empy


def test_performance_summary_empty_nav_returns_nans():
    nav = pd.Series(dtype=float)
    s = performance_summary(nav, use_empyrical=False)
    assert s["n_observations"] == 0
    assert math.isnan(s["sharpe"])


# ---------------------------------------------------------------------------
# find_cointegrated_pairs
# ---------------------------------------------------------------------------

def test_find_cointegrated_pairs_recovers_obvious_pair():
    """Two co-integrated names + an unrelated random walk; the cointegrated
    pair should top the screen."""
    pytest.importorskip("statsmodels")
    rng = np.random.default_rng(42)
    n = 300
    common = np.cumsum(rng.normal(0, 0.01, n))
    base = 100 * np.exp(common)
    # A and B co-move tightly via the common factor.
    a = base * np.exp(rng.normal(0, 0.001, n))
    b = base * np.exp(rng.normal(0, 0.001, n))
    # C is an unrelated random walk.
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    syms = {name: Symbol(name, AssetClass.EQUITY) for name in ("A", "B", "C")}
    bars = {
        syms["A"]: [_bar(syms["A"], i, a[i]) for i in range(n)],
        syms["B"]: [_bar(syms["B"], i, b[i]) for i in range(n)],
        syms["C"]: [_bar(syms["C"], i, c[i]) for i in range(n)],
    }
    results = find_cointegrated_pairs(bars, lookback=250, pvalue_threshold=0.10)
    assert results, "expected at least one cointegrated pair"
    # The first hit should be the (A, B) or (B, A) pair, not C.
    top = results[0]
    assert {top.a.ticker, top.b.ticker} == {"A", "B"}
    a_sym, b_sym, beta = top.as_pair_triple()
    assert isinstance(beta, float)


def test_find_cointegrated_pairs_returns_empty_when_no_cointegration():
    pytest.importorskip("statsmodels")
    rng = np.random.default_rng(7)
    n = 200
    syms = [Symbol(name, AssetClass.EQUITY) for name in ("X", "Y")]
    bars = {s: [_bar(s, i, 100 * math.exp(p))
                for i, p in enumerate(np.cumsum(rng.normal(0, 0.02, n)))]
            for s in syms}
    results = find_cointegrated_pairs(bars, lookback=180,
                                       pvalue_threshold=0.001)
    assert results == []


# ---------------------------------------------------------------------------
# signal_ic
# ---------------------------------------------------------------------------

def test_signal_ic_positive_for_perfectly_predictive_score():
    a = Symbol("A", AssetClass.EQUITY)
    b = Symbol("B", AssetClass.EQUITY)
    bars = {
        a: [_bar(a, i, 100 + i) for i in range(20)],
        b: [_bar(b, i, 100 - i) for i in range(20)],
    }
    sigs = []
    for i in range(15):
        sigs.append(Signal(symbol=a, ts=T0 + timedelta(days=i),
                           score=0.8, strategy="x"))
        sigs.append(Signal(symbol=b, ts=T0 + timedelta(days=i),
                           score=-0.8, strategy="x"))
    ic = signal_ic(sigs, bars, horizon_bars=1)
    assert ic["mean_ic"] > 0
    assert ic["n_observations"] == 15


def test_signal_ic_handles_empty_input():
    ic = signal_ic([], {}, horizon_bars=1)
    assert ic["n_observations"] == 0
    assert math.isnan(ic["mean_ic"])


# ---------------------------------------------------------------------------
# html_tearsheet
# ---------------------------------------------------------------------------

def test_html_tearsheet_writes_file(tmp_path: Path):
    pytest.importorskip("quantstats")
    nav = _trending_nav(n=180, seed=2)
    out = tmp_path / "tear.html"
    result = html_tearsheet(nav, out, title="hedger test")
    assert result == out
    assert out.exists()
    assert out.stat().st_size > 0
