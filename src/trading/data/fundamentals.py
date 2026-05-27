"""Fundamentals via yfinance .info. Best-effort, heavily cached.

yfinance scrapes Yahoo and frequently returns partial data or 429s. We:
- Cache per-symbol to JSON on disk
- Refresh weekly (fundamentals don't change daily)
- Return a typed Fundamentals object with None fields when missing
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import yfinance as yf

from trading.settings import ROOT

CACHE = ROOT / "data" / "cache" / "fundamentals"
CACHE.mkdir(parents=True, exist_ok=True)
MAX_AGE_DAYS = 7


@dataclass
class Fundamentals:
    symbol: str
    market_cap_cr: float | None      # crore INR
    trailing_pe: float | None
    forward_pe: float | None
    peg_ratio: float | None          # P/E ÷ growth — <1 is interesting, >2 is rich
    roe: float | None                # 0..1
    debt_to_equity: float | None
    earnings_growth: float | None    # 0..1 (yoy)
    pct_from_52w_low: float | None   # how far above the 52w low we are (%)
    sector: str | None
    industry: str | None
    fetched_at: float = 0.0          # epoch


def _path(symbol: str) -> Path:
    return CACHE / f"{symbol}.json"


def _fresh(path: Path) -> bool:
    if not path.exists():
        return False
    return (time.time() - path.stat().st_mtime) < MAX_AGE_DAYS * 86400


_FIELDS = {f for f in Fundamentals.__dataclass_fields__}


def _load_cached(path: Path) -> Fundamentals | None:
    """Read cached JSON; tolerate schema drift by dropping unknown keys and
    filling missing ones with None."""
    try:
        raw = json.loads(path.read_text())
    except Exception:                       # noqa: BLE001
        return None
    payload = {k: raw.get(k) for k in _FIELDS}
    payload["symbol"] = raw.get("symbol") or path.stem
    payload["fetched_at"] = raw.get("fetched_at", 0.0) or 0.0
    try:
        return Fundamentals(**payload)
    except TypeError:
        return None


def fetch_one(symbol: str, *, force: bool = False) -> Fundamentals:
    p = _path(symbol)
    if _fresh(p) and not force:
        cached = _load_cached(p)
        if cached is not None:
            return cached

    info: dict = {}
    try:
        info = yf.Ticker(f"{symbol}.NS").info or {}
    except Exception as e:                 # noqa: BLE001
        print(f"[fund] {symbol} fetch failed: {e}")

    mc = info.get("marketCap")
    low_52w = info.get("fiftyTwoWeekLow")
    last = info.get("currentPrice") or info.get("regularMarketPrice")
    pct_from_low: float | None = None
    if low_52w and last:
        try:
            pct_from_low = round((last - low_52w) / low_52w * 100, 2)
        except Exception:                  # noqa: BLE001
            pct_from_low = None

    f = Fundamentals(
        symbol=symbol,
        market_cap_cr=round(mc / 1e7, 2) if mc else None,
        trailing_pe=info.get("trailingPE"),
        forward_pe=info.get("forwardPE"),
        peg_ratio=info.get("pegRatio") or info.get("trailingPegRatio"),
        roe=info.get("returnOnEquity"),
        debt_to_equity=info.get("debtToEquity"),
        earnings_growth=info.get("earningsGrowth"),
        pct_from_52w_low=pct_from_low,
        sector=info.get("sector"),
        industry=info.get("industry"),
        fetched_at=time.time(),
    )
    p.write_text(json.dumps(asdict(f)))
    return f


def fetch_many(symbols: list[str]) -> dict[str, Fundamentals]:
    return {s: fetch_one(s) for s in symbols}
