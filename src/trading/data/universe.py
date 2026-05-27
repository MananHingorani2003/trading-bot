"""Nifty 500 universe loader. Cached on disk; refreshed weekly."""
from __future__ import annotations

import csv
import time
from io import StringIO
from pathlib import Path

import httpx

from trading.settings import ROOT

NSE_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
CACHE = ROOT / "data" / "cache" / "nifty500.csv"
MAX_AGE_DAYS = 7

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/csv,*/*",
}


def _fresh(path: Path, max_age_days: int) -> bool:
    if not path.exists():
        return False
    age_s = time.time() - path.stat().st_mtime
    return age_s < max_age_days * 86400


def fetch_nifty500(*, force: bool = False) -> Path:
    """Download Nifty 500 CSV to cache. Returns the local path."""
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    if _fresh(CACHE, MAX_AGE_DAYS) and not force:
        return CACHE
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as c:
        r = c.get(NSE_URL)
        r.raise_for_status()
        CACHE.write_bytes(r.content)
    return CACHE


def load_nifty500() -> list[dict]:
    """Returns list of {symbol, company, industry, isin} for all Nifty 500 names."""
    path = fetch_nifty500()
    text = path.read_text(encoding="utf-8-sig")
    out: list[dict] = []
    rdr = csv.DictReader(StringIO(text))
    for row in rdr:
        # Column names per NSE: Company Name, Industry, Symbol, Series, ISIN Code
        sym = (row.get("Symbol") or "").strip()
        if not sym:
            continue
        out.append({
            "symbol": sym,
            "company": (row.get("Company Name") or "").strip(),
            "industry": (row.get("Industry") or "").strip(),
            "isin": (row.get("ISIN Code") or "").strip(),
        })
    return out


def load_symbols() -> list[str]:
    return [r["symbol"] for r in load_nifty500()]
