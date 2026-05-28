"""Pre-trade risk gates and position sizing. AI cannot override these.

Sleeve-aware: each sleeve (`core`, `aggressive`) has its own cap on positions
and per-position size. A buy is rejected if the requested sleeve is full
even when the other sleeve has room.
"""
from __future__ import annotations

from dataclasses import dataclass

from trading.knowledge import ledger
from trading.settings import SleeveCfg, StrategyCfg


@dataclass
class GateResult:
    allowed: bool
    qty: int
    reason: str
    sleeve: str = "core"


def size_position(*, capital: float, max_pct: float, price: float) -> int:
    budget = capital * max_pct
    return max(0, int(budget // price))


def _sleeve_cfg(cfg: StrategyCfg, sleeve: str) -> SleeveCfg | None:
    return cfg.sleeves.get(sleeve) if cfg.sleeves else None


def gate_buy(
    *, symbol: str, price: float, cfg: StrategyCfg, cash_available: float,
    sleeve: str = "core",
) -> GateResult:
    p = cfg.portfolio
    r = cfg.risk
    sl = _sleeve_cfg(cfg, sleeve)

    # Fall back to global portfolio caps if sleeves not configured (back-compat)
    max_positions = sl.max_positions if sl else p.max_positions
    max_pct_per_position = sl.max_pct_per_position if sl else p.max_pct_per_position

    with ledger.connect() as conn:
        all_open = ledger.open_positions(conn)
        same_sleeve_open = [pos for pos in all_open if (pos["sleeve"] if "sleeve" in pos.keys() else "core") == sleeve]
        existing = ledger.find_open_position(conn, symbol)

    if existing is not None and r.no_average_down:
        return GateResult(False, 0, "already holding — no averaging down", sleeve)

    if len(same_sleeve_open) >= max_positions:
        return GateResult(
            False, 0,
            f"{sleeve} sleeve full ({len(same_sleeve_open)}/{max_positions})",
            sleeve,
        )

    # Global position cap (sum across sleeves)
    if len(all_open) >= p.max_positions:
        return GateResult(False, 0, f"global max positions reached ({p.max_positions})", sleeve)

    qty = size_position(capital=p.capital, max_pct=max_pct_per_position, price=price)
    if qty <= 0:
        return GateResult(False, 0, f"qty zero at price ₹{price:.2f}", sleeve)

    # Cash floor (after global reserve)
    spendable = cash_available - p.capital * p.reserve_cash_pct
    if spendable <= 0:
        return GateResult(False, 0, "cash below reserve floor", sleeve)

    # Sleeve capital cap — committed in this sleeve must stay within sleeve_pct of capital
    if sl:
        committed = sum(pos["qty"] * pos["avg_price"] for pos in same_sleeve_open)
        sleeve_cap_inr = p.capital * sl.capital_pct
        sleeve_room = sleeve_cap_inr - committed
        if sleeve_room <= 0:
            return GateResult(
                False, 0,
                f"{sleeve} sleeve capital cap hit (₹{committed:.0f} / ₹{sleeve_cap_inr:.0f})",
                sleeve,
            )
        spendable = min(spendable, sleeve_room)

    affordable = int(spendable // price)
    qty = min(qty, affordable)
    if qty <= 0:
        return GateResult(False, 0, f"can't afford 1 share within sleeve+reserve room (need ₹{price:.0f})", sleeve)

    return GateResult(True, qty, "ok", sleeve)


def stop_and_target(
    *, entry: float, atr_pct: float, cfg: StrategyCfg, sleeve: str = "core",
) -> tuple[float, float]:
    """ATR-aware stop, R-multiple target. Sleeve config overrides defaults."""
    sl = _sleeve_cfg(cfg, sleeve)
    base_stop_pct = sl.stop_loss_pct if sl else cfg.risk.default_stop_loss_pct
    r_target = sl.target_r_multiple if sl else cfg.risk.default_target_r_multiple
    stop_pct = max(base_stop_pct, (atr_pct / 100) * 1.5)
    stop = round(entry * (1 - stop_pct), 2)
    target = round(entry + r_target * (entry - stop), 2)
    return stop, target
