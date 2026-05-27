"""Trend-following signal: EMA(fast) > EMA(slow) and ADX > threshold.

Emits Candidate objects with a numeric `score` so the AI layer can rank.
The strategy is intentionally simple; the edge (if any) comes from the
post-screen quality filter + AI vetting, not exotic indicator combos.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trading.strategy.indicators import adx, atr, ema, pct_from_52w_high, rsi, volume_surge


@dataclass
class Candidate:
    symbol: str
    last_close: float
    fast_ema: float
    slow_ema: float
    adx: float
    rsi: float
    atr_pct: float           # ATR as % of price — for stop sizing
    pct_from_high: float     # 0 = at 52w high
    vol_surge: float         # 1.0 = average; >1.3 = confirmation
    score: float             # higher = stronger setup
    reason: str


def trend_candidates(
    bars_by_symbol: dict[str, pd.DataFrame],
    *,
    fast: int = 20,
    slow: int = 50,
    adx_min: float = 20.0,
) -> list[Candidate]:
    """Return BUY candidates where price is in a confirmed up-trend."""
    out: list[Candidate] = []
    for sym, df in bars_by_symbol.items():
        if len(df) < slow + 20:
            continue
        close = df["Close"]
        f = ema(close, fast)
        s = ema(close, slow)
        a = adx(df, 14)
        r = rsi(close, 14)
        atr_ = atr(df, 14)

        last_close = float(close.iloc[-1])
        f_now, s_now = float(f.iloc[-1]), float(s.iloc[-1])
        a_now = float(a.iloc[-1])
        r_now = float(r.iloc[-1])
        atr_pct = float(atr_.iloc[-1]) / last_close * 100 if last_close else 0.0
        pct_hi = pct_from_52w_high(df)
        vsurge = volume_surge(df)

        # Filters: up-trend, momentum, not extended, volume not dead
        if f_now <= s_now:
            continue
        if a_now < adx_min:
            continue
        if r_now > 75:
            continue
        if vsurge < 0.7:                  # unusually thin day — likely noise
            continue

        # Score:
        #   trend separation + ADX strength bonus
        # - overextension penalty
        # + proximity-to-52w-high bonus (closer is better)
        # + volume surge bonus (clipped so a single huge day doesn't dominate)
        sep = (f_now - s_now) / s_now * 100
        prox_bonus = max(0.0, 8.0 - pct_hi) * 0.4       # within 8% of 52w high
        vol_bonus = (min(vsurge, 2.5) - 1.0) * 1.5      # above-avg volume = signal
        score = (
            sep
            + (a_now - adx_min) * 0.5
            - max(0.0, r_now - 70) * 0.3
            + prox_bonus
            + vol_bonus
        )

        reason = (
            f"EMA{fast}>{slow} (sep {sep:+.2f}%), ADX {a_now:.1f}, "
            f"RSI {r_now:.1f}, ATR {atr_pct:.2f}%, "
            f"{pct_hi:.1f}% from 52wH, vol×{vsurge:.2f}"
        )
        out.append(Candidate(
            symbol=sym, last_close=last_close, fast_ema=f_now, slow_ema=s_now,
            adx=a_now, rsi=r_now, atr_pct=atr_pct,
            pct_from_high=pct_hi, vol_surge=vsurge,
            score=round(score, 3), reason=reason,
        ))

    out.sort(key=lambda c: c.score, reverse=True)
    return out


def exit_signal(df: pd.DataFrame, *, fast: int = 20, slow: int = 50) -> tuple[bool, str]:
    """Trend exit: fast EMA crosses below slow, or RSI breakdown."""
    if len(df) < slow + 2:
        return False, "insufficient data"
    close = df["Close"]
    f = ema(close, fast)
    s = ema(close, slow)
    if f.iloc[-1] < s.iloc[-1] and f.iloc[-2] >= s.iloc[-2]:
        return True, f"EMA{fast} crossed below EMA{slow}"
    r = rsi(close, 14)
    if r.iloc[-1] < 35 and r.iloc[-2] >= 35:
        return True, f"RSI broke down to {r.iloc[-1]:.1f}"
    return False, "trend intact"
