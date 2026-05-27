"""Knowledge base — structured (SQL) + keyword (FTS5) retrieval.

The structured layer is primary: "what was the buy thesis for THIS position"
is a direct join over positions → theses. The FTS5 layer is for "find similar
prior setups" kind of fuzzy recall. A future Phase 2 can layer ChromaDB on top
of this without changing callers.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass


def index_decision(conn: sqlite3.Connection, decision_id: int) -> None:
    row = conn.execute(
        "SELECT id, ts, symbol, reasoning_md FROM decisions WHERE id=?", (decision_id,),
    ).fetchone()
    if not row:
        return
    conn.execute(
        "INSERT INTO kb_fts (kind, symbol, ref_id, ts, body) VALUES (?,?,?,?,?)",
        ("decision", row["symbol"] or "", row["id"], row["ts"], row["reasoning_md"]),
    )


def index_thesis(conn: sqlite3.Connection, thesis_id: int) -> None:
    row = conn.execute(
        """SELECT t.id, t.summary_md, t.created_at, p.symbol
           FROM theses t LEFT JOIN positions p ON p.id = t.position_id
           WHERE t.id=?""",
        (thesis_id,),
    ).fetchone()
    if not row:
        return
    conn.execute(
        "INSERT INTO kb_fts (kind, symbol, ref_id, ts, body) VALUES (?,?,?,?,?)",
        ("thesis", row["symbol"] or "", row["id"], row["created_at"], row["summary_md"]),
    )


def index_journal(conn: sqlite3.Connection, date: str, body: str) -> None:
    conn.execute(
        "INSERT INTO kb_fts (kind, symbol, ref_id, ts, body) VALUES (?,?,?,?,?)",
        ("journal", "", 0, date, body),
    )


@dataclass
class KBHit:
    kind: str
    symbol: str
    ts: str
    body: str
    ref_id: int


def search(conn: sqlite3.Connection, query: str, *, limit: int = 20) -> list[KBHit]:
    rows = conn.execute(
        """SELECT kind, symbol, ts, body, ref_id
           FROM kb_fts
           WHERE kb_fts MATCH ?
           ORDER BY rank
           LIMIT ?""",
        (query, limit),
    ).fetchall()
    return [KBHit(**dict(r)) for r in rows]


def history_for_symbol(conn: sqlite3.Connection, symbol: str, *, limit: int = 50) -> list[dict]:
    """Structured recall: everything we've ever decided about this symbol."""
    return [dict(r) for r in conn.execute(
        """SELECT ts, kind, action, ai_tier, reasoning_md
           FROM decisions WHERE symbol=? ORDER BY ts DESC LIMIT ?""",
        (symbol, limit),
    )]


def open_position_with_thesis(conn: sqlite3.Connection, symbol: str) -> dict | None:
    """For sell-side reasoning: original buy thesis + current position state."""
    row = conn.execute(
        """SELECT p.id pos_id, p.symbol, p.qty, p.avg_price, p.opened_at,
                  t.summary_md, t.target_price, t.stop_price, t.time_horizon, t.catalysts
           FROM positions p LEFT JOIN theses t ON t.id = p.thesis_id
           WHERE p.status='open' AND p.symbol=? LIMIT 1""",
        (symbol,),
    ).fetchone()
    return dict(row) if row else None
