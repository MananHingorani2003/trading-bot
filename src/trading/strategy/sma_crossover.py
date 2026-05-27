"""EMA crossover swing strategy. Pure: takes a price DataFrame, returns a signal.

This is intentionally trivial — exists to prove the backtest pipeline. Real
strategies live alongside this once Phase 1 begins.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class Signal:
    action: str        # 'BUY','SELL','HOLD'
    reason: str


def ema_signal(df: pd.DataFrame, *, fast: int = 20, slow: int = 50) -> Signal:
    if len(df) < slow + 2:
        return Signal("HOLD", "insufficient data")

    close = df["Close"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()

    f_prev, f_now = ema_fast.iloc[-2], ema_fast.iloc[-1]
    s_prev, s_now = ema_slow.iloc[-2], ema_slow.iloc[-1]

    if f_prev <= s_prev and f_now > s_now:
        return Signal("BUY", f"EMA{fast} crossed above EMA{slow}")
    if f_prev >= s_prev and f_now < s_now:
        return Signal("SELL", f"EMA{fast} crossed below EMA{slow}")
    return Signal("HOLD", "no crossover")
