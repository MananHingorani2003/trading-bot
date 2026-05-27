"""Universe screener — applies hard filters before any strategy runs.

Filters:
- min price (drop penny stocks)
- min average daily traded value (liquidity)
- min market cap (drop micros)
- blacklist patterns (e.g. '*-BE' T2T segment)
- minimum bar count (enough history for indicators)
"""
from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase

import pandas as pd

from trading.data.fundamentals import Fundamentals
from trading.strategy.indicators import avg_daily_value


@dataclass
class ScreenResult:
    symbol: str
    passed: bool
    reason: str
    last_close: float
    avg_value_cr: float


def screen_one(
    symbol: str,
    bars: pd.DataFrame,
    fund: Fundamentals | None,
    *,
    min_price: float,
    min_avg_daily_value_cr: float,
    min_market_cap_cr: float,
    blacklist: list[str],
    min_bars: int = 60,
) -> ScreenResult:
    if bars is None or len(bars) < min_bars:
        return ScreenResult(symbol, False, f"insufficient history ({0 if bars is None else len(bars)} bars)", 0.0, 0.0)

    last_close = float(bars["Close"].iloc[-1])
    adv = avg_daily_value(bars) / 1e7      # rupees → crore

    for pat in blacklist:
        if fnmatchcase(symbol, pat):
            return ScreenResult(symbol, False, f"blacklisted ({pat})", last_close, adv)

    if last_close < min_price:
        return ScreenResult(symbol, False, f"price {last_close:.1f} < {min_price}", last_close, adv)

    if adv < min_avg_daily_value_cr:
        return ScreenResult(symbol, False, f"liquidity {adv:.2f}cr < {min_avg_daily_value_cr}cr", last_close, adv)

    if fund and fund.market_cap_cr and fund.market_cap_cr < min_market_cap_cr:
        return ScreenResult(symbol, False, f"mcap {fund.market_cap_cr:.0f}cr < {min_market_cap_cr}cr", last_close, adv)

    return ScreenResult(symbol, True, "ok", last_close, adv)


def screen_many(
    bars_by_symbol: dict[str, pd.DataFrame],
    funds_by_symbol: dict[str, Fundamentals],
    *,
    universe_cfg: dict,
) -> list[ScreenResult]:
    out: list[ScreenResult] = []
    for sym, bars in bars_by_symbol.items():
        out.append(screen_one(
            sym, bars, funds_by_symbol.get(sym),
            min_price=universe_cfg["min_price"],
            min_avg_daily_value_cr=universe_cfg["min_avg_daily_value_cr"],
            min_market_cap_cr=universe_cfg["min_market_cap_cr"],
            blacklist=universe_cfg.get("blacklist", []),
        ))
    return out
