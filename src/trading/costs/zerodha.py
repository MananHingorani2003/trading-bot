"""Zerodha cost calculator for cash equity DELIVERY (CNC) orders.

Rates as of 2026; rates change — verify against zerodha.com/charges before going live.

Rates used:
- Brokerage:        ₹0 on delivery
- STT:              0.1% on BUY + 0.1% on SELL (turnover)
- Exchange txn:     NSE 0.00297% on both sides (BSE differs slightly; we assume NSE)
- SEBI charges:     ₹10 per crore = 0.0001% on both sides
- Stamp duty:       0.015% on BUY side only
- GST:              18% on (brokerage + exchange txn + SEBI)
- DP charges:       ₹15.34 per scrip per day on SELL (CDSL), regardless of qty
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostBreakdown:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi_charges: float
    stamp_duty: float
    gst: float
    dp_charges: float

    @property
    def total(self) -> float:
        return (
            self.brokerage + self.stt + self.exchange_txn + self.sebi_charges
            + self.stamp_duty + self.gst + self.dp_charges
        )


def _round_paise(x: float) -> float:
    return round(x, 2)


def compute_costs(side: str, qty: int, price: float) -> CostBreakdown:
    """Return per-trade costs for a delivery cash equity fill on Zerodha (NSE).

    side: 'BUY' or 'SELL'
    """
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    if qty <= 0 or price <= 0:
        raise ValueError("qty and price must be positive")

    turnover = qty * price

    brokerage = 0.0
    stt = 0.001 * turnover
    exchange_txn = 0.0000297 * turnover
    sebi_charges = 0.000001 * turnover
    stamp_duty = 0.00015 * turnover if side == "BUY" else 0.0
    gst = 0.18 * (brokerage + exchange_txn + sebi_charges)
    dp_charges = 15.34 if side == "SELL" else 0.0

    return CostBreakdown(
        brokerage=_round_paise(brokerage),
        stt=_round_paise(stt),
        exchange_txn=_round_paise(exchange_txn),
        sebi_charges=_round_paise(sebi_charges),
        stamp_duty=_round_paise(stamp_duty),
        gst=_round_paise(gst),
        dp_charges=_round_paise(dp_charges),
    )


def net_cash_flow(side: str, qty: int, price: float) -> tuple[float, float, CostBreakdown]:
    """Returns (gross_value, net_cash, breakdown).

    For BUY: net_cash is what leaves your account = gross + costs.
    For SELL: net_cash is what hits your account = gross - costs.
    """
    cb = compute_costs(side, qty, price)
    gross = qty * price
    net = gross + cb.total if side.upper() == "BUY" else gross - cb.total
    return _round_paise(gross), _round_paise(net), cb
