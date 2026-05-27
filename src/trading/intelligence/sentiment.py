"""Global cues + India VIX via yfinance.

Lightweight: pulls last 5 daily bars per ticker, returns last close + 1d change.
Used as the macro snapshot fed to the pre-market briefing and Opus decide.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import yfinance as yf

CUES = {
    "dow":        "^DJI",
    "nasdaq":     "^IXIC",
    "nifty50":    "^NSEI",
    "india_vix":  "^INDIAVIX",
    "crude_wti":  "CL=F",
    "gold":       "GC=F",
    "usd_inr":    "USDINR=X",
}


@dataclass
class CueReading:
    name: str
    ticker: str
    close: float | None
    pct_1d: float | None


def fetch_cues(as_of: date | None = None) -> list[CueReading]:
    """Pull most recent close + 1d % for each macro cue. Best-effort."""
    end = (as_of or date.today()) + timedelta(days=1)
    start = end - timedelta(days=10)
    out: list[CueReading] = []
    for name, tkr in CUES.items():
        try:
            df = yf.download(tkr, start=start.isoformat(), end=end.isoformat(),
                             progress=False, auto_adjust=False)
            if df.empty or len(df) < 2:
                out.append(CueReading(name, tkr, None, None))
                continue
            close = df["Close"]
            if hasattr(close, "columns"):       # rare: MultiIndex
                close = close.iloc[:, 0]
            last = float(close.iloc[-1])
            prev = float(close.iloc[-2])
            pct = (last - prev) / prev * 100 if prev else 0.0
            out.append(CueReading(name, tkr, round(last, 2), round(pct, 2)))
        except Exception as e:                  # noqa: BLE001
            print(f"[cues] {name}/{tkr} failed: {e}")
            out.append(CueReading(name, tkr, None, None))
    return out


def cues_to_dict(cues: list[CueReading]) -> dict:
    return {c.name: {"close": c.close, "pct_1d": c.pct_1d} for c in cues}
