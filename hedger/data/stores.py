"""Storage as Mappings.

Per Thor's conventions, every persistent entity is exposed as a Mapping (or
MutableMapping). The same code works against a dict-in-memory, a parquet
dir on disk, or (later) blob storage — whatever you bind into the mall.

The `mall()` factory returns the canonical {name -> store} dict so callers
can do `mall['bars'][key] = bar` rather than threading paths through APIs.
"""

from __future__ import annotations

import json
from collections.abc import MutableMapping
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

import pandas as pd

from hedger.base import (
    AssetClass,
    Bar,
    Decision,
    Fill,
    Order,
    Side,
    OrderType,
    TimeInForce,
    Signal,
    Symbol,
)


# ---------------------------------------------------------------------------
# Generic JSON-line store (works for anything dataclass-shaped)
# ---------------------------------------------------------------------------

class JsonlStore(MutableMapping):
    """A keyed JSON-lines store on disk. Cheap, inspectable with `head`/`jq`.

    Keys are tuples or strings; values are JSON-serialisable dicts. Records
    accumulate; deletion rewrites the file. Intentionally simple — for hot
    paths, swap in a parquet/duckdb-backed store with the same interface.

    >>> import tempfile; d = tempfile.mkdtemp()
    >>> s = JsonlStore(Path(d) / 'x.jsonl')
    >>> s[('a', 1)] = {'foo': 'bar'}
    >>> s[('a', 1)]
    {'foo': 'bar'}
    >>> ('a', 1) in s
    True
    >>> len(s)
    1
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[Any, dict] | None = None

    def _load(self) -> dict[Any, dict]:
        if self._cache is not None:
            return self._cache
        out: dict[Any, dict] = {}
        if self.path.exists():
            with self.path.open("r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    key = _to_hashable(rec["__key__"])
                    out[key] = rec["value"]
        self._cache = out
        return out

    def _flush(self) -> None:
        cache = self._load()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w") as f:
            for k, v in cache.items():
                f.write(json.dumps({"__key__": list(k) if isinstance(k, tuple) else k,
                                    "value": v}, default=str))
                f.write("\n")
        tmp.replace(self.path)

    def __getitem__(self, k):
        return self._load()[k]

    def __setitem__(self, k, v):
        self._load()[k] = v
        # Append-only fast path; rewrite happens on delete or close.
        with self.path.open("a") as f:
            f.write(json.dumps({"__key__": list(k) if isinstance(k, tuple) else k,
                                "value": v}, default=str))
            f.write("\n")

    def __delitem__(self, k):
        del self._load()[k]
        self._flush()

    def __iter__(self) -> Iterator:
        return iter(self._load())

    def __len__(self) -> int:
        return len(self._load())


def _to_hashable(v):
    if isinstance(v, list):
        return tuple(_to_hashable(x) for x in v)
    return v


# ---------------------------------------------------------------------------
# BarStore — parquet-backed, partitioned by (symbol, timeframe)
# ---------------------------------------------------------------------------

class BarStore(MutableMapping):
    """Mapping interface over parquet files of OHLCV bars.

    Key: (symbol_str, timeframe).
    Value: a pandas DataFrame indexed by tz-aware UTC timestamp.

    Set semantics: assignment **upserts** (merges on index, overwrites
    overlapping rows). This mirrors how you actually maintain a market-data
    cache — you fetch a recent window, hand it to the store, and it updates
    only what changed.

    >>> import tempfile, pandas as pd
    >>> d = tempfile.mkdtemp()
    >>> store = BarStore(d)
    >>> df = pd.DataFrame(
    ...     {'open': [1.0], 'high': [1.1], 'low': [0.9], 'close': [1.05], 'volume': [10]},
    ...     index=pd.to_datetime(['2026-01-02'], utc=True),
    ... )
    >>> store[('AAPL', '1d')] = df
    >>> ('AAPL', '1d') in store
    True
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: tuple[str, str]) -> Path:
        sym, tf = key
        safe = sym.replace("/", "_").replace(":", "__")
        return self.root / f"{safe}__{tf}.parquet"

    def __getitem__(self, k: tuple[str, str]) -> pd.DataFrame:
        p = self._path(k)
        if not p.exists():
            raise KeyError(k)
        return pd.read_parquet(p)

    def __setitem__(self, k: tuple[str, str], v: pd.DataFrame) -> None:
        p = self._path(k)
        if p.exists():
            existing = pd.read_parquet(p)
            v = pd.concat([existing, v]).sort_index()
            v = v[~v.index.duplicated(keep="last")]
        v.to_parquet(p)

    def __delitem__(self, k):
        p = self._path(k)
        if not p.exists():
            raise KeyError(k)
        p.unlink()

    def __iter__(self) -> Iterator[tuple[str, str]]:
        for p in self.root.glob("*.parquet"):
            stem = p.stem
            sym, _, tf = stem.rpartition("__")
            yield (sym.replace("__", ":").replace("_", "/", 1), tf)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def write_bars(self, bars: Iterable[Bar], *, timeframe: str) -> int:
        """Convenience: ingest an iterable of Bar dataclasses, grouped by symbol.

        ``Bar`` itself doesn't carry a timeframe (it's a fixed-cadence row), so
        the caller passes it once for the whole batch. Returns the count of
        rows written across all symbols.

        >>> import tempfile
        >>> from hedger.base import AssetClass, Bar, Symbol
        >>> from datetime import datetime, timezone
        >>> store = BarStore(tempfile.mkdtemp())
        >>> sym = Symbol('AAPL', AssetClass.EQUITY)
        >>> b = Bar(symbol=sym, ts=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ...         open=1, high=2, low=0.5, close=1.5, volume=100)
        >>> store.write_bars([b], timeframe='1d')
        1
        """
        rows_by_sym: dict[str, list[dict]] = {}
        for b in bars:
            ts = b.ts
            if isinstance(ts, datetime) and ts.tzinfo is None:
                # The cache index is tz-aware UTC; coerce naive timestamps.
                ts = ts.replace(tzinfo=__import__("datetime").timezone.utc)
            rows_by_sym.setdefault(str(b.symbol), []).append(
                {"ts": ts, "open": b.open, "high": b.high, "low": b.low,
                 "close": b.close, "volume": b.volume}
            )
        total = 0
        for sym_str, rows in rows_by_sym.items():
            df = pd.DataFrame(rows).set_index("ts").sort_index()
            df.index = pd.to_datetime(df.index, utc=True)
            self[(sym_str, timeframe)] = df
            total += len(df)
        return total

    def read_bars(
        self,
        symbol: Symbol,
        timeframe: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Bar]:
        """Read cached bars for a symbol+timeframe back as Bar dataclasses.

        Returns an empty list if nothing is cached. Honors optional start/end
        slicing in tz-aware UTC.
        """
        key = (str(symbol), timeframe)
        try:
            df = self[key]
        except KeyError:
            return []
        if df.empty:
            return []
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        if start is not None:
            df = df[df.index >= start]
        if end is not None:
            df = df[df.index <= end]
        out: list[Bar] = []
        for ts, row in df.iterrows():
            out.append(Bar(
                symbol=symbol, ts=ts.to_pydatetime(),
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                volume=float(row["volume"]),
            ))
        return out


