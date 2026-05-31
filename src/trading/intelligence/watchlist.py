"""Watchlist sweep — re-evaluate recently WATCH'd symbols even if they fall
out of today's trend candidates.

Why: Sonnet often says "wait for RSI < 60" or "wait for pullback to ₹X". If the
stock later drops out of the trend filter (ADX weakens, EMA stack breaks) but
the trigger condition is met, the symbol would otherwise be forgotten. This
module forces those symbols back into the pipeline so the AI re-evaluates them
with full prior context.

Behavior:
- Pull symbols with action='WATCH' in last `max_age_days` days
- Dedupe (keep most recent WATCH per symbol)
- Build Candidate objects with today's technicals
- Hand them to the orchestrator, which appends them to the trend/momentum pool
  before Haiku ranks; Sonnet's history lookup will surface the prior WATCH
  reasoning automatically
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from trading.knowledge import ledger
from trading.strategy.indicators import adx, atr, ema, pct_from_52w_high, rsi, volume_surge
from trading.strategy.trend import Candidate

DEFAULT_MAX_AGE_DAYS = 14
DEFAULT_MAX_N = 10
WATCHLIST_SCORE_BOOST = 100.0   # ensures Haiku surfaces them above noise


@dataclass
class WatchEntry:
    symbol: str
    last_watch_ts: str
    days_ago: int
    last_reason: str           # FULL Sonnet reasoning_md; truncate at display time


def active_watches(*, max_age_days: int = DEFAULT_MAX_AGE_DAYS, limit: int = DEFAULT_MAX_N) -> list[WatchEntry]:
    """Return symbols currently on the active watchlist (most recent WATCH per
    symbol within the window)."""
    cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
    with ledger.connect() as conn:
        rows = conn.execute(
            """
            SELECT symbol, MAX(ts) AS last_ts
            FROM decisions
            WHERE action='WATCH' AND symbol IS NOT NULL AND ts >= ?
            GROUP BY symbol
            ORDER BY last_ts DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
        out: list[WatchEntry] = []
        for r in rows:
            sym = r["symbol"]
            last_ts = r["last_ts"]
            # Grab the FULL Sonnet reasoning from that last WATCH (no SQL truncation)
            reason_row = conn.execute(
                """SELECT reasoning_md
                   FROM decisions
                   WHERE symbol=? AND action='WATCH' AND ts=?
                   LIMIT 1""",
                (sym, last_ts),
            ).fetchone()
            days_ago = (date.today() - date.fromisoformat(last_ts[:10])).days
            out.append(WatchEntry(
                symbol=sym, last_watch_ts=last_ts,
                days_ago=days_ago,
                last_reason=(reason_row["reasoning_md"] if reason_row else ""),
            ))
    return out


def watchlist_candidates(
    bars_by_symbol: dict[str, pd.DataFrame],
    *, max_age_days: int = DEFAULT_MAX_AGE_DAYS, limit: int = DEFAULT_MAX_N,
) -> list[Candidate]:
    """Build Candidate objects for symbols on the active watchlist.

    Bypasses the trend filter — these candidates are admitted by virtue of
    having a recent WATCH recommendation. The Sonnet analyse call will read
    the full prior history and reason whether today's state matches the
    earlier "wait for X" condition.
    """
    watches = active_watches(max_age_days=max_age_days, limit=limit)
    out: list[Candidate] = []
    for w in watches:
        df = bars_by_symbol.get(w.symbol)
        if df is None or df.empty:
            continue
        df = df.dropna(subset=["Close", "High", "Low"])
        if len(df) < 60:
            continue

        close = df["Close"]
        last_close = float(close.iloc[-1])
        f = ema(close, 20)
        s = ema(close, 50)
        a = adx(df, 14)
        r_ind = rsi(close, 14)
        atr_ = atr(df, 14)
        atr_pct = float(atr_.iloc[-1]) / last_close * 100 if last_close else 0.0

        out.append(Candidate(
            symbol=w.symbol,
            last_close=last_close,
            fast_ema=float(f.iloc[-1]),
            slow_ema=float(s.iloc[-1]),
            adx=float(a.iloc[-1]),
            rsi=float(r_ind.iloc[-1]),
            atr_pct=atr_pct,
            pct_from_high=pct_from_52w_high(df),
            vol_surge=volume_surge(df),
            # Score high so Haiku surfaces these; the real decision is Sonnet's
            score=round(WATCHLIST_SCORE_BOOST - w.days_ago, 3),
            reason=(
                f"[watchlist re-check] flagged WATCH {w.days_ago}d ago "
                f"({w.last_watch_ts[:10]}); reason snippet: "
                f"{w.last_reason[:120]}"
            ),
        ))
    return out
