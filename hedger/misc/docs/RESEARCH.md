# Self-Improving Algorithmic Trading Bot: Research and Design

**Author:** Thor Whalen
**Date:** 2026-05-05
**Status:** Initial scaffold research, version 0.1

---

## Abstract

This document surveys the tooling, market-structure, and methodological choices behind `hedger` — a self-improving algorithmic trading bot designed to run on a single server, with a nightly Claude-Code-driven reflection cycle. We compare backtesting frameworks [1–3], broker APIs accessible from Europe [4–6], LLM-augmented trading methodologies [7–10], and tax/regulatory constraints that shape design [11–13]. The conclusions justify an architecture that defers as many decisions as possible to runtime configuration, exposing plugin seams for strategies, data sources, brokers, and tax policies.

---

## 1. Backtesting and Execution Frameworks

The Python algorithmic-trading ecosystem has consolidated around three families of frameworks, each with a different latency/realism trade-off.

**VectorBT** is a vectorised backtester optimised for parameter sweeps and signal research. It runs on NumPy/Numba and can evaluate millions of parameter combinations in minutes [1]. Its limitation is the standard one for vectorised engines: order-by-order causality is approximated, which can hide bugs that only surface in event-driven execution.

**NautilusTrader** is an open-source, event-driven platform written in Rust with Python bindings, built specifically so that the *same* strategy code runs in backtest, paper, and live without modification [1]. This "code-once" property is the dominant reason to favour it for any system that intends to graduate to live trading.

**Backtrader** remains the most-cited entry-level event-driven framework [2]. It is mature but unmaintained relative to newer tools, and its execution model is single-threaded.

The pragmatic workflow [1] is: VectorBT for hyperparameter sweeps and idea-screening, then port survivors to NautilusTrader (or our `PaperBroker`) for execution-realistic validation including slippage, fees, and partial fills. The `hedger` scaffold reflects this directly — `backtest_simple()` uses `PaperBroker` with the *same* code path the live runner uses, and a `backtest_vectorbt()` stub is reserved for vectorised sweeps.

A representative survey of the broader open-source ecosystem (over a hundred frameworks, libraries, and brokers indexed) is maintained at the awesome-systematic-trading list [3].

---

## 2. Broker APIs Accessible from Europe

For a European-based retail/prosumer setup with under ~$100k AUM, the best-documented and lowest-friction options as of 2026 are:

**Alpaca** ranks first in BrokerChooser's 2026 survey of best brokers for algorithmic trading in Europe [4]. It offers commission-free US stocks and ETFs, free unlimited paper trading, REST and WebSocket APIs, and crypto trading. Its REST API allows 200 calls/minute on the free tier and 1,000/minute on funded accounts; WebSocket data is unlimited [5]. Critically, **paper trading uses the same API surface as live**, which means our `AlpacaBroker` class can run identically against either by toggling a single flag.

**Interactive Brokers** (IBKR) is the heavyweight choice with the broadest instrument coverage but a steeper API (TWS/IB Gateway), and tighter rate limits — 50 order messages/second, 100 simultaneous market-data lines, 10 historical-data requests/second [5]. It is the right answer once AUM and instrument-breadth justify the integration cost.

**OANDA** is the recommended specialist for FX and CFDs [4].

**Trading 212 and DEGIRO** — popular among European retail — do **not** offer official APIs [4]. Reverse-engineered libraries exist, but their use is grounds for account closure. They are excluded from `hedger` by design.

Latency benchmarks from the same source [5]: REST round-trip 80–150 ms; WebSocket 15–40 ms; Polygon's WebSocket median is 4–12 ms. For any cadence below ~1 minute, WebSocket-driven event handlers are mandatory; for daily/4-hour cadences, REST polling is fine and simpler.

The macro context: roughly 60–73% of US equity volume is now algorithmic, and retail algorithmic activity has surged ~340% since 2019 [5]. The "retail quant" niche `hedger` targets is well-supported by tooling.

---

## 3. LLM-Augmented Trading

LLM use in trading has matured from "ask GPT for stock tips" to multi-agent architectures with role specialisation.

**TradingAgents** [7] is the canonical reference framework: it spawns a fundamental-analyst agent, a sentiment agent, a technical agent, a risk-manager agent, and a portfolio-manager agent, and lets them debate before producing a decision. Multiple replications have shown that the debate step measurably improves robustness over single-agent setups [8]. The framework is open-source.

Hybrid approaches that pair classical sentiment models (FinBERT) with frontier LLMs (Gemini, Claude) for higher-order reasoning have shown alpha in recent peer-reviewed work [9]. The pattern is: cheap model for high-volume scoring, expensive model for synthesis and edge-cases — the same cost-discipline pattern we encode in `hedger`'s data-pipeline skill.

For benchmarking, the Open-Finance-Lab FinLLM Leaderboard [10] tracks LLM performance on financial tasks specifically, which is more useful than general-purpose leaderboards when picking a model for a sentiment or fundamentals task.

