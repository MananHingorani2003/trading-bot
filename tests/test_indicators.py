import numpy as np
import pandas as pd

from trading.strategy.indicators import adx, atr, avg_daily_value, bollinger, ema, rsi


def _df(n=100):
    # Synthetic uptrend + noise
    rng = np.random.default_rng(42)
    close = pd.Series(100 + np.cumsum(rng.normal(0.1, 1.0, n)))
    high = close + rng.uniform(0.1, 1.0, n)
    low = close - rng.uniform(0.1, 1.0, n)
    vol = pd.Series(rng.integers(10_000, 100_000, n))
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": vol})


def test_ema_shapes_and_smoothing():
    s = pd.Series(range(100), dtype=float)
    e = ema(s, 10)
    assert len(e) == 100
    assert abs(e.iloc[-1] - s.iloc[-1]) < 5    # close to current value


def test_rsi_bounds():
    df = _df()
    r = rsi(df["Close"])
    assert ((r >= 0) & (r <= 100)).all()


def test_atr_positive():
    df = _df()
    a = atr(df)
    assert (a.dropna() > 0).all()


def test_adx_in_range():
    df = _df()
    a = adx(df)
    assert ((a >= 0) & (a <= 100)).all()


def test_bollinger_envelopes_mid():
    df = _df()
    mid, up, low = bollinger(df["Close"])
    valid = mid.dropna().index
    assert (up.loc[valid] >= mid.loc[valid] - 1e-9).all()
    assert (low.loc[valid] <= mid.loc[valid] + 1e-9).all()


def test_avg_daily_value_smoke():
    df = _df()
    v = avg_daily_value(df)
    assert v > 0
