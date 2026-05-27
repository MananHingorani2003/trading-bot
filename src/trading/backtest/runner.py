"""Trivial backtest runner. Walks calendar days, applies signal per symbol,
sizes positions against available cash, routes through PaperBroker.

This is Phase-0 plumbing. Not a strategy you should trust.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from trading.broker.base import Order
from trading.broker.paper import PaperBroker
from trading.data.prices import fetch_many
from trading.knowledge import ledger
from trading.settings import load_strategy, load_watchlist
from trading.strategy.sma_crossover import ema_signal


@dataclass
class BacktestResult:
    starting_cash: float
    ending_cash: float
    open_position_value: float
    total_value: float
    n_trades: int


def run_backtest(start: date, end: date) -> BacktestResult:
    cfg = load_strategy()
    symbols = load_watchlist()

    ledger.init_db()
    broker = PaperBroker(starting_cash=cfg.portfolio.capital)

    print(f"[bt] Fetching {len(symbols)} symbols {start}..{end}")
    bars = fetch_many(symbols, start - timedelta(days=120), end)
    if not bars:
        raise SystemExit("No price data; check network / yfinance.")

    all_dates = sorted({d.date() for df in bars.values() for d in df.index})
    trading_days = [d for d in all_dates if start <= d <= end]

    max_per_pos = cfg.portfolio.capital * cfg.portfolio.max_pct_per_position
    n_trades = 0

    for d in trading_days:
        for sym, df in bars.items():
            sub = df.loc[:pd.Timestamp(d)]
            if sub.empty or sub.index[-1].date() != d:
                continue
            sig = ema_signal(sub, fast=cfg.signals["trend"]["fast_ema"],
                                  slow=cfg.signals["trend"]["slow_ema"])
            price = float(sub["Close"].iloc[-1])
            ts = f"{d.isoformat()}T15:25:00"

            with ledger.connect() as conn:
                pos = ledger.find_open_position(conn, sym)
                ledger.insert_decision(
                    conn, kind="scan", symbol=sym, action=sig.action,
                    strategy_version=cfg.version, reasoning_md=sig.reason,
                    ai_tier="rules",
                )

            if sig.action == "BUY" and pos is None:
                # Position sizing: cap by max_per_pos AND by available cash
                budget = min(max_per_pos, broker.cash())
                qty = int(budget // price)
                if qty <= 0:
                    continue
                try:
                    broker.place_order(Order(symbol=sym, side="BUY", qty=qty), ref_price=price, ts=ts)
                    n_trades += 1
                except ValueError as e:
                    print(f"[bt] {d} {sym} BUY skipped: {e}")

            elif sig.action == "SELL" and pos is not None:
                try:
                    broker.place_order(Order(symbol=sym, side="SELL", qty=pos["qty"]), ref_price=price, ts=ts)
                    n_trades += 1
                except ValueError as e:
                    print(f"[bt] {d} {sym} SELL skipped: {e}")

    # End-of-run valuation: open positions priced at last close
    open_val = 0.0
    for p in broker.positions():
        df = bars.get(p.symbol)
        if df is not None and not df.empty:
            open_val += p.qty * float(df["Close"].iloc[-1])

    return BacktestResult(
        starting_cash=cfg.portfolio.capital,
        ending_cash=broker.cash(),
        open_position_value=round(open_val, 2),
        total_value=round(broker.cash() + open_val, 2),
        n_trades=n_trades,
    )
