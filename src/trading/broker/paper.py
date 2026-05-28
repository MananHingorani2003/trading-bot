"""Paper broker: simulates fills at ref_price ± fixed slippage, applies real Zerodha costs,
writes to ledger, maintains FIFO tax lots, tracks cash."""
from __future__ import annotations

import uuid
from datetime import datetime

from trading.broker.base import Broker, Fill, Order, Position
from trading.costs.zerodha import net_cash_flow
from trading.knowledge import ledger
from trading.tax.fifo import realise_sell_fifo, record_buy_lot

DEFAULT_SLIPPAGE_BPS = 5    # 0.05% per side — conservative for midcap delivery


class PaperBroker(Broker):
    def __init__(self, starting_cash: float, slippage_bps: float = DEFAULT_SLIPPAGE_BPS):
        self._cash = starting_cash
        self.slippage = slippage_bps / 10_000.0

    def _slip(self, side: str, price: float) -> float:
        return price * (1 + self.slippage) if side == "BUY" else price * (1 - self.slippage)

    def place_order(self, order: Order, *, ref_price: float, ts: str) -> Fill:
        fill_price = round(self._slip(order.side, ref_price), 2)
        gross, net, cb = net_cash_flow(order.side, order.qty, fill_price)

        if order.side == "BUY" and net > self._cash:
            raise ValueError(
                f"Insufficient paper cash: need {net:.2f}, have {self._cash:.2f}"
            )

        order_id = order.client_order_id or f"PAPER-{uuid.uuid4().hex[:10]}"

        with ledger.connect() as conn:
            # Find/open position before writing trade, so trade can reference position_id
            pos_row = ledger.find_open_position(conn, order.symbol)
            position_id = pos_row["id"] if pos_row else None

            if order.side == "BUY":
                if position_id is None:
                    position_id = ledger.open_position(
                        conn,
                        symbol=order.symbol,
                        qty=order.qty,
                        avg_price=fill_price,
                        opened_at=ts,
                        strategy_ver="paper",
                        sleeve=order.sleeve,
                    )
                else:
                    new_qty = pos_row["qty"] + order.qty
                    new_avg = (
                        pos_row["qty"] * pos_row["avg_price"] + order.qty * fill_price
                    ) / new_qty
                    conn.execute(
                        "UPDATE positions SET qty=?, avg_price=? WHERE id=?",
                        (new_qty, new_avg, position_id),
                    )
            else:  # SELL
                if pos_row is None or pos_row["qty"] < order.qty:
                    raise ValueError(
                        f"Cannot SELL {order.qty} of {order.symbol}: insufficient holdings"
                    )

            trade_row = ledger.TradeRow(
                ts=ts, symbol=order.symbol, side=order.side,
                qty=order.qty, price=fill_price, gross_value=gross,
                brokerage=cb.brokerage, stt=cb.stt, exchange_txn=cb.exchange_txn,
                sebi_charges=cb.sebi_charges, stamp_duty=cb.stamp_duty,
                gst=cb.gst, dp_charges=cb.dp_charges,
                total_costs=cb.total, net_value=net,
                order_id=order_id, decision_id=order.decision_id,
                position_id=position_id,
            )
            trade_id = ledger.insert_trade(conn, trade_row)

            if order.side == "BUY":
                record_buy_lot(
                    conn, symbol=order.symbol, buy_trade_id=trade_id,
                    buy_date=ts[:10], qty=order.qty, price=fill_price,
                    buy_costs_total=cb.total,
                )
                self._cash -= net
            else:
                realise_sell_fifo(
                    conn, symbol=order.symbol, sell_trade_id=trade_id,
                    sell_date=ts[:10], qty=order.qty, price=fill_price,
                    sell_costs_total=cb.total,
                )
                remaining = pos_row["qty"] - order.qty
                if remaining == 0:
                    ledger.close_position(conn, position_id, ts)
                else:
                    conn.execute(
                        "UPDATE positions SET qty=? WHERE id=?",
                        (remaining, position_id),
                    )
                self._cash += net

        return Fill(
            order_id=order_id, symbol=order.symbol, side=order.side,
            qty=order.qty, price=fill_price, ts=ts, trade_id=trade_id,
        )

    def positions(self) -> list[Position]:
        with ledger.connect() as conn:
            return [
                Position(symbol=r["symbol"], qty=r["qty"], avg_price=r["avg_price"])
                for r in ledger.open_positions(conn)
            ]

    def cash(self) -> float:
        return round(self._cash, 2)


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")