A recent FinRL+sentiment study [14] shows that combining reinforcement-learning policies with LLM-derived sentiment features outperforms either component alone, supporting the architectural choice in `hedger` to treat LLM outputs as **features stored in the mall**, consumed by strategies, rather than as direct decision-makers.

A 2024 survey of LLM agents in finance [8] catalogues failure modes — overconfidence, prompt-injection from news content, look-ahead bias from training-data contamination — that the `hedger` scaffold mitigates with: (a) deterministic feature caching, (b) a risk middleware that can veto any decision regardless of source, (c) the strategy contract that forbids look-ahead access.

---

## 4. Tax and Regulatory Considerations

Whether to bake tax-aware logic into trading decisions is a question of scale. The honest answer for sub-six-figure portfolios with moderate turnover is: **not in the decision loop, but yes in the bookkeeping**.

**US wash-sale rules** [11, 13] forbid claiming a loss on a security if a "substantially identical" security is purchased within 30 days before or after the sale (a 61-day window total). The rule applies across all accounts the taxpayer controls, including IRAs. For an algorithmic system that may rebuy positions frequently, this is decision-relevant: a naive rebalance can disallow legitimate losses. `hedger` ships with a `USWashSalePolicy` that vetoes loss-sells inside the window.

**Crypto is currently exempt** from US wash-sale rules [11], although legislative proposals to close this gap are perennial. This is one of several reasons crypto is the recommended starting venue for `hedger` (see §6).

**EU treatment** varies sharply by member state [12]. France applies the *Prélèvement Forfaitaire Unique* (30% flat) on capital gains; Germany has a 25% flat rate plus solidarity surcharge with €1,000 annual allowance; Belgium has historically been near-zero on long-term capital gains for individuals. There is no EU-level harmonisation, so a tax policy that hard-codes one jurisdiction is wrong by construction. `hedger`'s `TaxPolicy` is a Protocol; jurisdiction-specific implementations live as plugins.

**Tax-loss harvesting at the high end** [13] is a real source of alpha — there is now a small industry of algorithmic harvesting services serving high-net-worth individuals. The complexity is only worth it above roughly 6-figure NAV with active turnover and meaningful realised gains to offset; below that, a periodic year-end review is sufficient.

Conclusion: `hedger`'s default `tax_policy = "none"` is correct for paper trading and small live portfolios. Operators in jurisdictions with wash-sale-style rules should activate `us_wash_sale` (or write a local equivalent) before going live.

---

## 5. Trading Frequency

Frequency choice is constrained by four costs: market-data infrastructure, broker rate limits, LLM inference, and signal-to-noise.

| Cadence | Data feed | Broker API | LLM cost/day | Risk profile |
|---|---|---|---|---|
| Tick / sub-second | Direct exchange or Polygon WS | Co-located, FIX | N/A (too slow) | HFT — out of scope |
| 1-minute | WebSocket (15–40 ms) | WebSocket orders | ~$50–200 if LLM-per-bar | Microstructure-driven |
| 5–15 minute | WebSocket or fast REST | REST OK | ~$5–20 | Intraday momentum |
| **1-hour / 4-hour** | **REST polling fine** | **REST fine** | **~$0.50–2** | **Sweet spot for LLM** |
| Daily | Once-a-day batch | REST | <$0.20 | Position trading |

For an LLM-augmented system on a single server, the **1-hour to 4-hour cadence is the sweet spot**: it amortises LLM inference cost over many bars; it survives a transient broker outage of a few minutes without missing a decision; and it operates above the noise floor where most strategies have detectable edge. Below 5 minutes, infrastructure cost climbs sharply for diminishing returns. Above daily, the reflection-cycle insight ("what happened today, what should I learn") loses fidelity.

The `hedger` scheduler defaults to a 4-hour cron during market hours, with the nightly reflection at 22:00 local time.

---

## 6. Recommended Starting Configuration

Given the survey above, the highest-information starting setup is:

1. **Paper trading on Alpaca**, 4-hour cadence, US equities/ETFs (SPY, QQQ, IWM, sector ETFs). Free, well-documented, identical API to live [4, 5].
2. **Optionally crypto on Kraken via CCXT**, daily cadence, top-5-by-cap. Crypto is 24/7 (no market-hours edge cases for the scheduler), exempt from wash-sale rules [11], and supports fractional positions for sub-$10k accounts.
3. **Two strategies running in parallel**: a rule-based SMA-crossover (deterministic, audit-friendly, free to run) and an LLM-news-sentiment scorer (uses Haiku for routine scoring, escalates to Sonnet for ambiguous signals). The shipped code includes both.
4. **Risk middleware always on**: cap position weight at 10%, gross exposure at 100%, daily loss circuit-breaker at 2%. These are sacred — the reflection cycle is forbidden from weakening them without explicit human review.
5. **Reflection cycle nightly 22:00–06:00 CET**, budgeted at 8 hours of Claude Code time, with mandatory git-tag-and-rollback if pytest fails after any change.

After 2–4 weeks of paper trading with positive risk-adjusted returns and a clean reflection-cycle history, graduate one strategy at a time to a small live capital allocation.

---

## 7. Open Questions Worth Researching Further

