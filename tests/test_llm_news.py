"""Tests for the llm_news strategy and the runner's news context plumbing."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Iterable

import pytest

from hedger.base import AssetClass, Bar, Symbol
from hedger.config import Config, DataConfig
from hedger.data.stores import mall


# ---------------------------------------------------------------------------
# Strategy unit test (no network) — score_fn is injectable
# ---------------------------------------------------------------------------

def test_llm_news_returns_signal_with_injected_score_fn():
    from hedger.strategies.llm_news import llm_news
    sym = Symbol("SPY", AssetClass.ETF)
    bars = {sym: [Bar(symbol=sym, ts=datetime(2026, 1, h+1, tzinfo=timezone.utc),
                      open=100, high=101, low=99, close=100.5, volume=1000)
                  for h in range(3)]}
    headlines = {"default:SPY": ["SPY rallies on rate cut hopes"]}

    def fake_score(prompt: str) -> str:
        return json.dumps([{
            "symbol": "default:SPY",
            "score": 0.7,
            "rationale": "rate-cut tailwind",
        }])

    sigs = list(llm_news(bars, context={"news": headlines}, score_fn=fake_score))
    assert len(sigs) == 1
    assert sigs[0].symbol == sym
    assert 0.69 < sigs[0].score < 0.71
    assert sigs[0].strategy == "llm_news"
    assert sigs[0].meta["rationale"]


def test_llm_news_no_news_for_symbol_yields_no_signal():
    from hedger.strategies.llm_news import llm_news
    sym = Symbol("SPY", AssetClass.ETF)
    bars = {sym: [Bar(symbol=sym, ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
                      open=100, high=101, low=99, close=100.5, volume=1000)]}
    sigs = list(llm_news(bars, context={"news": {}}, score_fn=lambda p: "[]"))
    assert sigs == []


def test_llm_news_clips_score_to_unit_interval():
    """Even a wild API response should be clipped to [-1, 1]."""
    from hedger.strategies.llm_news import llm_news
    sym = Symbol("SPY", AssetClass.ETF)
    bars = {sym: [Bar(symbol=sym, ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
                      open=100, high=101, low=99, close=100.5, volume=1000)]}
    out = json.dumps([{"symbol": "default:SPY", "score": 5.0, "rationale": "x"}])
    sigs = list(llm_news(bars, context={"news": {"default:SPY": ["headline"]}},
                          score_fn=lambda p: out))
    assert sigs[0].score == 1.0


def test_llm_news_extracts_array_from_chatty_response():
    """Real LLM responses sometimes wrap JSON in commentary."""
    from hedger.strategies.llm_news import llm_news
    sym = Symbol("SPY", AssetClass.ETF)
    bars = {sym: [Bar(symbol=sym, ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
                      open=100, high=101, low=99, close=100.5, volume=1000)]}
    text = ("Here are the scores:\n"
            '[{"symbol": "default:SPY", "score": -0.4, "rationale": "soft data"}]\n'
            "End of analysis.")
    sigs = list(llm_news(bars,
                         context={"news": {"default:SPY": ["headline"]}},
                         score_fn=lambda p: text))
    assert len(sigs) == 1
    assert sigs[0].score == -0.4


# ---------------------------------------------------------------------------
# Runner news plumbing — verify news_context is correctly built
# ---------------------------------------------------------------------------

class _FakeNewsSource:
    name = "alpaca_news"

    def __init__(self, items):
        self.items = items
        self.calls = 0

    def fetch(self, symbols, *, start=None, end=None, limit=50):
        self.calls += 1
        return iter(self.items)


def _runner_for_news(news_source):
    from hedger.execution.brokers import PaperBroker
    from hedger.live.runner import Runner
    from hedger.strategies.sma_crossover import sma_crossover  # registers
    cfg = Config(universe=("SPY",), timeframe="1h",
                 data=DataConfig(primary="alpaca", timeframe="1h"))
    return Runner(config=cfg, strategy=sma_crossover,
                  broker=PaperBroker(price_fn=lambda s: 1.0),
                  source=None,  # type: ignore[arg-type]
                  mall=mall(tempfile.mkdtemp()),
                  news_source=news_source,
                  news_refresh_minutes=0)


def test_news_context_builds_from_mall():
    items = [{
        "id": "1",
        "headline": "SPY rallies",
        "summary": "",
        "symbols": ["SPY"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "url": None,
        "author": None,
    }]
    runner = _runner_for_news(_FakeNewsSource(items))
    sym = Symbol("SPY", AssetClass.EQUITY)
    n = runner.refresh_news([sym])
    assert n == 1
    ctx = runner.news_context([sym])
    assert ctx["default:SPY"] == ["SPY rallies"]


def test_news_context_dedupes_via_news_id():
    """Two refresh calls with the same items shouldn't double-count headlines."""
    item = {
        "id": "stable-id",
        "headline": "SPY rallies",
        "summary": "",
        "symbols": ["SPY"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "url": None,
        "author": None,
    }
    src = _FakeNewsSource([item])
    runner = _runner_for_news(src)
    sym = Symbol("SPY", AssetClass.EQUITY)
    runner.refresh_news([sym])
    runner.refresh_news([sym])
    ctx = runner.news_context([sym])
    # Same headline once, even though refresh ran twice (mall is keyed by id).
    assert ctx["default:SPY"] == ["SPY rallies"]


# ---------------------------------------------------------------------------
# Live integration test — real Alpaca news + real Anthropic Haiku call
# ---------------------------------------------------------------------------

requires_creds = pytest.mark.skipif(
    not (os.environ.get("ALPACA_API_KEY")
         and os.environ.get("ALPACA_SECRET_KEY")
         and os.environ.get("ANTHROPIC_API_KEY")),
    reason="needs ALPACA_* and ANTHROPIC_API_KEY in env",
)


@requires_creds
def test_llm_news_end_to_end_with_real_apis():
    """Pull real news, call Haiku, verify a Signal pops out (or empty if no news)."""
    from hedger.data.sources import AlpacaNews
    from hedger.strategies.llm_news import llm_news

    news_src = AlpacaNews()
    sym = Symbol("SPY", AssetClass.ETF)
    items = list(news_src.fetch(["SPY"],
                                start=datetime.now(timezone.utc) - timedelta(days=2),
                                limit=5))
    if not items:
        pytest.skip("no recent SPY news; skip live LLM round-trip")

    # Build a tiny bar window — llm_news only inspects the last 30 closes.
    bars = {sym: [Bar(symbol=sym, ts=datetime.now(timezone.utc) - timedelta(hours=h),
                      open=100, high=101, low=99, close=100 + h*0.1, volume=1000)
                  for h in range(5)]}
    headlines = {"default:SPY": [it["headline"] for it in items if it.get("headline")]}
    sigs = list(llm_news(bars, context={"news": headlines}))
    # We only check shape: 0 signals is fine if Haiku returned no usable JSON,
    # but if anything came back it should be a valid Signal in [-1, 1].
    for s in sigs:
        assert -1.0 <= s.score <= 1.0
        assert s.strategy == "llm_news"
