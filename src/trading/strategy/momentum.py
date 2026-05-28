"""Aggressive sleeve: news-catalyst breakout strategy.

Filters (all must hold):
  • Close > prior 20-day high (breakout)
  • Today's volume / 20d avg ≥ 1.8 (institutional participation)
  • RSI < 80 (room to run)
  • At least one news headline mentioning the symbol in last 48h
  • Avg daily value ≥ ₹2cr (tradeable at retail size)
  • Market cap ≥ ₹200cr (smaller than core, still not micro)

Scored by breakout strength + volume surge + news freshness.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from trading.data.fundamentals import Fundamentals
from trading.intelligence import news as news_mod
from trading.settings import SleeveCfg
from trading.strategy.indicators import atr, avg_daily_value, rsi


@dataclass
class MomentumCandidate:
    symbol: str
    last_close: float
    breakout_pct: float       # how much we cleared prior N-day high (positive %)
    vol_surge: float          # today / 20d avg
    rsi: float
    atr_pct: float
    n_news_24h: int
    n_news_48h: int
    latest_news_title: str
    score: float
    reason: str


def momentum_candidates(
    bars_by_symbol: dict[str, pd.DataFrame],
    funds_by_symbol: dict[str, Fundamentals],
    company_by_symbol: dict[str, str],
    *, cfg: SleeveCfg,
) -> list[MomentumCandidate]:
    if not cfg.breakout_lookback_days:
        return []

    out: list[MomentumCandidate] = []
    cutoff_24h = (datetime.now().astimezone() - timedelta(hours=24)).isoformat()

    for sym, df in bars_by_symbol.items():
        lb = cfg.breakout_lookback_days
        if len(df) < lb + 22:
            continue

        # Liquidity (aggressive sleeve floor is lower than core)
        adv_cr = avg_daily_value(df) / 1e7
        if cfg.min_avg_daily_value_cr and adv_cr < cfg.min_avg_daily_value_cr:
            continue

        # Market cap (smaller-cap allowed)
        fund = funds_by_symbol.get(sym)
        if fund and fund.market_cap_cr and cfg.min_market_cap_cr and fund.market_cap_cr < cfg.min_market_cap_cr:
            continue

        close = df["Close"]
        last = float(close.iloc[-1])

        # Breakout: today's close > prior `lb`-day max (excluding today)
        prior_max = float(close.iloc[-(lb + 1):-1].max())
        if last <= prior_max:
            continue
        brk_pct = (last - prior_max) / prior_max * 100

        # Volume surge — today vs 20d avg (excluding today)
        vol_avg = float(df["Volume"].iloc[-21:-1].mean())
        if vol_avg <= 0:
            continue
        vsurge = float(df["Volume"].iloc[-1]) / vol_avg
        if cfg.min_volume_surge and vsurge < cfg.min_volume_surge:
            continue

        # RSI not extended
        r_now = float(rsi(close).iloc[-1])
        if cfg.rsi_max and r_now > cfg.rsi_max:
            continue

        # News in last N hours — REQUIRED
        company = company_by_symbol.get(sym)
        hours = cfg.require_news_hours or 48
        hits = news_mod.for_symbol(sym, company=company, days=max(1, hours // 24 + 1), limit=10)
        if not hits:
            continue
        n_recent = sum(1 for h in hits if h.published_iso and h.published_iso[:10] >= cutoff_24h[:10])
        latest_title = hits[0].title if hits else ""

        atr_pct = float(atr(df).iloc[-1]) / last * 100 if last else 0.0

        score = (
            brk_pct * 1.5                          # breakout magnitude weight
            + min(vsurge - 1.0, 2.0) * 2.0          # volume surge bonus, clipped
            + n_recent * 1.0                        # fresh-news bonus
            - max(0.0, r_now - 70) * 0.4            # don't chase late-stage RSI
        )

        reason = (
            f"breakout +{brk_pct:.2f}% over {lb}d high, vol×{vsurge:.2f}, "
            f"RSI {r_now:.0f}, ATR {atr_pct:.2f}%, "
            f"{len(hits)} news hits ({n_recent} in last 24h)"
        )
        out.append(MomentumCandidate(
            symbol=sym, last_close=last, breakout_pct=round(brk_pct, 2),
            vol_surge=round(vsurge, 2), rsi=round(r_now, 1),
            atr_pct=round(atr_pct, 2),
            n_news_24h=n_recent, n_news_48h=len(hits),
            latest_news_title=latest_title[:120],
            score=round(score, 3), reason=reason,
        ))

    out.sort(key=lambda c: c.score, reverse=True)
    return out