- **Capital level and instrument selection.** Below ~$10k, fractional crypto or fractional-share US ETFs are the only viable instruments; full-share US stocks lock too much capital per position to diversify. Define this before live trading.
- **Data licensing.** `yfinance` is free but rate-limited and has occasional gaps; Polygon (~$30/mo for the basics, $200+ for high-quality WS) is the next tier; direct exchange feeds are 4-figure-monthly. Match data quality to cadence — daily strategies don't need WebSocket.
- **MiFID II reporting** if trading EU-listed instruments through an EU-licensed broker; record-keeping requirements are non-trivial and the `mall` should be treated as audit-grade.
- **Failover and reconciliation.** What happens if the server is down during a scheduled tick? `hedger`'s scheduler has misfire-grace, but position reconciliation against the broker on every restart is not yet implemented and should be Reflection Cycle #1's pick-up.
- **Monitoring and alerting.** A Telegram or Discord webhook on (i) any rolled-back reflection cycle, (ii) any risk-middleware veto, (iii) any drawdown >1% intraday. Out-of-scope for v0.1, in-scope for v0.2.
- **Version-pinning policy.** Currently `pyproject.toml` is unpinned. Once the reflection cycle is running stably, generate a `requirements.lock` and pin.

---

## REFERENCES

[1] python.financial. *Python Backtesting Frameworks Compared: VectorBT, NautilusTrader, Backtrader.* Available from: [https://python.financial/](https://python.financial/)

[2] kernc. *backtesting.py — fast, simple, intuitive backtesting framework for quantitative trading strategies.* GitHub. Available from: [https://github.com/kernc/backtesting.py](https://github.com/kernc/backtesting.py)

[3] wangzhe3224. *awesome-systematic-trading — a curated list of systematic-trading libraries, brokers, frameworks and resources.* GitHub. Available from: [https://github.com/wangzhe3224/awesome-systematic-trading](https://github.com/wangzhe3224/awesome-systematic-trading)

[4] BrokerChooser. *Best Brokers for Algorithmic Trading in Europe, 2026 edition.* Available from: [https://brokerchooser.com/best-brokers/best-brokers-for-algo-trading-in-europe](https://brokerchooser.com/best-brokers/best-brokers-for-algo-trading-in-europe)

[5] TradeAlgo. *Best Broker APIs for Algorithmic Trading in 2026.* Available from: [https://www.tradealgo.com/trading-guides/tools/best-broker-apis-for-algorithmic-trading-in-2026](https://www.tradealgo.com/trading-guides/tools/best-broker-apis-for-algorithmic-trading-in-2026)

[6] Alpaca Markets. *Paper Trading API documentation.* Available from: [https://docs.alpaca.markets/docs/paper-trading](https://docs.alpaca.markets/docs/paper-trading)

[7] TradingAgents. *Multi-Agents LLM Financial Trading Framework — project page.* Available from: [https://tradingagents-ai.github.io/](https://tradingagents-ai.github.io/) (paper: arXiv:2412.20138, [https://arxiv.org/abs/2412.20138](https://arxiv.org/abs/2412.20138))

[8] *A Survey of Large Language Model Agents for Question Answering and Decision Making in Finance.* arXiv:2408.06361. Available from: [https://arxiv.org/html/2408.06361v2](https://arxiv.org/html/2408.06361v2)

[9] *Hybrid FinBERT–LLM Sentiment Models for Equity Trading.* AI (MDPI), 7(4):138. Available from: [https://www.mdpi.com/2673-2688/7/4/138](https://www.mdpi.com/2673-2688/7/4/138)

[10] Open-Finance-Lab. *FinLLM Leaderboard.* Available from: [https://finllm-leaderboard.readthedocs.io/](https://finllm-leaderboard.readthedocs.io/)

[11] Terms.law. *Wash Sale Rules and Algorithmic Trading.* Available from: [https://terms.law/Trading-Legal/guides/wash-sale-algo-trading.html](https://terms.law/Trading-Legal/guides/wash-sale-algo-trading.html) (cross-reference: Charles Schwab, *A Primer on Wash Sales*, [https://www.schwab.com/learn/story/primer-on-wash-sales](https://www.schwab.com/learn/story/primer-on-wash-sales))

[12] eToro. *What is Tax Harvesting? — overview of European treatments.* Available from: [https://www.etoro.com/investing/what-is-tax-harvesting/](https://www.etoro.com/investing/what-is-tax-harvesting/)

[13] ABC Money. *The Tax-Loss Harvesting Machine: the algorithmic edge keeping the 1% steps ahead of the IRS.* April 2026. Available from: [https://www.abcmoney.co.uk/2026/04/the-tax-loss-harvesting-machine-the-algorithmic-edge-keeping-the-1-steps-ahead-of-the-irs](https://www.abcmoney.co.uk/2026/04/the-tax-loss-harvesting-machine-the-algorithmic-edge-keeping-the-1-steps-ahead-of-the-irs)

[14] *FinRL with Sentiment-Augmented State Representation.* arXiv:2411.11059. Available from: [https://arxiv.org/pdf/2411.11059](https://arxiv.org/pdf/2411.11059)
