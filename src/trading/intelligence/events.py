"""Earnings calendar & blackout windows.

yfinance Ticker.calendar returns upcoming earnings — best-effort, sometimes None.
Cached per symbol for 24h.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import yfinance as yf

from trading.settings import ROOT

CACHE = ROOT / "data" / "cache" / "earnings"
CACHE.mkdir(parents=True, exist_ok=True)
MAX_AGE_SECONDS = 86400


def _path(symbol: str) -> Path:
    return CACHE / f"{symbol}.json"


def _fresh(p: Path) -> bool:
    return p.exists() and (time.time() - p.stat().st_mtime) < MAX_AGE_SECONDS


def next_earnings_date(symbol: str) -> date | None:
    p = _path(symbol)
    if _fresh(p):
        try:
            data = json.loads(p.read_text())
            d = data.get("next_earnings")
            return date.fromisoformat(d) if d else None
        except Exception:                       # noqa: BLE001
            pass

    next_dt: date | None = None
    try:
        cal = yf.Ticker(f"{symbol}.NS").calendar or {}
        if isinstance(cal, dict):
            edate = cal.get("Earnings Date")
            if isinstance(edate, list) and edate:
                edate = edate[0]
            if hasattr(edate, "date"):
                next_dt = edate.date()
            elif isinstance(edate, str):
                next_dt = datetime.fromisoformat(edate[:10]).date()
    except Exception as e:                      # noqa: BLE001
        print(f"[earnings] {symbol} failed: {e}")

    p.write_text(json.dumps({"next_earnings": next_dt.isoformat() if next_dt else None}))
    return next_dt


def in_blackout(symbol: str, as_of: date, *, days: int = 2) -> tuple[bool, str]:
    """Block new entries / hold tighter if earnings are within `days`."""
    nxt = next_earnings_date(symbol)
    if not nxt:
        return False, "no earnings date known"
    delta = (nxt - as_of).days
    if 0 <= delta <= days:
        return True, f"earnings in {delta}d ({nxt.isoformat()})"
    return False, f"earnings in {delta}d"
