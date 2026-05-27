"""Technical indicators. Pure functions over pandas DataFrames with OHLC columns."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50.0)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR. Expects High, Low, Close columns."""
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ADX. Returns the ADX line (0..100)."""
    high, low = df["High"], df["Low"]
    up = high.diff()
    down = -low.diff()
    plus_dm = ((up > down) & (up > 0)).astype(float) * up.clip(lower=0)
    minus_dm = ((down > up) & (down > 0)).astype(float) * down.clip(lower=0)
    atr_ = atr(df, period)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0.0)


def bollinger(close: pd.Series, period: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (middle, upper, lower)."""
    mid = close.rolling(period).mean()
    sd = close.rolling(period).std()
    return mid, mid + k * sd, mid - k * sd


def avg_daily_value(df: pd.DataFrame, period: int = 20) -> float:
    """Rupees/day traded, averaged over the last `period` bars."""
    if len(df) < period:
        return 0.0
    sub = df.tail(period)
    return float((sub["Close"] * sub["Volume"]).mean())


def pct_from_52w_high(df: pd.DataFrame) -> float:
    """How far below the 52-week high we are, as a positive percent.
    0 = at the high. 5 = 5% below. Lower is better for trend continuation."""
    look = df.tail(252)
    if look.empty:
        return 100.0
    hi = float(look["High"].max())
    last = float(df["Close"].iloc[-1])
    return round((hi - last) / hi * 100, 2)


def volume_surge(df: pd.DataFrame, period: int = 20) -> float:
    """Today's volume divided by the trailing `period`-day average.
    >1.3 means meaningful volume confirmation; ~1.0 is unremarkable."""
    if len(df) < period + 1:
        return 1.0
    avg = float(df["Volume"].tail(period).mean())
    if avg <= 0:
        return 1.0
    return round(float(df["Volume"].iloc[-1]) / avg, 2)
