"""Tests for hedger.strategies.news_embed.

The strategy is a thin reader on the mall — most of its behavior is wiring,
so the tests focus on: (a) graceful no-mall handling, (b) correct key lookup
and squashing, (c) absence of false positives when features are missing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from hedger.base import AssetClass, Bar, Symbol
from hedger.strategies import available, get
from hedger.strategies.news_embed import news_embed


T0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
SPY = Symbol(ticker="SPY", asset_class=AssetClass.ETF, venue="default")


def _bar(sym: Symbol, i: int, close: float) -> Bar:
    return Bar(
        symbol=sym,
        ts=T0 + timedelta(days=i),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
    )


def _bars_for(*syms):
    return {s: [_bar(s, 0, 100.0)] for s in syms}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_news_embed_registered():
    assert "news_embed" in available()
    assert get("news_embed") is news_embed


# ---------------------------------------------------------------------------
# Graceful absent-mall paths
# ---------------------------------------------------------------------------


def test_no_context_emits_nothing():
    out = list(news_embed(_bars_for(SPY)))
    assert out == []


def test_no_mall_emits_nothing():
    out = list(news_embed(_bars_for(SPY), context={}))
    assert out == []


def test_missing_feature_key_emits_nothing():
    out = list(news_embed(_bars_for(SPY), context={"mall": {}}))
    assert out == []


# ---------------------------------------------------------------------------
# Lookup + signal shape
# ---------------------------------------------------------------------------


def test_signal_emitted_for_session_hit():
    sess = (T0).date()
    mall = {"features:news_embed_v1": {(str(SPY), sess): 0.012}}
    out = list(news_embed(_bars_for(SPY), context={"mall": mall}))
    assert len(out) == 1
    sig = out[0]
    assert sig.symbol == SPY
    assert sig.strategy == "news_embed"
    # tanh(0.012 / 0.01) ≈ tanh(1.2) ≈ 0.834
    assert 0.5 < sig.score <= 1.0
    assert sig.meta["raw_score"] == 0.012


def test_negative_raw_score_produces_negative_signal():
    sess = T0.date()
    mall = {"features:news_embed_v1": {(str(SPY), sess): -0.02}}
    out = list(news_embed(_bars_for(SPY), context={"mall": mall}))
    assert out[0].score < 0


def test_score_clamped_to_unit_range():
    sess = T0.date()
    mall = {"features:news_embed_v1": {(str(SPY), sess): 1e6}}
    out = list(news_embed(_bars_for(SPY), context={"mall": mall}))
    assert -1.0 <= out[0].score <= 1.0


def test_ticker_only_fallback_key():
    sess = T0.date()
    mall = {"features:news_embed_v1": {(SPY.ticker, sess): 0.01}}
    out = list(news_embed(_bars_for(SPY), context={"mall": mall}))
    assert len(out) == 1


def test_string_key_fallback():
    sess = T0.date()
    mall = {"features:news_embed_v1": {f"{SPY.ticker}:{sess.isoformat()}": 0.01}}
    out = list(news_embed(_bars_for(SPY), context={"mall": mall}))
    assert len(out) == 1


def test_session_mismatch_emits_nothing():
    other_sess = date(2025, 1, 1)
    mall = {"features:news_embed_v1": {(str(SPY), other_sess): 0.5}}
    out = list(news_embed(_bars_for(SPY), context={"mall": mall}))
    assert out == []


def test_nan_or_invalid_raw_score_is_zero_signal():
    sess = T0.date()
    mall = {"features:news_embed_v1": {(str(SPY), sess): float("nan")}}
    out = list(news_embed(_bars_for(SPY), context={"mall": mall}))
    # tanh(nan)/scale-fallback returns 0.0
    assert out[0].score == 0.0


def test_score_scale_override():
    sess = T0.date()
    mall = {"features:news_embed_v1": {(str(SPY), sess): 0.01}}
    out_default = list(news_embed(_bars_for(SPY), context={"mall": mall}))
    out_wider = list(news_embed(_bars_for(SPY), context={"mall": mall}, score_scale=0.1))
    # With wider scale, same raw_score yields smaller signal
    assert abs(out_wider[0].score) < abs(out_default[0].score)


def test_empty_bars_emit_nothing():
    sess = T0.date()
    mall = {"features:news_embed_v1": {(str(SPY), sess): 0.01}}
    out = list(news_embed({SPY: []}, context={"mall": mall}))
    assert out == []
