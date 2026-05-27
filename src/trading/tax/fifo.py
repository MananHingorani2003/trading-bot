"""FIFO tax-lot tracker for Indian listed equity (delivery).

Rates (post Union Budget Jul 2024, applicable FY 2024-25 onwards):
  STCG (held <= 12 months): 20% flat
  LTCG (held >  12 months): 12.5% on gains above ₹1,25,000/yr exemption

Buy-side and sell-side charges are amortised into per-share cost basis so that
realised P&L is post-all-charges.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime


STCG_RATE = 0.20
LTCG_RATE = 0.125
LTCG_EXEMPT_PER_FY = 125_000.0
LTCG_THRESHOLD_DAYS = 366  # >12 months => LTCG


def fy_for(d: date) -> str:
    """Indian financial year string: 'FY2025-26' for a date in Apr2025–Mar2026."""
    if d.month >= 4:
        start, end = d.year, d.year + 1
    else:
        start, end = d.year - 1, d.year
    return f"FY{start}-{str(end)[-2:]}"


@dataclass
class Realisation:
    sell_trade_id: int
    buy_trade_id: int
    symbol: str
    qty: int
    buy_date: str
    sell_date: str
    holding_days: int
    buy_cost_total: float
    sell_proceeds: float
    realised_pnl: float
    gain_type: str        # 'STCG' or 'LTCG'
    fy: str


def record_buy_lot(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    buy_trade_id: int,
    buy_date: str,
    qty: int,
    price: float,
    buy_costs_total: float,
) -> int:
    per_share = buy_costs_total / qty if qty else 0.0
    cur = conn.execute(
        """INSERT INTO tax_lots
           (symbol, buy_trade_id, buy_date, qty_remaining, qty_original, buy_price, buy_costs_per_share)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (symbol, buy_trade_id, buy_date, qty, qty, price, per_share),
    )
    return int(cur.lastrowid)


def realise_sell_fifo(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    sell_trade_id: int,
    sell_date: str,
    qty: int,
    price: float,
    sell_costs_total: float,
) -> list[Realisation]:
    """Consume FIFO lots for `symbol`, write tax_realisations rows, return them.

    Sell-side costs are amortised across the qty being sold.
    """
    remaining = qty
    sell_cost_per_share = sell_costs_total / qty if qty else 0.0
    sell_dt = datetime.strptime(sell_date[:10], "%Y-%m-%d").date()
    fy = fy_for(sell_dt)
    out: list[Realisation] = []

    lots = list(conn.execute(
        """SELECT * FROM tax_lots WHERE symbol=? AND qty_remaining > 0
           ORDER BY buy_date, id""",
        (symbol,),
    ))
    if sum(l["qty_remaining"] for l in lots) < qty:
        raise ValueError(f"Not enough lots to sell {qty} of {symbol}")

    for lot in lots:
        if remaining == 0:
            break
        take = min(lot["qty_remaining"], remaining)
        buy_dt = datetime.strptime(lot["buy_date"][:10], "%Y-%m-%d").date()
        days = (sell_dt - buy_dt).days
        gain_type = "LTCG" if days >= LTCG_THRESHOLD_DAYS else "STCG"

        buy_cost_total = take * (lot["buy_price"] + lot["buy_costs_per_share"])
        sell_proceeds = take * price - take * sell_cost_per_share
        pnl = round(sell_proceeds - buy_cost_total, 2)

        conn.execute(
            """INSERT INTO tax_realisations
               (sell_trade_id, buy_trade_id, symbol, qty, buy_date, sell_date,
                holding_days, buy_cost_total, sell_proceeds, realised_pnl, gain_type, fy)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sell_trade_id, lot["buy_trade_id"], symbol, take,
                lot["buy_date"], sell_date, days,
                round(buy_cost_total, 2), round(sell_proceeds, 2), pnl,
                gain_type, fy,
            ),
        )
        conn.execute(
            "UPDATE tax_lots SET qty_remaining = qty_remaining - ? WHERE id = ?",
            (take, lot["id"]),
        )

        out.append(Realisation(
            sell_trade_id=sell_trade_id,
            buy_trade_id=lot["buy_trade_id"],
            symbol=symbol,
            qty=take,
            buy_date=lot["buy_date"],
            sell_date=sell_date,
            holding_days=days,
            buy_cost_total=round(buy_cost_total, 2),
            sell_proceeds=round(sell_proceeds, 2),
            realised_pnl=pnl,
            gain_type=gain_type,
            fy=fy,
        ))
        remaining -= take

    return out


@dataclass
class TaxSummary:
    fy: str
    stcg_gain: float
    ltcg_gain: float
    ltcg_exempt: float
    ltcg_taxable: float
    stcg_tax: float
    ltcg_tax: float
    total_tax: float
    net_realised_after_tax: float


def fy_summary(conn: sqlite3.Connection, fy: str) -> TaxSummary:
    rows = list(conn.execute(
        "SELECT gain_type, SUM(realised_pnl) AS g FROM tax_realisations WHERE fy=? GROUP BY gain_type",
        (fy,),
    ))
    stcg = next((r["g"] for r in rows if r["gain_type"] == "STCG"), 0.0) or 0.0
    ltcg = next((r["g"] for r in rows if r["gain_type"] == "LTCG"), 0.0) or 0.0

    ltcg_exempt = min(max(ltcg, 0.0), LTCG_EXEMPT_PER_FY)
    ltcg_taxable = max(ltcg - LTCG_EXEMPT_PER_FY, 0.0)

    stcg_tax = max(stcg, 0.0) * STCG_RATE
    ltcg_tax = ltcg_taxable * LTCG_RATE
    total_tax = stcg_tax + ltcg_tax
    net = stcg + ltcg - total_tax

    return TaxSummary(
        fy=fy,
        stcg_gain=round(stcg, 2),
        ltcg_gain=round(ltcg, 2),
        ltcg_exempt=round(ltcg_exempt, 2),
        ltcg_taxable=round(ltcg_taxable, 2),
        stcg_tax=round(stcg_tax, 2),
        ltcg_tax=round(ltcg_tax, 2),
        total_tax=round(total_tax, 2),
        net_realised_after_tax=round(net, 2),
    )
