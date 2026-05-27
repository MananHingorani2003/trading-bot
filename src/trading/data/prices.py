"""Daily OHLC data via yfinance, cached to data/cache/<symbol>.parquet.

NSE tickers on Yahoo end in `.NS`. We keep the canonical NSE symbol everywhere
and append the suffix only at fetch time.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from trading.settings import ROOT

CACHE_DIR = ROOT / "data" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(symbol: str) -> Path:
    return CACHE_DIR / f"{symbol}.parquet"


def fetch_daily(symbol: str, start: date, end: date, *, force: bool = False) -> pd.DataFrame:
    """Return DataFrame indexed by date with columns: Open, High, Low, Close, Volume."""
    cp = _cache_path(symbol)
    if cp.exists() and not force:
        df = pd.read_parquet(cp)
        if not df.empty and df.index.max().date() >= end - timedelta(days=3):
            return df.loc[str(start):str(end)]

    yf_sym = f"{symbol}.NS"
    df = yf.download(
        yf_sym, start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
        progress=False, auto_adjust=False,
    )
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df[["Open", "High", "Low", "Close", "Volume"]]
    df.to_parquet(cp)
    return df.loc[str(start):str(end)]


def fetch_many(symbols: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for s in symbols:
        try:
            df = fetch_daily(s, start, end)
            if not df.empty:
                out[s] = df
        except Exception as e:                    # noqa: BLE001
            print(f"[data] {s} fetch failed: {e}")
    return out
