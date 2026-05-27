"""Pre-trade risk gates and position sizing. AI cannot override these."""
from __future__ import annotations

from dataclasses import dataclass

from trading.knowledge import ledger
from trading.settings import StrategyCfg


@dataclass
class GateResult:
    allowed: bool
    qty: int                 # 0 if not allowed
    reason: str


def size_position(*, capital: float, max_pct: float, price: float) -> int:
    """Whole-share sizing capped at max_pct of capital."""
    budget = capital * max_pct
    return max(0, int(budget // price))


def gate_buy(
    *, symbol: str, price: float, cfg: StrategyCfg, cash_available: float,
) -> GateResult:
    p = cfg.portfolio
    r = cfg.risk

    with ledger.connect() as conn:
        open_pos = ledger.open_positions(conn)
        existing = ledger.find_open_position(conn, symbol)

    if existing is not None:
        if r.no_average_down:
            return GateResult(False, 0, "already holding — no averaging down")

    if len(open_pos) >= p.max_positions:
        return GateResult(False, 0, f"max positions reached ({p.max_positions})")

    qty = size_position(
        capital=p.capital, max_pct=p.max_pct_per_position, price=price,
    )
    if qty <= 0:
        return GateResult(False, 0, f"qty zero at price ₹{price:.2f}")

    # cap by available cash (after reserve)
    spendable = cash_available - p.capital * p.reserve_cash_pct
    if spendable <= 0:
        return GateResult(False, 0, "cash below reserve floor")

    affordable = int(spendable // price)
    qty = min(qty, affordable)
    if qty <= 0:
        return GateResult(False, 0, f"can't afford 1 share within reserve (need ₹{price:.0f})")

    return GateResult(True, qty, "ok")


def stop_and_target(*, entry: float, atr_pct: float, cfg: StrategyCfg) -> tuple[float, float]:
    """Stop sized off ATR (min cfg default); target = R-multiple of risk."""
    stop_pct = max(cfg.risk.default_stop_loss_pct, (atr_pct / 100) * 1.5)
    stop = round(entry * (1 - stop_pct), 2)
    target = round(entry + cfg.risk.default_target_r_multiple * (entry - stop), 2)
    return stop, target
