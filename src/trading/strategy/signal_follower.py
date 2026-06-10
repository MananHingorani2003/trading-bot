"""Execute analyst-channel signals against the paper broker.

Two operations:
  • execute_buy(buy)    — open a position sized by analyst_signal sleeve cap,
                         using the analyst's stop and target
  • execute_update(upd) — close any open analyst position matching the symbol

The whole point of signal-following is no AI gate — we follow what the SEBI RA
says. Hard risk caps (sleeve allocation, max positions) still apply because
those guard against bad signals, not against the AI overriding.

Telegram fill alerts get a `[sig]` tag so signal trades are visually distinct
from AI trades.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from trading.alerts import telegram as tg
from trading.broker.base import Order
from trading.broker.paper import PaperBroker
from trading.intelligence.signals.parser import BuySignal, UpdateSignal
from trading.knowledge import kb, ledger
from trading.settings import StrategyCfg

SOURCE_TAG = "lemonnhq"          # default source label; pass explicitly to override


@dataclass
class FollowResult:
    action: str                  # 'bought' | 'sold' | 'skipped' | 'failed'
    detail: str
    position_id: int | None = None
    qty: int = 0
    price: float = 0.0


def _sleeve_cfg(cfg: StrategyCfg, sleeve: str = "analyst_signal"):
    return cfg.sleeves.get(sleeve) if cfg.sleeves else None


def execute_buy(
    buy: BuySignal,
    *,
    current_price: float,
    cfg: StrategyCfg,
    broker: PaperBroker,
    source: str = SOURCE_TAG,
) -> FollowResult:
    """Open an analyst_signal position based on `buy`."""
    sl = _sleeve_cfg(cfg)
    if not sl:
        return FollowResult("skipped", "analyst_signal sleeve not configured")

    # Don't double-buy if we already hold this symbol from the same source
    with ledger.connect() as conn:
        existing = conn.execute(
            """SELECT id, qty FROM positions
               WHERE status='open' AND symbol=? AND sleeve='analyst_signal'
               LIMIT 1""",
            (buy.symbol,),
        ).fetchone()
    if existing:
        return FollowResult("skipped", f"already holding {buy.symbol} from analyst signal")

    # Same-sleeve concurrent-position cap
    with ledger.connect() as conn:
        n_open = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE status='open' AND sleeve='analyst_signal'"
        ).fetchone()[0]
    if n_open >= sl.max_positions:
        return FollowResult(
            "skipped",
            f"analyst_signal sleeve full ({n_open}/{sl.max_positions})",
        )

    # Sizing
    budget = cfg.portfolio.capital * sl.max_pct_per_position
    qty = max(0, int(budget // current_price))
    if qty <= 0:
        return FollowResult(
            "skipped",
            f"sized to 0 shares at ₹{current_price:.2f} (budget ₹{budget:.0f})",
        )
    cost = qty * current_price
    if cost > broker.cash():
        return FollowResult("skipped", f"insufficient cash: need ₹{cost:.0f}, have ₹{broker.cash():.0f}")

    ts = datetime.utcnow().isoformat(timespec="seconds")
    try:
        with ledger.connect() as conn:
            did = ledger.insert_decision(
                conn, kind="signal_buy", symbol=buy.symbol, action="BUY",
                strategy_version=cfg.version, ai_tier="rules",
                reasoning_md=(
                    f"[analyst_signal/{source}] BUY {buy.symbol}\n"
                    f"entry range ₹{buy.entry_low}-{buy.entry_high}; "
                    f"target ₹{buy.target}; stop ₹{buy.stop_loss}; "
                    f"valid {buy.valid_days}d. "
                    f"Filled at ~₹{current_price:.2f}."
                ),
            )
            kb.index_decision(conn, did)

        fill = broker.place_order(
            Order(symbol=buy.symbol, side="BUY", qty=qty, decision_id=did, sleeve="analyst_signal"),
            ref_price=current_price,
            ts=ts,
        )
        with ledger.connect() as conn:
            pos = ledger.find_open_position(conn, buy.symbol)
            conn.execute(
                "UPDATE positions SET signal_source=? WHERE id=?",
                (source, pos["id"]),
            )
            thid = conn.execute(
                """INSERT INTO theses
                   (position_id, summary_md, target_price, stop_price, time_horizon, catalysts, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    pos["id"],
                    f"[analyst signal · {source}] {buy.raw_text[:500]}",
                    buy.target, buy.stop_loss, f"{buy.valid_days}d",
                    f"signal:{source}", ts,
                ),
            ).lastrowid
            conn.execute("UPDATE positions SET thesis_id=? WHERE id=?", (thid, pos["id"]))
            kb.index_thesis(conn, int(thid))

        tg.notify_fill(
            f"BUY[sig]", buy.symbol, qty, fill.price, qty * fill.price,
        )
        return FollowResult("bought", "ok", position_id=pos["id"], qty=qty, price=fill.price)
    except Exception as e:                          # noqa: BLE001
        return FollowResult("failed", f"order failed: {e}")


def execute_update(
    upd: UpdateSignal,
    *,
    current_price: float,
    cfg: StrategyCfg,
    broker: PaperBroker,
    source: str = SOURCE_TAG,
) -> FollowResult:
    """Close the matching analyst_signal position based on `upd`."""
    with ledger.connect() as conn:
        pos = conn.execute(
            """SELECT id, qty FROM positions
               WHERE status='open' AND symbol=? AND sleeve='analyst_signal'
               LIMIT 1""",
            (upd.symbol,),
        ).fetchone()
    if not pos:
        return FollowResult(
            "skipped",
            f"no open analyst position in {upd.symbol} — update ignored",
        )

    qty = int(pos["qty"])
    ts = datetime.utcnow().isoformat(timespec="seconds")
    ref_price = upd.exit_price if upd.exit_price > 0 else current_price
    try:
        with ledger.connect() as conn:
            did = ledger.insert_decision(
                conn, kind="signal_update", symbol=upd.symbol,
                action="EXIT", strategy_version=cfg.version, ai_tier="rules",
                reasoning_md=(
                    f"[analyst_signal/{source}] {upd.side.upper()} on {upd.symbol} "
                    f"@ ₹{upd.exit_price}. Following analyst directive."
                ),
            )
            kb.index_decision(conn, did)

        fill = broker.place_order(
            Order(symbol=upd.symbol, side="SELL", qty=qty, decision_id=did, sleeve="analyst_signal"),
            ref_price=ref_price,
            ts=ts,
        )
        tg.notify_fill(
            f"SELL[sig·{upd.side[:5]}]", upd.symbol, qty, fill.price, qty * fill.price,
        )
        return FollowResult("sold", "ok", position_id=pos["id"], qty=qty, price=fill.price)
    except Exception as e:                          # noqa: BLE001
        return FollowResult("failed", f"order failed: {e}")
