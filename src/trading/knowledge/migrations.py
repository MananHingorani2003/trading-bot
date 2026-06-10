"""Forward-only schema migrations. Each step bumps schema_meta.version."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable

from trading.knowledge.ledger import connect, init_db


def _v(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
    return int(row["value"]) if row else 0


def _set_v(conn: sqlite3.Connection, v: int) -> None:
    conn.execute("UPDATE schema_meta SET value=? WHERE key='version'", (str(v),))


def _m2_fts5(conn: sqlite3.Connection) -> None:
    """v2: FTS5 over decisions + theses + journals + a content_chunks bag."""
    conn.executescript("""
        CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
            kind,              -- 'decision','thesis','journal'
            symbol,
            ref_id UNINDEXED,
            ts,
            body,
            tokenize = 'porter unicode61'
        );
    """)


def _m3_headlines(conn: sqlite3.Connection) -> None:
    """v3: persistent headlines table + FTS5 for per-symbol news lookups."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS headlines (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            source        TEXT NOT NULL,
            title         TEXT NOT NULL,
            summary       TEXT,
            link          TEXT UNIQUE,
            published_iso TEXT,
            fetched_at    REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_headlines_published
          ON headlines(published_iso DESC);

        CREATE VIRTUAL TABLE IF NOT EXISTS headlines_fts USING fts5(
            title, summary,
            content='headlines', content_rowid='id',
            tokenize = 'porter unicode61'
        );

        CREATE TRIGGER IF NOT EXISTS headlines_ai AFTER INSERT ON headlines BEGIN
          INSERT INTO headlines_fts(rowid, title, summary)
            VALUES (new.id, new.title, COALESCE(new.summary,''));
        END;
        CREATE TRIGGER IF NOT EXISTS headlines_ad AFTER DELETE ON headlines BEGIN
          INSERT INTO headlines_fts(headlines_fts, rowid, title, summary)
            VALUES ('delete', old.id, old.title, COALESCE(old.summary,''));
        END;
    """)


def _m4_sleeves(conn: sqlite3.Connection) -> None:
    """v4: positions.sleeve column ('core' | 'aggressive'). Default 'core'
    so pre-existing rows stay in the core book."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(positions)")]
    if "sleeve" not in cols:
        conn.execute(
            "ALTER TABLE positions ADD COLUMN sleeve TEXT NOT NULL DEFAULT 'core'"
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_sleeve ON positions(sleeve)")


def _m5_signal_source(conn: sqlite3.Connection) -> None:
    """v5: external-signal tracking. Adds:
       - positions.signal_source column (NULL=AI-generated, else channel name)
       - signal_messages table: raw inbound channel messages + parsed payload
       - signal_messages_fts: FTS over message text for search
    """
    cols = [r[1] for r in conn.execute("PRAGMA table_info(positions)")]
    if "signal_source" not in cols:
        conn.execute("ALTER TABLE positions ADD COLUMN signal_source TEXT")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS signal_messages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            source        TEXT    NOT NULL,            -- e.g. 'lemonnhq'
            channel_msg_id INTEGER NOT NULL,
            received_at   TEXT    NOT NULL,            -- ISO
            message_text  TEXT    NOT NULL,
            kind          TEXT,                         -- 'buy' | 'update' | 'unknown'
            symbol        TEXT,
            parsed_json   TEXT,                         -- structured payload
            action_taken  TEXT,                         -- 'bought' | 'sold' | 'skipped' | 'parse_failed'
            position_id   INTEGER REFERENCES positions(id),
            UNIQUE(source, channel_msg_id)
        );
        CREATE INDEX IF NOT EXISTS idx_signal_msgs_received ON signal_messages(received_at DESC);
        CREATE INDEX IF NOT EXISTS idx_signal_msgs_symbol ON signal_messages(symbol);
    """)


MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (2, _m2_fts5),
    (3, _m3_headlines),
    (4, _m4_sleeves),
    (5, _m5_signal_source),
]


def migrate() -> int:
    init_db()
    with connect() as conn:
        cur = _v(conn)
        for target, fn in MIGRATIONS:
            if cur < target:
                fn(conn)
                _set_v(conn, target)
                cur = target
        return cur
