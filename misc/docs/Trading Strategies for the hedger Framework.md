---
title: "Trading Strategies for the hedger Framework"
author: "Thor Whalen"
date: 2026-05-06
---

# Trading Strategies for the `hedger` Framework

*by Thor Whalen — 2026-05-06*

> A self-contained research report mapping the algorithmic-trading-strategy landscape onto the `hedger` plug-in contract (`Bar → Signal → Sizer → Decision`). Written for a senior Python engineer who is new to quantitative-finance jargon: every term is defined on first use. Vancouver-style numeric refs in §6.

---

## § 1. Terminology landscape

### 1.1 Strategy / system / model / algo

- **Strategy** — the *idea*: "buy past winners, sell past losers." This is what `hedger.Strategy` represents. It is the unit of *alpha* (defined below).
- **System** — strategy + sizer + risk + execution + ops. `hedger` is a system; a strategy is one of its components.
- **Model** — usually a *statistical or ML object* that produces a forecast or score (a logistic regression, a gradient-boosted tree, an LSTM). A model is often a sub-component of a strategy.
- **Algo** — in sell-side parlance an *execution algo* (VWAP, TWAP, POV) is a scheduler, **not** an alpha. In retail/quant blogs it is often loose for "systematic." In `hedger`, execution algos live in the Broker/Sizer layer, not the Strategy layer.

### 1.2 Signal, alpha, factor

- **Signal** — a directional view at one timestamp; in `hedger`, `Signal.score ∈ [-1, 1]`. Signals are *unsized*.
- **Alpha** — return in excess of a benchmark, attributable to skill rather than to broad market exposure.
- **Factor** — a *systematic risk premium* (e.g. value, size, momentum, quality, low-vol) that explains a slice of cross-sectional return variation. The Fama–French 3- and 5-factor models [3, 4] are canonical. In quant practice a factor is usually a long/short portfolio constructed from a sorting rule.

A `hedger` `Signal` is operationally closest to an *alpha forecast for one symbol at one timestamp*; multiple strategies' signals can be combined upstream of the Sizer, which is exactly how multi-factor portfolios are built in industry.

### 1.3 Univariate / cross-sectional / multivariate / context-augmented

