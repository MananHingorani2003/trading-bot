"""Thin SQLite access layer. Source of truth for positions, trades, decisions, tax lots."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from trading.settings import settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def init_db(path: Path | None = None) -> Path:
    p = path or settings.ledger_path
    with sqlite3.connect(p) as conn:
        conn.executescript(SCHEMA_PATH.read_text())
    return p


@contextmanager
def connect(path: Path | None = None):
    p = path or settings.ledger_path
    if not p.exists():
        init_db(p)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@dataclass
class TradeRow:
    ts: str
    symbol: str
    side: str         # 'BUY' or 'SELL'
    qty: int
    price: float
    gross_value: float
    brokerage: float
    stt: float
    exchange_txn: float
    sebi_charges: float
    stamp_duty: float
    gst: float
    dp_charges: float
    total_costs: float
    net_value: float  # BUY: cash out = gross + costs; SELL: cash in = gross - costs
    order_id: str | None = None
    decision_id: int | None = None
    position_id: int | None = None


def insert_trade(conn: sqlite3.Connection, t: TradeRow) -> int:
    cur = conn.execute(
        """INSERT INTO trades (
            ts, symbol, side, qty, price, gross_value,
            brokerage, stt, exchange_txn, sebi_charges, stamp_duty, gst, dp_charges,
            total_costs, net_value, order_id, decision_id, position_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            t.ts, t.symbol, t.side, t.qty, t.price, t.gross_value,
            t.brokerage, t.stt, t.exchange_txn, t.sebi_charges, t.stamp_duty, t.gst, t.dp_charges,
            t.total_costs, t.net_value, t.order_id, t.decision_id, t.position_id,
        ),
    )
    return int(cur.lastrowid)


def insert_decision(
    conn: sqlite3.Connection,
    *,
    kind: str,
    reasoning_md: str,
    strategy_version: str,
    symbol: str | None = None,
    action: str | None = None,
    market_snapshot: dict | None = None,
    ai_tier: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    git_sha: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO decisions (
            ts, kind, symbol, action, strategy_version, git_sha, reasoning_md,
            market_snapshot, ai_tier, tokens_in, tokens_out, cost_usd
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            datetime.utcnow().isoformat(timespec="seconds"),
            kind, symbol, action, strategy_version, git_sha, reasoning_md,
            json.dumps(market_snapshot) if market_snapshot else None,
            ai_tier, tokens_in, tokens_out, cost_usd,
        ),
    )
    return int(cur.lastrowid)


def open_position(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    qty: int,
    avg_price: float,
    opened_at: str,
    strategy_ver: str,
    thesis_id: int | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO positions (symbol, qty, avg_price, opened_at, status, thesis_id, strategy_ver)
           VALUES (?,?,?,?, 'open', ?, ?)""",
        (symbol, qty, avg_price, opened_at, thesis_id, strategy_ver),
    )
    return int(cur.lastrowid)


def close_position(conn: sqlite3.Connection, position_id: int, closed_at: str) -> None:
    conn.execute(
        "UPDATE positions SET status='closed', closed_at=? WHERE id=?",
        (closed_at, position_id),
    )


def open_positions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM positions WHERE status='open' ORDER BY opened_at"))


def find_open_position(conn: sqlite3.Connection, symbol: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM positions WHERE status='open' AND symbol=? LIMIT 1", (symbol,)
    ).fetchone()