# ---------------------------------------------------------------------------
# Mall — dict of stores
# ---------------------------------------------------------------------------

def mall(root: str | Path = ".hedger") -> dict[str, MutableMapping]:
    """Return the default hedger mall: {name -> store}.

    >>> import tempfile
    >>> m = mall(tempfile.mkdtemp())
    >>> sorted(m.keys())
    ['bars', 'decisions', 'drifts', 'fills', 'news', 'orders', 'positions', 'reflections', 'signals']
    """
    root = Path(root)
    return {
        "bars":        BarStore(root / "bars"),
        "signals":     JsonlStore(root / "signals.jsonl"),
        "decisions":   JsonlStore(root / "decisions.jsonl"),
        "orders":      JsonlStore(root / "orders.jsonl"),
        "fills":       JsonlStore(root / "fills.jsonl"),
        "news":        JsonlStore(root / "news.jsonl"),
        "positions":   JsonlStore(root / "positions.jsonl"),
        "drifts":      JsonlStore(root / "drifts.jsonl"),
        "reflections": JsonlStore(root / "reflections.jsonl"),
    }


# ---------------------------------------------------------------------------
# Position serialisation (snapshots; reconciliation lives in hedger.live.runner)
# ---------------------------------------------------------------------------

def positions_to_snapshot(
    positions: "Mapping[Symbol, Position]",
    *,
    nav: float | None = None,
    ts: str | None = None,
) -> dict:
    """Render a mapping of positions to a JSONable snapshot dict.

    >>> from hedger.base import AssetClass, Position, Symbol
    >>> snap = positions_to_snapshot(
    ...     {Symbol('AAPL', AssetClass.EQUITY): Position(
    ...         symbol=Symbol('AAPL', AssetClass.EQUITY), qty=10, avg_price=100)},
    ...     nav=1000, ts='2026-01-01T00:00:00+00:00')
    >>> snap['nav']
    1000
    >>> snap['positions'][0]['symbol']
    'default:AAPL'
    """
    return {
        "ts": ts,
        "nav": nav,
        "positions": [
            {
                "symbol": str(p.symbol),
                "asset_class": p.symbol.asset_class.value,
                "qty": p.qty,
                "avg_price": p.avg_price,
                "realized_pnl": p.realized_pnl,
            }
            for p in positions.values()
        ],
    }


# ---------------------------------------------------------------------------
# Round-trip helpers (dataclass <-> dict). Kept here, near the stores.
# ---------------------------------------------------------------------------

def signal_to_dict(s: Signal) -> dict:
    return {
        "symbol": str(s.symbol),
        "asset_class": s.symbol.asset_class.value,
        "venue": s.symbol.venue,
        "ts": s.ts.isoformat(),
        "score": s.score,
        "strategy": s.strategy,
        "meta": dict(s.meta),
    }


def decision_to_dict(d: Decision) -> dict:
    return {
        "symbol": str(d.symbol),
        "asset_class": d.symbol.asset_class.value,
        "venue": d.symbol.venue,
        "ts": d.ts.isoformat(),
        "target_weight": d.target_weight,
        "rationale": d.rationale,
        "risk_budget": d.risk_budget,
        "meta": dict(d.meta),
    }


def order_to_dict(o: Order) -> dict:
    return {
        "symbol": str(o.symbol),
        "side": o.side.value,
        "qty": o.qty,
        "order_type": o.order_type.value,
        "limit_price": o.limit_price,
        "stop_price": o.stop_price,
        "time_in_force": o.time_in_force.value,
        "client_order_id": o.client_order_id,
        "meta": dict(o.meta),
    }


def fill_to_dict(f: Fill) -> dict:
    return {
        "order_id": f.order_id,
        "symbol": str(f.symbol),
        "side": f.side.value,
        "qty": f.qty,
        "price": f.price,
        "fee": f.fee,
        "ts": f.ts.isoformat(),
        "venue": f.venue,
        "meta": dict(f.meta),
    }


__all__ = [
    "JsonlStore",
    "BarStore",
    "mall",
    "signal_to_dict",
    "decision_to_dict",
    "order_to_dict",
    "fill_to_dict",
    "positions_to_snapshot",
]