| Pattern (the user's framing) | Canonical name | Example |
|---|---|---|
| Looks at one ticker, acts on that ticker | **univariate / single-name / time-series** | SMA crossover; RSI mean reversion |
| Looks at a basket, acts on all of them | **cross-sectional** ("ranking" / "long-short portfolio") | XSMOM [1]; FF factor portfolios [3] |
| Acts on one ticker, uses other tickers as predictors | **multivariate / relative-value / pairs / cointegration / stat-arb** | Pairs trading [5]; Avellaneda–Lee residuals [6] |
| Uses non-price data | **context-augmented** in hedger terms (industry: fundamental / sentiment / NLP / macro / alt-data) | FinBERT [7]; LLM systems [8] |

### 1.4 Time-series vs. cross-sectional momentum

- **Cross-sectional momentum (XSMOM)** — *rank* every name in a universe by past return; long top decile, short bottom decile [1].
- **Time-series momentum (TSMOM)** — for *each* asset independently, long if its own past return is positive, short if negative [2].

The two are correlated but distinct: in extreme markets (e.g. 2008) TSMOM goes net-short across many assets; XSMOM cannot, because it is dollar-neutral within the universe by construction.

### 1.5 Long-only / long-short / market-neutral / dollar-neutral

- **Long-only**: weights ∈ [0, 1].
- **Long/short**: weights ∈ [-1, 1]; needs a margin account and a borrow facility.
- **Dollar-neutral**: gross long = gross short (in $).
- **Market-neutral**: net beta to the chosen benchmark ≈ 0 after factor hedging — strictly stronger than dollar-neutral.

For `hedger`'s Alpaca broker, US equities and most ETFs are shortable; many cryptos are not on spot venues.

### 1.6 Other axes

- **Discretionary** (human) vs **systematic** (code). `hedger` is exclusively systematic.
- **Rule-based** (hand-coded) vs **ML-based** (learned).
- **Technical** (OHLCV-only) vs **fundamental** (accounting) vs **sentiment / NLP** (text) vs **alt-data** (everything else: satellite, on-chain, etc.).

### 1.7 Frequency taxonomy

- **HFT** — sub-second, requires colocated servers. *Not* feasible from a single retail server.
- **Intraday** — minutes to hours; below ~30 minutes, slippage and broker latency dominate edges.
- **Swing** — 1–10 days; the natural sweet spot for `hedger` at 4h bars.
- **Position** — weeks to months; daily bars suffice.
- **Buy-and-hold** — the benchmark to beat.

**Realistic for `hedger` at 1h–4h on a single server:** swing, position, the slow end of intraday. Order-book / market-making strategies are out of scope under the current `Bar`/`Signal` contract.

---

## § 2. Strategy families

### 2.1 Trend-following / time-series momentum

**Synonyms**: TSMOM, trend-following, CTA-style, breakouts (Donchian, Keltner). **Intuition**: assets that have gone up tend to keep going up over 1–12 months. **Inputs**: OHLCV, single-symbol; univariate. **Cadence**: daily to multi-month; 4h is at the fast edge of robustness.

**Honest assessment**: Moskowitz et al. [2] reported gross Sharpe ~1.5 on a diversified 58-asset futures portfolio, 1985–2009. **Out-of-sample reality is materially worse.** AQR's September 2018 whitepaper "Trend Following in Focus" reports that the SG Trend Index — the ten largest trend-following CTAs — returned just +1.0% annualised from April 2009 to June 2018 versus +9.7% annualised for a global 60/40 portfolio (Exhibit 6). The strategy is regime-dependent (great in 2008, 2022; awful in chop). On single-name equities (where most `hedger` users will run it) signals are noisier than on the diversified futures basket. Realistic single-name single-server Sharpe before fees: 0.3–0.7. Survivorship inflates published numbers.

### 2.2 Mean reversion

**Synonyms**: contrarian, OU-process trading, Bollinger reversion, RSI extremes. **Intuition**: short-horizon overshoots revert. The **Ornstein–Uhlenbeck (OU) process** — a stochastic process that drifts back toward a long-run mean with mean-reversion speed θ — is the standard model. **Cadence**: intraday-to-weekly. At 1h–4h, single-name reversion on liquid US equities is heavily arbitraged; on crypto it survives but is fee-sensitive.

**Honest assessment**: Do & Faff [9, 10] showed that after costs, naïve short-term reversal on US equities is approximately zero alpha post-2002. Many backtests look great because they ignore borrow fees, short-availability and bid-ask spread on the names where signals fire (which are the illiquid ones).

### 2.3 Cross-sectional momentum

**Synonyms**: XSMOM, JT93, "12–1 momentum" (skip the most recent month to avoid the 1-month reversal). **Intuition**: rank a universe by past 12-month return; long top decile, short bottom decile, monthly rebalance. JT93 [1] reported (p. 67) that "the portfolio formed on the basis of returns realized in the past 6 months generates an average cumulative return of 9.5% over the next 12 months" — i.e. ≈ 0.79% per month — for NYSE/AMEX stocks January 1965–December 1989. **Cadence**: monthly canonical; weekly works; 4h is too fast for the classical version, but a "fast XSMOM" with 1–5 day formation periods is viable on crypto.

**Honest assessment**: post-publication decay is real but the factor still has positive risk-adjusted return in most decades [16]. The "momentum crash" of March–May 2009 cost the factor roughly 70% in three months. On crypto, recent work finds time-series momentum stronger than cross-sectional, with cross-sectional sometimes insignificant on monthly samples [17, 18].

### 2.4 Pairs trading / statistical arbitrage / cointegration

**Synonyms**: stat-arb, relative-value arbitrage, generalised pairs. **Intuition**: two assets share a common factor; their spread is mean-reverting; trade the spread. **hedger slot**: relative-value — multivariate. **Cadence**: minutes to days; 4h is realistic.

**Honest assessment**: Gatev–Goetzmann–Rouwenhorst 2006 [5] reported about 11% annualised excess returns 1962–2002 on simple distance-pairs. Do & Faff [9, 10] showed naïve pairs trading became largely unprofitable post-2002 (~0.24% per month 2003–2009 after costs). Avellaneda & Lee 2010 [6] reported Sharpe 1.44 on PCA stat-arb 1997–2007 but only 0.9 in 2003–2007 — already decaying in-sample. Real edge today requires careful pair selection (industry-restricted, fundamentally similar) and is fragile to regime breaks (the 2007 quant quake, March 2020).

### 2.5 Factor investing ("smart beta")

**Intuition**: long/short or long-tilt portfolios sorted on Value, Size, Quality, Low-Vol, Momentum. Fama–French 3-factor [3] adds Size + Value to CAPM; FF 5-factor [4] adds Profitability and Investment. **Inputs**: OHLCV + fundamentals (book value, earnings) — fundamentals are not yet plumbed into hedger's `context`.

**Honest assessment**: published premia (~3–5% annualised per factor) are robust over 50+ years but exhibit decade-long droughts. Arnott, Harvey, Kalesnik & Rattray (2021), "Reports of Value's Death May Be Greatly Exaggerated" (*Financial Analysts Journal*, doi:10.1080/0015198X.2020.1842704), document that the Fama–French HML factor experienced a drawdown of −55% from 2007 to June 2020 — "the largest drawdown observed since June 1963." Most retail "factor" ETFs deliver pure-factor exposure of only 0.3–0.5; the rest is market beta. For `hedger`, only price-only factors (Momentum, Low-Vol, 52-week-high) are immediately implementable.

### 2.6 Volatility / vol-targeting / variance strategies

**Intuition**: scale exposure inversely to recent realised volatility so risk contribution is constant; improves Sharpe on most underlyings [21]. **hedger slot**: this is a *Sizer*, not a Strategy. **Honest assessment**: vol targeting genuinely improves risk-adjusted return for trending or autocorrelated assets; neutral or harmful for mean-reverting assets. It is risk management, not alpha.

### 2.7 Event-driven (PEAD, M&A, index inclusion)

**Intuition**: prices underreact to news; ride the drift. Bernard & Thomas 1989 [11] documented post-earnings-announcement drift: top-decile earnings-surprise stocks outperform bottom-decile by roughly 6% over the 60 days post-announcement. **Inputs**: OHLCV + earnings calendar + analyst consensus.

**Honest assessment**: PEAD has decayed substantially. A 2024 ScienceDirect review (doi:10.1016/j.irfa.2024.103922) reports the magnitude has declined from 18% annualised abnormal returns (Bernard & Thomas, 1989) to approximately 4% in recent periods, citing Auer & Rottmann (2019) and Chordia et al. (2014) and noting cases at "the point of insignificance" [12]. Real but smaller; concentrated in small-cap / high-spread names where realistic costs eat most of the gross alpha. M&A-arb requires deal-flow data the scaffold doesn't plumb.

### 2.8 Sentiment / NLP / news-driven

**Intuition**: extract polarity/topic from headlines/filings/tweets; trade with the sentiment. **Inputs**: OHLCV + text feed (hedger's `AlpacaNews` already provides this).

**Honest assessment**: Araci's FinBERT [7] beats Loughran–McDonald dictionaries on classification benchmarks, but **classification accuracy is not Sharpe ratio**. Liu, Lin & Rojas 2025 [13] report monthly returns of 2–4% combining FinBERT with technical indicators on the S&P 500, but these are short windows on a single index. A 2024 paper using OPT (a GPT-3-class model) on 965K news articles reports a 3.05 Sharpe long–short, August 2021–July 2023 [14] — only 23 months out-of-sample, in a uniquely tradeable post-COVID regime. Treat as suggestive, not confirmed.

### 2.9 ML on engineered features (tree models)

**Intuition**: engineer hundreds of price/volume features, train XGBoost/LightGBM/CatBoost to predict next-N-bar return; threshold to a signal. **Honest assessment**: López de Prado's *Advances in Financial Machine Learning* [18] is required reading and explains why most published "ML beats market" results are p-hacked — backtest overfitting, lookahead through normalisation, and survivorship bias are pervasive. Use **purged k-fold cross-validation** and **combinatorial purged CV** [18] or your numbers will lie. Realistic out-of-sample Sharpe for a careful single-server tree model on liquid US equities is 0.3–0.8; anything published north of 2 should be treated as suspect until reproduced.

### 2.10 Reinforcement learning (FinRL, deep RL)

**Honest assessment**: FinRL [19] is the dominant open-source framework — `AI4Finance-Foundation/FinRL`, MIT. The original `master` branch was last meaningfully updated December 2025; the AI4Finance lab acknowledges on `openfin.engineering.columbia.edu` that the legacy repo has been substantially inactive since November 2023. Active development has moved to `FinRL-Trading` (FinRL-X) and the FinRL Contests 2024/2025. Most published FinRL results are on Dow-30 with hand-tuned hyperparameters and short out-of-sample windows; reproducing them is hard and run-to-run variance is often larger than the alpha. RL is a research vehicle, not a production-ready alpha source for a single-server retail bot.

### 2.11 Multi-agent LLM systems (TradingAgents-style)

**Honest assessment**: Xiao et al. 2024 (TradingAgents [8]) report Sharpe ratios of 5.6 to 8.2 on AAPL/MSFT/GOOG over a *three-month* window in early 2024. The repo `TauricResearch/TradingAgents` (Apache-2.0) is very actively maintained — 65.6K GitHub stars and 12.7K forks as of early May 2026, with v0.2.4 released 25 April 2026. The authors themselves note in a footnote that the high SR comes from "few pullbacks during that period." Independent reproductions in 2026 — including the ACM AIFinTech 2026 reproducibility study (Google stock, May–July 2025) — find that both Qwen3- and GPT-4o-based TradingAgents configurations *fail to outperform buy-and-hold*, with mean cumulative returns of about 16–18% versus about 19% for passive holding [22]. A March 2026 critique [23] explicitly argues the published Sharpe is consistent with a bullish-regime trend-following artifact rather than alpha.

**Cost**: the paper reports about 11 LLM calls and 20+ tool calls per decision. At GPT-4o pricing (about $2.50/M input, $10/M output, April 2026) and roughly 5K input + 1K output tokens per call, that's about $0.25 per decision; at Claude Sonnet 4.6 ($3/$15 per million tokens) about $0.33. Across 100 symbols, daily, that's about $25–33/day before tool-API costs. Treat as a research seam.

### 2.12 – 2.14 — out of scope or deferred

- **Market-making / liquidity provision** — out of scope for `hedger` at retail (sub-millisecond latency, direct-market-access, rebate fee schedules, order-book data that the framework doesn't carry).
- **Carry strategies (FX, futures roll yield)** — defer until futures/FX feeds are plumbed.
- **Options-based** — flag explicitly: requires option-chain, IV-surface, and Greeks. The `Bar` dataclass has no concept of strike, expiry or option-side. Adding these would require a new `OptionContract` abstraction (or extending `Symbol.meta` to carry strike/expiry plus a separate IV-surface plug-in), **not** just a new strategy. Don't propose options strategies until the data model is extended.

---

## § 3. Concrete strategy recommendations for `hedger`

Eight picks, spanning all three taxonomy axes and three complexity tiers. All conform literally to:

```python
def strategy(
    bars: Mapping[Symbol, Iterable[Bar]],
    *,
    context: Mapping[str, Any] | None = None,
    **kwargs,
) -> Iterable[Signal]: ...
```

### 3.1 (Simple) `donchian_breakout` — univariate trend

```python
@register("donchian_breakout")
def donchian_breakout(bars, *, context=None, fast=20, slow=55, atr_window=14):
    """Long when close > rolling-high(fast); short when close < rolling-low(fast).
    Score = tanh((close - channel) / ATR), bounded in [-1, 1]."""
    for symbol, window in bars.items():
        w = list(window)
        if len(w) < slow + 1:
            continue
        upper = max(b.high for b in w[-fast:-1])
        lower = min(b.low  for b in w[-fast:-1])
        atr   = _atr(w[-atr_window:])
        c     = w[-1].close
        if atr == 0:
            continue
        if c > upper:
            score = math.tanh((c - upper) / atr)
        elif c < lower:
            score = -math.tanh((lower - c) / atr)
        else:
            continue
        yield Signal(symbol=symbol, ts=w[-1].ts, score=score,
                     strategy="donchian_breakout",
                     meta={"upper": upper, "lower": lower, "atr": atr})
```

- **Score map**: `tanh((close − channel) / ATR)`.
- **Defaults**: `fast=20, slow=55, atr_window=14`. **Sweep**: `fast ∈ {10, 20, 40}`, `slow ∈ {40, 55, 100}`.
- **Edge / failure modes**: positive expectancy on liquid futures and major crypto; whipsaws hard in choppy ranges; on individual equities Sharpe is typically <0.5.

### 3.2 (Simple) `bollinger_meanrev` — univariate mean reversion

```python
@register("bollinger_meanrev")
def bollinger_meanrev(bars, *, context=None, window=20, n_std=2.0, rsi_filter=14):
    """Fade z-score deviations beyond ±n_std from rolling mean. Optional RSI filter."""
    for symbol, w in bars.items():
        w = list(w)
        if len(w) < window + 1:
            continue
        closes = np.array([b.close for b in w])
        mean   = closes[-window:].mean()
        std    = closes[-window:].std(ddof=1)
        if std == 0:
            continue
        z = (closes[-1] - mean) / std
        if abs(z) < n_std:
            continue
        score = float(-np.tanh(z / n_std))   # contrarian
        yield Signal(symbol=symbol, ts=w[-1].ts, score=score,
                     strategy="bollinger_meanrev",
                     meta={"z": z, "mean": mean, "std": std})
```

- **Score map**: `-tanh(z / n_std)`. **Sweep**: `window ∈ {10, 20, 50}`, `n_std ∈ {1.5, 2.0, 2.5}`.
- **Edge / failure modes**: works on range-bound assets; **catastrophic in trends** ("falling knives"). Pair with a regime filter (e.g. only fire when ADX < 20) in production.

### 3.3 (Medium) `xs_momentum` — cross-sectional momentum (JT93)

```python
@register("xs_momentum")
def xs_momentum(bars, *, context=None,
                formation_bars=252, skip_bars=21,
                top_quantile=0.2, bottom_quantile=0.2):
    """Rank universe by past return (skip-1m). Long top quantile, short bottom."""
    rets = {}
    for symbol, w in bars.items():
        w = list(w)
        if len(w) < formation_bars + skip_bars + 1:
            continue
        c_then = w[-(formation_bars + skip_bars)].close
        c_now  = w[-skip_bars - 1].close
        rets[symbol] = (c_now / c_then) - 1.0
    if len(rets) < 5:
        return
    ranked = sorted(rets.items(), key=lambda kv: kv[1])
    n = len(ranked)
    n_top = max(1, int(n * top_quantile))
    n_bot = max(1, int(n * bottom_quantile))
    losers, winners = dict(ranked[:n_bot]), dict(ranked[-n_top:])
    ts = list(next(iter(bars.values())))[-1].ts
    for sym in winners:
        yield Signal(symbol=sym, ts=ts, score=+1.0, strategy="xs_momentum",
                     meta={"ret_form": winners[sym], "side": "winner"})
    for sym in losers:
        yield Signal(symbol=sym, ts=ts, score=-1.0, strategy="xs_momentum",
                     meta={"ret_form": losers[sym], "side": "loser"})
```

- **Score map**: binary ±1 on the tails; continuous variant uses `tanh` of the cross-sectional z-score of formation returns.
- **Sweep**: `formation_bars`, `top_quantile/bottom_quantile ∈ {0.1, 0.2, 0.3}`.
- **Edge / failure modes**: real, ~3–5% annualised long-term [1, 16]; periodic momentum crashes (March 2009: ~−70%). On crypto, restrict to top-50-by-volume to limit pump-and-dump contamination [17].

### 3.4 (Medium) `pairs_zscore` — cointegration-based pairs

```python
@register("pairs_zscore")
def pairs_zscore(bars, *, context=None, lookback=120, entry_z=2.0, exit_z=0.5):
    """For each (a, b, beta) in context['pairs']: spread = a - beta*b;
    z-score it; long-cheap-leg / short-rich-leg when |z| > entry_z."""
    pairs = (context or {}).get("pairs", [])
    for (sym_a, sym_b, beta) in pairs:
        w_a = list(bars.get(sym_a, []))
        w_b = list(bars.get(sym_b, []))
        if len(w_a) < lookback or len(w_b) < lookback:
            continue
        a = np.array([b.close for b in w_a[-lookback:]])
        b = np.array([b.close for b in w_b[-lookback:]])
        spread = a - beta * b
        z = (spread[-1] - spread.mean()) / spread.std(ddof=1)
        if abs(z) < entry_z:
            continue
        score_a = float(-np.tanh(z / entry_z))
        score_b = -score_a
        ts = w_a[-1].ts
        yield Signal(symbol=sym_a, ts=ts, score=score_a, strategy="pairs_zscore",
                     meta={"z": z, "pair": (sym_a.ticker, sym_b.ticker), "leg": "A"})
        yield Signal(symbol=sym_b, ts=ts, score=score_b, strategy="pairs_zscore",
                     meta={"z": z, "pair": (sym_a.ticker, sym_b.ticker), "leg": "B"})
```

- **Score map**: `-tanh(z / entry_z)` for the cheap leg; opposite for the rich leg.
- **Where the cointegration test lives**: *not* in the strategy. The runner should screen pairs offline (Engle–Granger, Johansen) and inject β via `context`. This keeps the strategy pure.
- **Edge / failure modes**: post-2002 alpha is small [10]; fragile to one leg blowing up on a corporate event. Cap per-pair exposure; have a hard time-stop (e.g. close after 20 bars without convergence).

### 3.5 (Medium) `pca_residual_revert` — Avellaneda–Lee residual stat-arb

```python
@register("pca_residual_revert")
def pca_residual_revert(bars, *, context=None,
                        lookback=60, n_factors=5,
                        entry_z=1.25, exit_z=0.5):
    """PCA on universe returns → top-k factors. For each name, residualise vs factors,
    treat cumulative residual as OU; signal = -z(cum_residual)."""
    rets = _aligned_returns(bars, lookback)
    if rets is None or rets.shape[0] < lookback:
        return
    cov = np.cov(rets.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    top = eigvecs[:, -n_factors:]
    factor_rets = rets @ top
    for i, sym in enumerate(_universe(bars)):
        beta, *_ = np.linalg.lstsq(factor_rets, rets[:, i], rcond=None)
        resid = rets[:, i] - factor_rets @ beta
        cum   = np.cumsum(resid)
        z = (cum[-1] - cum.mean()) / cum.std(ddof=1)
        if abs(z) < entry_z:
            continue
        score = float(-np.tanh(z / entry_z))
        yield Signal(symbol=sym, ts=_last_ts(bars, sym), score=score,
                     strategy="pca_residual_revert",
                     meta={"z": z, "n_factors": n_factors})
```

- **Sweep**: `lookback ∈ {30, 60, 90}`, `n_factors ∈ {3, 5, 7, 10}`, `entry_z ∈ {1.0, 1.25, 1.5}`.
- **Edge / failure modes**: canonical Avellaneda–Lee 2010 [6]; original Sharpe ~1.4 pre-2003, decaying to 0.9 post-2003. Modern reality on large-cap US equities net of costs: 0.3–0.6. Very crowded.

### 3.6 (Medium) `pead_drift` — post-earnings-announcement drift

Requires earnings via `context["earnings"]`.

```python
@register("pead_drift")
def pead_drift(bars, *, context=None,
               drift_window_bars=60, sue_threshold=1.0):
    """Long names that beat earnings (SUE > +threshold), short those that missed,
    hold for drift_window_bars after announcement; magnitude decays linearly."""
    earnings = (context or {}).get("earnings", {})
    for sym, w in bars.items():
        events = earnings.get(sym, [])
        if not events:
            continue
        last_event = events[-1]
        bars_since = _bars_since(w, last_event["ts"])
        if bars_since is None or bars_since > drift_window_bars:
            continue
        sue = last_event["sue"]
        if abs(sue) < sue_threshold:
            continue
        score = float(np.tanh(sue / 3.0))
        decay = 1.0 - bars_since / drift_window_bars
        yield Signal(symbol=sym, ts=list(w)[-1].ts, score=score * decay,
                     strategy="pead_drift",
                     meta={"sue": sue, "bars_since": bars_since,
                           "event_ts": last_event["ts"]})
```

- **Score map**: `tanh(SUE / 3) × (1 − bars_since/drift_window)`.
- **Edge / failure modes**: PEAD is decaying [12]; concentrate on small/mid-cap names with low analyst coverage where the drift is strongest. Watch out for short-borrow on small caps.

### 3.7 (Medium) `news_sentiment` — FinBERT-style scoring

```python
@register("news_sentiment")
def news_sentiment(bars, *, context=None,
                   lookback_hours=24, min_articles=2,
                   decay_half_life_hours=6.0):
    """Aggregate FinBERT polarity over recent headlines; emit a Signal proportional to
    time-decayed average sentiment, gated by article-count threshold."""
    news = (context or {}).get("news", {})
    sm   = (context or {}).get("sentiment_model")
    for sym, w in bars.items():
        ts_now = list(w)[-1].ts
        items = [n for n in news.get(sym, [])
                 if (ts_now - n["ts"]).total_seconds() / 3600 <= lookback_hours]
        if len(items) < min_articles:
            continue
        polarities = [n.get("polarity") or sm(n["headline"]) for n in items]
        weights = [0.5 ** ((ts_now - n["ts"]).total_seconds() / 3600 / decay_half_life_hours)
                   for n in items]
        wsum = sum(weights)
        if wsum == 0:
            continue
        avg = sum(p * wt for p, wt in zip(polarities, weights)) / wsum
        score = float(np.tanh(avg * 2))
        yield Signal(symbol=sym, ts=ts_now, score=score,
                     strategy="news_sentiment",
                     meta={"n_articles": len(items), "avg_polarity": avg})
```

- **Score map**: `tanh(2 × weighted_avg_polarity)`.
- **Edge / failure modes**: classification accuracy is solid [7]; trading edge is much smaller than classification accuracy implies. Beware survivorship and event-study bias [13, 14]. Cost-of-decision is essentially zero (FinBERT runs locally on CPU).

### 3.8 (Ambitious) `llm_committee` — TradingAgents-lite

A scaled-down multi-agent strategy: one batched call per universe per bar; structured-JSON response.

```python
@register("llm_committee")
def llm_committee(bars, *, context=None,
                  universe_max=30, cache_key_fn=None,
                  llm_client=None, model_name="claude-haiku-4.5"):
    """One batched call per (date, symbol_set, news_hash); structured-JSON response
    yields {ticker -> {score: [-1, 1], rationale: str}}."""
    news = (context or {}).get("news", {})
    bars_dict = {s: list(b)[-30:] for s, b in bars.items()}
    items = list(bars_dict.items())[:universe_max]
    cache_key = cache_key_fn(items, news) if cache_key_fn else None
    cached = (context or {}).get("llm_cache", {}).get(cache_key)
    if cached is not None:
        verdicts = cached
    else:
        prompt = _build_committee_prompt(items, news)
        verdicts = llm_client.complete_json(prompt, model=model_name)
        if cache_key is not None:
            (context or {}).setdefault("llm_cache", {})[cache_key] = verdicts
    for symbol, w in bars.items():
        v = verdicts.get(symbol.ticker)
        if v is None:
            continue
        score = max(-1.0, min(1.0, float(v["score"])))
        yield Signal(symbol=symbol, ts=w[-1].ts, score=score,
                     strategy="llm_committee",
                     meta={"rationale": v.get("rationale", "")[:200],
                           "model": model_name, "cache_hit": cached is not None})
```

- **Cost discipline (critical)**: at GPT-4o (~$2.50/$10 per million tokens, April 2026) and Claude Sonnet 4.6 (~$3/$15), a single batched committee call over 30 symbols with ~10K input + 2K output tokens costs about $0.045 (GPT-4o) or about $0.06 (Sonnet). At 4h cadence ≈ 6 calls/day → about $0.30–0.40/day. Default to **Haiku 4.5** ($1/$5) for routine calls; reserve Sonnet/Opus for end-of-week reflection. Cache by `(date, symbol_set, news_hash)`.
- **Edge / failure modes**: the published TradingAgents headline Sharpe (5.6–8.2 [8]) does not survive independent reproduction. The ACM AIFinTech 2026 reproducibility study found the framework underperformed buy-and-hold on out-of-sample data [22]; arXiv 2603.27539 attributes the published Sharpe to a bullish-regime artifact rather than alpha [23]. Treat as a research seam, not a production strategy. *If* it works in walk-forward, the value is probably in producing diversifying signals (low correlation to technical strategies) rather than higher absolute Sharpe.

### Coverage check

| Pick | Univariate | Cross-sec. | Context-aug. | Complexity |
|---|---|---|---|---|
| 3.1 donchian_breakout | ✅ | | | low |
| 3.2 bollinger_meanrev | ✅ | | | low |
| 3.3 xs_momentum | | ✅ | | medium |
| 3.4 pairs_zscore | | ✅* | (pairs via context) | medium |
| 3.5 pca_residual_revert | | ✅ | | medium |
| 3.6 pead_drift | | | ✅ (earnings) | medium |
| 3.7 news_sentiment | | | ✅ (news) | medium |
| 3.8 llm_committee | | ✅ | ✅ (news + LLM) | high |

\* multivariate / relative-value, technically a sub-class of cross-sectional.

---

## § 4. Existing Python packages worth leveraging

Verified currency as of May 2026.

### 4.1 `vectorbt`
Maintainer Oleg Polakov (polakowo). **License**: Apache 2.0 with Commons Clause (fair-code — usable, not resaleable). **Status**: actively maintained; v1.0.0 released, regular updates through 2026. **Use**: parameter sweeps offline; convert chosen kwargs into a hedger strategy. Closed-source `vectorbt PRO` (~$20/month) has more features — only if needed. **Data deps**: any pandas DataFrame.

### 4.2 `bt` (pmorissette)
**License**: MIT. **Status**: active — releases through March 2026, supports Python 3.13. **Use**: portfolio-level backtests, ERC/risk-parity sizing; useful for validating Sizer logic offline. Don't wrap as a Strategy.

### 4.3 `zipline-reloaded` + `pyfolio-reloaded` + `empyrical-reloaded` + `alphalens` (Stefan Jansen)
**License**: Apache 2.0. **Status**: active — `zipline-reloaded` 3.1.1 July 2025, supports Python 3.10–3.13. **Always use the `-reloaded` forks** — original Quantopian `pyfolio` and `zipline` are abandoned. **Use**: alphalens for factor diagnostics during research; pyfolio-reloaded for tear sheets. Don't wrap zipline — it is its own backtester.

### 4.4 `backtesting.py` (kernc)
**License**: AGPL-3.0 (strong copyleft — commercial deployment may be problematic). **Status**: active; v0.6.5 July 2025. Single-symbol only. The `lucit-backtesting` fork relicences. **Skip** — hedger's own backtester covers this and AGPL is a non-trivial constraint.

### 4.5 `qlib` (Microsoft)
**License**: MIT. **Status**: very actively maintained as of 2026 — 41.6K GitHub stars and 6.6K forks (per the `microsoft/qlib` branch listing, early May 2026), continuous commits, integrated with RD-Agent for autonomous factor R&D. Heavyweight. **Use**: train an `Alpha158`/`Alpha360` LightGBM offline, ship the model into hedger:

```python
@register("alpha158_lgbm")
def alpha158_lgbm(bars, *, context=None, model_path="alpha158_lgbm.pkl"):
    model = (context or {}).get("alpha_model") or _load_model(model_path)
    for sym, w in bars.items():
        feats = _extract_alpha158_features(w)   # mirror qlib feature extraction
        score = float(np.tanh(model.predict(feats)[-1]))
        yield Signal(symbol=sym, ts=list(w)[-1].ts, score=score,
                     strategy="alpha158_lgbm", meta={"model_version": model_path})
```

**Verify exact feature-extraction APIs against the qlib docs** — they have evolved.

### 4.6 `FinRL` (AI4Finance Foundation)
**License**: MIT. **Status**: legacy `master` last meaningfully updated December 2025; lab acknowledges substantial inactivity since November 2023. Active development moved to `FinRL-Trading` / FinRL-X and the FinRL Contests. **Use**: train policy offline, ship as a callable, reconstruct state from `bars`. Don't import the gym env into the Strategy itself.

### 4.7 `TradingAgents` (TauricResearch)
**License**: Apache 2.0. **Status**: very actively maintained — 65.6K GitHub stars and 12.7K forks as of early May 2026 (per the `TauricResearch/TradingAgents` branches page); v0.2.4 released 25 April 2026; Python 3.12–3.13. **Use**: run a daily TradingAgents pass offline, persist `{ticker: score}` JSON, expose via `context["llm_committee_scores"]`, emit Signals from the dict. **Honesty caveat**: independent 2026 reproductions do not validate the published numbers [22, 23].

### 4.8 `pandas-ta` — currently a mess; avoid
The original `twopirllc/pandas-ta` was archived and the PyPI package's history was wiped in 2025; a new maintainer published releases that some users have flagged as supply-chain-suspicious. **Do not trust the current PyPI `pandas-ta` without auditing.** Alternatives: `pandas-ta-classic` (community fork, NumPy-2 compatible), `pandas-ta-openbb` (MIT; explicit shutdown notice if unfunded by July 2026), or — simplest — `TA-Lib` (Python wrapper at `TA-Lib/ta-lib-python`, BSD-2-Clause, v0.6.8 October 2025) — actively maintained, Python 3.9–3.14 wheels.

### 4.9 `stockstats` — light TA wrapper around pandas
Maintained, MIT, lightweight. If you want a few indicators (RSI, MACD, KDJ) without the C dependency of TA-Lib.

### 4.10 `riskfolio-lib` — portfolio optimisation
Maintainer Dany Cajas. **License**: BSD-3-Clause. **Status**: active — v7.2.x in 2026, Python 3.9+. **Use**: Sizer plug-in (HRP, ERC, Black–Litterman). Verify against riskfolio docs — APIs have changed across major versions.

### 4.11 `quantstats` — tear-sheet reporting
Maintained (ranaroussi); Apache. Drop-in for the overnight reflection loop.

### 4.12 `tsfresh` — automated time-series features
Maintained; MIT. Use as a feature pre-processor for ML strategies. ~700 features × hundreds of names × thousands of bars is a memory hazard — use `MinimalFCParameters` / `EfficientFCParameters`.

### 4.13 `mlfinlab` and `arbitragelab` (Hudson & Thames)
Open-source on GitHub but governed by a non-standard, restrictive licence with paid business/enterprise tiers. **Do not depend on them inside hedger** without auditing the licence. Read the H&T write-ups for ideas; implement directly from references [5, 6] or use a thin in-tree implementation.

### 4.14 Small but useful
`pyfolio-reloaded` (tear sheets), `empyrical-reloaded` (metrics), `alphalens` (factor IC and quantile spread).

---

## § 5. Open questions & decisions

### 5.1 Asset universe — US equities only? Add crypto? ETFs only?
Equities give the deepest research literature and best fundamentals/news data; crypto gives 24/7 markets and easier shorting via perps but thinner fundamentals and persistent pump-and-dump contamination. **Recommendation (default first pass)**: about 50 highly liquid US ETFs (sector + factor: SPY, QQQ, IWM, XLE, XLF, XLK, MTUM, USMV, VLUE, VTV, VUG, EEM, EFA, TLT, GLD, …). Avoid single-name issues (earnings dates, gap risk, borrow availability). Add the top-20-by-volume crypto pairs as a second universe once the ETF system is stable.

### 5.2 Long-only vs. long/short
Long/short doubles the strategy space (cross-sectional, pairs, market-neutral) but introduces borrow costs and short-availability constraints. Alpaca supports shorting on most US equities/ETFs; many small-caps are not shortable. **Recommendation**: long/short on the ETF universe (all easily shortable); long-only when extending to single-names until the borrow-cost model is validated.

### 5.3 Acceptable data dependencies
Free (Alpaca + yfinance) is enough for technical strategies; Polygon ($) buys reliable intraday and corporate-action-adjusted history; Tiingo / Compustat ($$) buy fundamentals; Benzinga / RavenPack / Bloomberg ($$$) buy clean news. **Recommendation**: stay free/cheap for v1. Add Polygon-equivalent only when intraday fills are demonstrably needed. Don't pay for Compustat-grade fundamentals until a fundamentals-driven strategy has shown signs of life on Yahoo / Financial Modeling Prep.

### 5.4 Cadence commitment — 1h, 4h, or daily?
Shorter cadence = more bars and statistical power, but also more noise and more cost. Daily is robust and matches most academic literature; 4h captures intraday news; 1h runs into intraday-microstructure issues. **Recommendation**: **4h primary** for v1. Daily for any cross-sectional or factor strategy. Avoid 1h until the runner has been stress-tested under live latency.

### 5.5 LLM cost ceiling
The `llm_news` and `llm_committee` strategies can blow through hundreds of dollars per month if not cached. Knobs: batch size, cache-key granularity, cadence, model tier (Haiku 4.5 is roughly 5× cheaper than Sonnet 4.6 and roughly 20× cheaper than Opus 4.6). **Recommendation**: hard cap at **$1/day in dev, $10/day in paper, $30/day in live** until ROI is demonstrated. Default to **Claude Haiku 4.5** ($1/$5 per million tokens) for the routine multi-symbol scoring loop; reserve Sonnet 4.6 for end-of-week reflection. Always cache by `(date, symbol_set, news_hash)`.

---

## § 6. References

[1] Jegadeesh, N. & Titman, S. (1993). "Returns to buying winners and selling losers: implications for stock market efficiency." *Journal of Finance* 48(1): 65–91. [PDF](https://www.bauer.uh.edu/rsusmel/phd/jegadeesh-titman93.pdf).

[2] Moskowitz, T.J., Ooi, Y.H. & Pedersen, L.H. (2012). "Time series momentum." *Journal of Financial Economics* 104(2): 228–250. [ScienceDirect](https://www.sciencedirect.com/science/article/pii/S0304405X11002613). [Author PDF](http://docs.lhpedersen.com/TimeSeriesMomentum.pdf).

[3] Fama, E.F. & French, K.R. (1993). "Common risk factors in the returns on stocks and bonds." *Journal of Financial Economics* 33(1): 3–56.

[4] Fama, E.F. & French, K.R. (2015). "A five-factor asset pricing model." *Journal of Financial Economics* 116(1): 1–22. [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0304405X14002323).

[5] Gatev, E., Goetzmann, W.N. & Rouwenhorst, K.G. (2006). "Pairs trading: performance of a relative-value arbitrage rule." *Review of Financial Studies* 19(3): 797–827. [PDF](http://stat.wharton.upenn.edu/~steele/Courses/434/434Context/PairsTrading/PairsTradingGGR.pdf).

[6] Avellaneda, M. & Lee, J.-H. (2010). "Statistical arbitrage in the US equities market." *Quantitative Finance* 10(7): 761–782. [PDF](https://traders.studentorg.berkeley.edu/papers/Statistical%20arbitrage%20in%20the%20US%20equities%20market.pdf).

[7] Araci, D. (2019). "FinBERT: financial sentiment analysis with pre-trained language models." [arXiv:1908.10063](https://arxiv.org/abs/1908.10063).

[8] Xiao, Y., Sun, E., Luo, D. & Wang, W. (2024–2025). "TradingAgents: multi-agents LLM financial trading framework." [arXiv:2412.20138](https://arxiv.org/abs/2412.20138). Repo: [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents).

[9] Do, B.H. & Faff, R. (2010). "Does simple pairs trading still work?" *Financial Analysts Journal* 66(4): 83–95. [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1656954).

[10] Do, B.H. & Faff, R. (2012). "Are pairs trading profits robust to trading costs?" *Journal of Financial Research* 35(2): 261–287. [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1707125).

[11] Bernard, V.L. & Thomas, J.K. (1989). "Post-earnings-announcement drift: delayed price response or risk premium?" *Journal of Accounting Research* 27 (Supplement): 1–36.

[12] Review of PEAD literature (2024). *International Review of Financial Analysis*. [DOI 10.1016/j.irfa.2024.103922](https://doi.org/10.1016/j.irfa.2024.103922). See also [Wikipedia: Post–earnings-announcement drift](https://en.wikipedia.org/wiki/Post%E2%80%93earnings-announcement_drift) for a concise survey of the documented decay.

[13] Liu, H., Lin, Z. & Rojas, R.R. (2025). "Enhancing trading performance through sentiment analysis with large language models: evidence from the S&P 500." [arXiv:2507.09739](https://arxiv.org/abs/2507.09739).

[14] (2024). "Sentiment trading with large language models." [arXiv:2412.19245](https://arxiv.org/abs/2412.19245).

[15] Daniel, K. & Moskowitz, T.J. (2016). "Momentum crashes." *Journal of Financial Economics* 122(2): 221–247.

[16] Han, C., Kang, B. & Ryu, J. (2024). "Time-series and cross-sectional momentum in the cryptocurrency market: a comprehensive analysis under realistic assumptions." [SSRN 4675565](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565).

[17] Liu, Y. & Tsyvinski, A. (2021). "Risks and returns of cryptocurrency." *Review of Financial Studies* 34(6): 2689–2727.

[18] López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley.

[19] Liu, X.-Y. et al. (2020). "FinRL: a deep reinforcement learning library for automated stock trading in quantitative finance." [SSRN 3737859](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3737859). Repo: [AI4Finance-Foundation/FinRL](https://github.com/AI4Finance-Foundation/FinRL).

[20] Arnott, R., Harvey, C.R., Kalesnik, V. & Rattray, J. (2021). "Reports of Value's Death May Be Greatly Exaggerated." *Financial Analysts Journal* 77(1). [DOI 10.1080/0015198X.2020.1842704](https://doi.org/10.1080/0015198X.2020.1842704).

[21] Moreira, A. & Muir, T. (2017). "Volatility-managed portfolios." *Journal of Finance* 72(4): 1611–1644.

[22] (2026). "Reproducibility in the TradingAgents Framework." Proceedings of the 2026 ACM International Conference on AI and Fintech (AIFinTech '26). [DOI 10.1145/3800973.3801029](https://dl.acm.org/doi/10.1145/3800973.3801029).

[23] (March 2026). "Toward reliable evaluation of LLM-based financial multi-agent systems." [arXiv:2603.27539](https://arxiv.org/abs/2603.27539).

[24] Wang, K. et al. (2025). "FinRL Contests: benchmarking data-driven financial reinforcement learning agents." *Artificial Intelligence for Engineering* (Wiley). [DOI 10.1049/aie2.12004](https://doi.org/10.1049/aie2.12004).

[25] AQR Capital Management (September 2018). *Trend Following in Focus*. (Source for the SG Trend Index 2009–2018 underperformance figures cited in §2.1.) Anthropic API pricing reference (2026): [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing); OpenAI API pricing (2026): [OpenAI pricing](https://openai.com/api/pricing/) (used for cost-per-decision arithmetic in §2.11 and §3.8).

— *fin* —