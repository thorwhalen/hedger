"""Live runner — the application service that ticks once per cadence.

Single tick:
  1. fetch fresh bars (only the gap since last fetch)
  2. update the bar store
  3. run the active strategy(ies) on the latest window
  4. size signals -> decisions
  5. apply risk + tax middleware
  6. submit orders
  7. drain fills, update positions and stores
  8. log everything to the mall

This is *the* place to look when something goes wrong live.

Idempotency: the client_order_id includes both the runner's lifetime id and
the per-tick timestamp; a re-fired tick at the same wall-clock second produces
the same id, so the broker dedupes. Different ticks produce different ids, so
back-to-back ticks for the same symbol can both submit when needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Mapping

from hedger.base import (
    AssetClass,
    Bar,
    Broker,
    DataSource,
    Decision,
    Order,
    OrderType,
    Side,
    Sizer,
    Strategy,
    Symbol,
    utc_now,
)
from hedger.config import Config
from hedger.data.sources import make_source
from hedger.data.stores import (
    decision_to_dict,
    fill_to_dict,
    mall as default_mall,
    order_to_dict,
    positions_to_snapshot,
    signal_to_dict,
)
from hedger.execution.brokers import AlpacaBroker, make_broker
from hedger.execution.risk import default_risk_middleware
from hedger.execution.sizing import equal_weight_sizer
from hedger.notify import LogNotifier, Notifier, make_notifier
from hedger.strategies import get as get_strategy
from hedger.tax import get_policy
from hedger.util import get_logger

log = get_logger("hedger.live")


def _latest_fill_ts(mall: Mapping) -> "datetime | None":
    """Return the latest fill ts in mall["fills"] if any, else None."""
    from datetime import datetime as _dt
    try:
        store = mall["fills"]
    except (KeyError, TypeError):
        return None
    latest: _dt | None = None
    try:
        for v in store.values():
            ts_str = v.get("ts") if isinstance(v, dict) else None
            if not ts_str:
                continue
            try:
                ts = _dt.fromisoformat(ts_str)
            except ValueError:
                continue
            if latest is None or ts > latest:
                latest = ts
    except Exception:
        return None
    return latest


@dataclass
class Runner:
    """Composable application service. All seams are constructor-injected."""
    config: Config
    strategy: Strategy
    broker: Broker
    source: DataSource
    sizer: Sizer = equal_weight_sizer
    mall: Mapping = field(default_factory=default_mall)
    notifier: Notifier = field(default_factory=LogNotifier)
    news_source: object | None = None        # AlpacaNews-like, lazy
    news_refresh_minutes: int = 60
    run_id: str = field(default_factory=lambda: utc_now().isoformat(timespec="seconds"))
    _nav_today_open: float | None = field(default=None, init=False)
    _alerted_drawdown: bool = field(default=False, init=False)
    _last_alert_day: str | None = field(default=None, init=False)
    _last_news_fetch: "datetime | None" = field(default=None, init=False)

    def reconcile(self, *, snapshot_label: str = "startup") -> dict:
        """Compare broker's authoritative positions to our last mall snapshot.

        Logs ``position_drift`` for every symbol whose qty differs by more than
        a small epsilon, persists a fresh snapshot to ``mall["positions"]``,
        and returns a small summary dict the caller can act on.

        This is intentionally read-only on positions — the broker is the
        source of truth, the mall is a journal. If a divergence shows up the
        right response is human investigation, not a silent overwrite.
        """
        broker_positions = self.broker.positions()
        try:
            broker_nav = self.broker.nav()
        except Exception:
            broker_nav = None
        prev = self._last_snapshot()
        # First reconcile of a fresh journal is a baseline-establishment, not
        # a drift event — without a prior snapshot we have nothing to compare.
        drifts: list[dict] = []
        if prev is not None:
            prev_by_symbol: dict[str, dict] = {
                row["symbol"]: row for row in prev.get("positions", [])
            }
            eps = 1e-9
            for sym, pos in broker_positions.items():
                prev_qty = float(prev_by_symbol.get(str(sym), {}).get("qty", 0.0))
                if abs(pos.qty - prev_qty) > eps:
                    drifts.append({"symbol": str(sym),
                                   "prev_qty": prev_qty, "broker_qty": pos.qty})
            # Also flag symbols the journal saw that the broker no longer reports.
            for sym_str, prev_row in prev_by_symbol.items():
                if abs(float(prev_row.get("qty", 0.0))) <= eps:
                    continue
                if not any(str(s) == sym_str for s in broker_positions):
                    drifts.append({"symbol": sym_str,
                                   "prev_qty": float(prev_row["qty"]),
                                   "broker_qty": 0.0})
            for d in drifts:
                log.warning("position_drift", **d, label=snapshot_label)
            # Persist drifts to the mall so the brief / reflection cycle
            # has a non-log audit source. Each drift is one record.
            try:
                drift_store = self.mall["drifts"]
                drift_ts = utc_now().isoformat(timespec="seconds")
                for i, d in enumerate(drifts):
                    drift_store[(drift_ts, snapshot_label, str(i))] = {
                        **d, "ts": drift_ts, "label": snapshot_label,
                    }
            except (KeyError, TypeError):
                pass

        snap_ts = utc_now().isoformat(timespec="seconds")
        snapshot = positions_to_snapshot(broker_positions,
                                         nav=broker_nav, ts=snap_ts)
        try:
            self.mall["positions"][(snap_ts, snapshot_label)] = snapshot
        except (TypeError, KeyError):
            # If the mall doesn't have a positions slot (e.g. test mall), skip.
            pass

        return {
            "ts": snap_ts,
            "label": snapshot_label,
            "n_positions": len(broker_positions),
            "n_drifts": len(drifts),
            "drifts": drifts,
            "nav": broker_nav,
        }

    def _last_snapshot(self) -> dict | None:
        """Return the most recent positions snapshot from the mall, or None."""
        try:
            store = self.mall["positions"]
        except (KeyError, TypeError):
            return None
        try:
            keys = list(store)
        except Exception:
            return None
        if not keys:
            return None
        # Keys are (ts_iso, label) tuples; sort by ts.
        latest = max(keys, key=lambda k: k[0] if isinstance(k, tuple) else str(k))
        try:
            return store[latest]
        except KeyError:
            return None

    def refresh_news(self, symbols: list[Symbol]) -> int:
        """Best-effort: pull fresh news into mall["news"]. Returns # ingested.

        Skips if no news_source is configured or if last fetch was within
        ``news_refresh_minutes``. Idempotent: persisted by news id, so
        re-runs don't duplicate.
        """
        if self.news_source is None:
            return 0
        now = utc_now()
        if (self._last_news_fetch is not None
                and (now - self._last_news_fetch).total_seconds()
                < self.news_refresh_minutes * 60):
            return 0
        tickers = [s.ticker for s in symbols if s.asset_class is not AssetClass.CRYPTO]
        if not tickers:
            return 0
        ingested = 0
        try:
            for item in self.news_source.fetch(
                tickers, start=now - timedelta(hours=24), limit=50,
            ):
                key = (item.get("created_at") or now.isoformat(),
                       str(item.get("id") or ingested))
                self.mall["news"][key] = item
                ingested += 1
        except Exception as e:
            log.warning("news_fetch_failed",
                        error=f"{type(e).__name__}: {e}")
            return 0
        self._last_news_fetch = now
        return ingested

    def news_context(self, symbols: list[Symbol], *, hours: int = 24) -> dict:
        """Build the {symbol_str: [headlines]} mapping llm_news consumes."""
        try:
            news_store = self.mall["news"]
        except (KeyError, TypeError):
            return {}
        cutoff = utc_now() - timedelta(hours=hours)
        out: dict[str, list[str]] = {}
        sym_strs = {str(s): s for s in symbols}
        ticker_to_symstr: dict[str, str] = {s.ticker: str(s) for s in symbols}
        for v in news_store.values():
            try:
                created = datetime.fromisoformat(v["created_at"])
            except (KeyError, ValueError, TypeError):
                continue
            if created < cutoff:
                continue
            headline = v.get("headline") or ""
            if not headline:
                continue
            for t in v.get("symbols", []):
                # Map by raw ticker; the runner's universe converts to Symbol.
                sym_str = ticker_to_symstr.get(t)
                if sym_str:
                    out.setdefault(sym_str, []).append(headline)
        return out

    def universe(self) -> list[Symbol]:
        # Naive parse: 'AAPL' -> equity, 'BTC/USD' -> crypto. Override in config.
        out: list[Symbol] = []
        for t in self.config.universe:
            ac = AssetClass.CRYPTO if "/" in t else AssetClass.EQUITY
            out.append(Symbol(ticker=t, asset_class=ac))
        return out

    def fetch_window(self, symbol: Symbol, lookback_bars: int = 200) -> list[Bar]:
        """Return the most recent N bars for `symbol`, gap-filling from source.

        Reads cached bars from ``mall["bars"]`` first; only fetches the gap
        between the newest cached bar and now. This drops API usage from
        ``lookback_bars`` per tick to ``new_bars_since_last_tick`` after the
        first warm-up.
        """
        from hedger.data.stores import BarStore  # local import keeps top-level light
        end = utc_now()
        # Alpaca free-tier stock data has a ~15-minute delay; pull back a hair.
        if symbol.asset_class is not AssetClass.CRYPTO and self.source.name == "alpaca":
            end -= timedelta(minutes=20)
        delta = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 60 * 24}.get(
            self.config.timeframe, 60
        )
        full_start = end - timedelta(minutes=delta * (lookback_bars + 5))

        store = self.mall.get("bars") if isinstance(self.mall, Mapping) else None
        cached: list[Bar] = []
        if isinstance(store, BarStore):
            cached = store.read_bars(symbol, self.config.timeframe,
                                     start=full_start, end=end)

        # If cache covers the whole window with a recent-enough tail, return it.
        if cached:
            last_ts = cached[-1].ts
            # Need to pull only bars newer than the last cached one. Add one
            # bar's worth of overlap to be safe against partial-bar caching.
            fetch_start = last_ts - timedelta(minutes=delta)
            if fetch_start >= end:
                return cached
        else:
            fetch_start = full_start

        new_bars = list(self.source.bars(
            symbol, start=fetch_start, end=end, timeframe=self.config.timeframe,
        ))
        if new_bars and isinstance(store, BarStore):
            try:
                store.write_bars(new_bars, timeframe=self.config.timeframe)
            except Exception as e:
                log.warning("bar_cache_write_failed", symbol=str(symbol),
                            error=f"{type(e).__name__}: {e}")

        # Merge cached + new, dedup by ts.
        if not cached:
            return new_bars
        seen: dict = {}
        for b in cached + new_bars:
            seen[b.ts] = b
        merged = [seen[k] for k in sorted(seen)]
        return merged

    def tick(self) -> dict:
        """Run one full pipeline iteration. Returns a small summary dict."""
        tick_ts = utc_now().isoformat(timespec="seconds")
        symbols = self.universe()

        # Optionally skip equity work outside market hours when using Alpaca.
        market_open = True
        if isinstance(self.broker, AlpacaBroker):
            market_open = self.broker.is_market_open()

        bars: dict[Symbol, list[Bar]] = {}
        for s in symbols:
            try:
                bars[s] = self.fetch_window(s)
            except Exception as e:
                log.warning("fetch_failed", symbol=str(s), error=str(e))
                bars[s] = []

        # 1. Strategy
        # Refresh news (cheap and idempotent if news_source is set), build
        # context. llm_news consumes context['news']; other strategies ignore.
        n_news_ingested = self.refresh_news(symbols)
        context = {"news": self.news_context(symbols), "tick_ts": tick_ts}
        try:
            signals = list(self.strategy(bars, context=context))
        except TypeError:
            # Strategy doesn't accept context kwarg — backward-compat fallback.
            signals = list(self.strategy(bars))
        for s in signals:
            self.mall["signals"][(s.strategy, str(s.symbol), s.ts.isoformat())] = signal_to_dict(s)

        # 2. Sizing
        positions = self.broker.positions()
        nav = self.broker.nav()
        decisions = list(self.sizer(signals, positions=positions, nav=nav))

        # Track today's opening NAV for drawdown calculation.
        today = utc_now().date().isoformat()
        if self._last_alert_day != today:
            self._nav_today_open = nav
            self._alerted_drawdown = False
            self._last_alert_day = today
        nav_today_open_value = self._nav_today_open or nav

        # 3. Risk + Tax middleware
        risk_mw = default_risk_middleware(
            self.config.risk,
            nav_today_open=lambda: nav_today_open_value,
            nav_now=self.broker.nav,
        )
        tax_policy = get_policy(self.config.tax_policy)
        approved: list[Decision] = []
        n_vetoes = 0
        for d in decisions:
            d2 = risk_mw(d)
            if d2 is None:
                n_vetoes += 1
                self.notifier.notify(
                    "warning", "risk middleware veto",
                    symbol=str(d.symbol), target_weight=d.target_weight,
                    rationale=d.rationale,
                )
                continue
            d3 = tax_policy(d2, positions=positions, history={})
            if d3 is None:
                n_vetoes += 1
                self.notifier.notify(
                    "warning", "tax policy veto",
                    symbol=str(d.symbol), target_weight=d.target_weight,
                    policy=getattr(tax_policy, "name", "?"),
                )
                continue
            approved.append(d3)
            self.mall["decisions"][(self.run_id, str(d3.symbol), d3.ts.isoformat())] = \
                decision_to_dict(d3)

        # 4. Convert to orders and submit
        last_close = {s: (bars[s][-1].close if bars[s] else 0.0) for s in symbols}
        n_submitted = 0
        n_skipped_market_closed = 0
        for d in approved:
            px = last_close.get(d.symbol, 0.0)
            if px <= 0:
                continue
            target_notional = d.target_weight * nav
            current = positions.get(d.symbol)
            current_notional = (current.qty * px) if current else 0.0
            qty = (target_notional - current_notional) / px
            if abs(qty * px) < max(1.0, 0.0005 * nav):  # ignore dust trades
                continue
            if not market_open and d.symbol.asset_class is not AssetClass.CRYPTO:
                n_skipped_market_closed += 1
                log.info("skip_equity_outside_hours", symbol=str(d.symbol),
                         qty=qty, run_id=self.run_id)
                continue
            client_order_id = f"{self.run_id}:{tick_ts}:{d.symbol}"
            order = Order(
                symbol=d.symbol,
                side=Side.BUY if qty > 0 else Side.SELL,
                qty=abs(qty),
                order_type=OrderType.MARKET,
                client_order_id=client_order_id,
            )
            try:
                order_id = self.broker.submit(order)
            except Exception as e:
                # Common cause: duplicate client_order_id on retry; safe to swallow.
                log.warning("submit_failed", symbol=str(d.symbol),
                            error=f"{type(e).__name__}: {e}")
                continue
            self.mall["orders"][(self.run_id, client_order_id)] = order_to_dict(order)
            log.info("order_submitted", id=order_id, symbol=str(d.symbol),
                     side=order.side.value, qty=order.qty)
            n_submitted += 1

        # 5. Drain fills
        n_fills = 0
        for f in self.broker.fills():
            self.mall["fills"][(self.run_id, f.order_id)] = fill_to_dict(f)
            n_fills += 1

        nav_after = self.broker.nav()

        # Drawdown alert: notify once per day when intraday loss crosses the
        # configured threshold. The risk-middleware circuit-breaker is a
        # harder gate; this alert is an *earlier* warning shot for humans.
        if (nav_today_open_value > 0 and not self._alerted_drawdown):
            loss_pct = (nav_today_open_value - nav_after) / nav_today_open_value
            threshold = self.config.notify.drawdown_alert_pct
            if loss_pct >= threshold:
                self._alerted_drawdown = True
                self.notifier.notify(
                    "warning", "intraday drawdown alert",
                    loss_pct=round(loss_pct, 4),
                    threshold=threshold,
                    nav_open=nav_today_open_value,
                    nav_now=nav_after,
                )

        # Per-tick positions snapshot (silent on success, logs drift if any).
        try:
            self.reconcile(snapshot_label=f"tick:{tick_ts}")
        except Exception as e:
            log.warning("tick_reconcile_failed",
                        error=f"{type(e).__name__}: {e}")

        return {
            "ts": utc_now().isoformat(),
            "tick_ts": tick_ts,
            "market_open": market_open,
            "n_signals": len(signals),
            "n_decisions": len(approved),
            "n_vetoes": n_vetoes,
            "n_orders_submitted": n_submitted,
            "n_skipped_market_closed": n_skipped_market_closed,
            "n_fills": n_fills,
            "nav_before": nav,
            "nav_after": nav_after,
        }


def make_runner(cfg: Config | None = None, *, strategy_name: str = "sma_crossover") -> Runner:
    """Factory wiring config -> runner. The single place broker/source live."""
    cfg = cfg or Config()
    source = make_source(cfg.data.primary)
    broker = make_broker(cfg.broker.name)
    strat = get_strategy(strategy_name)
    try:
        notifier = make_notifier(cfg.notify.kind)
    except Exception as e:
        log.warning("notifier_unavailable", spec=cfg.notify.kind,
                    error=f"{type(e).__name__}: {e}")
        notifier = LogNotifier()
    # News source — best effort; not all setups have alpaca creds, and not
    # all strategies need news. Failures are silent; llm_news degrades to
    # "no opinion" rather than erroring.
    news_source = None
    try:
        from hedger.data.sources import AlpacaNews
        news_source = AlpacaNews()
    except Exception as e:
        log.info("news_source_unavailable", error=f"{type(e).__name__}: {e}")
    runner = Runner(config=cfg, strategy=strat, broker=broker, source=source,
                    notifier=notifier, news_source=news_source)
    # Best-effort: if we're talking to Alpaca, kick off the fill stream so we
    # don't have to poll. Polling is the fallback in fills().
    if isinstance(broker, AlpacaBroker):
        def _on_stream_event(event_name, ctx):
            level = "warning" if event_name == "died" else "info"
            try:
                runner.notifier.notify(level, f"alpaca fill stream: {event_name}",
                                       **ctx)
            except Exception:
                pass
        try:
            broker.start_fill_stream(on_stream_event=_on_stream_event)
        except Exception as e:
            log.info("fill_stream_unavailable", error=f"{type(e).__name__}: {e}")
        # Seed the polling watermark from the mall so a process restart
        # doesn't re-emit every fill from the last 24h.
        try:
            seed = _latest_fill_ts(runner.mall)
            if seed is not None:
                broker.seed_fill_watermark(seed)
        except Exception as e:
            log.info("fill_watermark_seed_failed",
                     error=f"{type(e).__name__}: {e}")
    # Snapshot + reconcile against any prior journal — surfaces drift that
    # accumulated while the runner wasn't ticking (e.g. process restart,
    # manual broker activity).
    try:
        runner.reconcile(snapshot_label="startup")
    except Exception as e:
        log.warning("startup_reconcile_failed",
                    error=f"{type(e).__name__}: {e}")
    return runner


__all__ = ["Runner", "make_runner"]
