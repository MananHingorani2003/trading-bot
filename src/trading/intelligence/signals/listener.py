"""Telegram channel listener via Telethon (MTProto user client).

Polls a public channel for new messages, parses them, and yields
ParsedMessage results. Designed to fail gracefully:

  - Telethon not installed       → log warning, return []
  - Credentials missing in .env  → log warning, return []
  - Session not authed yet       → log warning, return []
  - Channel unreachable          → log error, return [] (next poll retries)
  - Parse error on a message     → save raw text + skip that message

Authentication is one-time interactive (phone + OTP). See cli.py `signal-auth`.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trading.intelligence.signals.parser import ParsedMessage, parse
from trading.knowledge import ledger
from trading.knowledge.migrations import migrate
from trading.settings import settings


@dataclass
class IncomingMessage:
    channel_msg_id: int
    received_at: datetime          # UTC
    text: str
    parsed: ParsedMessage


def _client_available() -> tuple[bool, str]:
    """Why-it-doesn't-work message if not ready, ('', '') if ready."""
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        return False, "TELEGRAM_API_ID / TELEGRAM_API_HASH not set"
    session_file = Path(settings.telegram_session_path)
    if not session_file.exists():
        return False, f"session file not found ({session_file}); run `tt signal-auth`"
    try:
        import telethon                              # noqa: F401
    except ImportError:
        return False, "telethon not installed"
    return True, ""


async def _fetch_async(channel: str, *, since_minutes: int, limit: int) -> list[IncomingMessage]:
    from telethon import TelegramClient
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)

    client = TelegramClient(
        settings.telegram_session_path,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    out: list[IncomingMessage] = []
    async with client:
        try:
            entity = await client.get_entity(channel)
        except Exception as e:                      # noqa: BLE001
            print(f"[signals] cannot resolve channel {channel!r}: {e}")
            return out
        async for msg in client.iter_messages(entity, limit=limit):
            if not msg or not msg.text:
                continue
            if msg.date < cutoff:
                break                                # iter is newest-first
            parsed = parse(msg.text)
            out.append(IncomingMessage(
                channel_msg_id=msg.id,
                received_at=msg.date.astimezone(timezone.utc),
                text=msg.text,
                parsed=parsed,
            ))
    return out


def fetch_recent(channel: str, *, since_minutes: int = 720, limit: int = 30) -> list[IncomingMessage]:
    """Pull recent messages from the given public channel.
    Returns empty list (with stderr warning) on any failure mode."""
    ok, why = _client_available()
    if not ok:
        print(f"[signals] listener disabled — {why}")
        return []
    try:
        return asyncio.run(_fetch_async(channel, since_minutes=since_minutes, limit=limit))
    except Exception as e:                          # noqa: BLE001
        print(f"[signals] fetch failed: {e}")
        return []


# ---- persistence: avoid double-acting on the same message ----

def already_seen(source: str, channel_msg_id: int) -> bool:
    migrate()
    with ledger.connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM signal_messages WHERE source=? AND channel_msg_id=? LIMIT 1",
            (source, channel_msg_id),
        ).fetchone()
        return row is not None


def record_message(
    *, source: str, channel_msg_id: int, received_at: datetime,
    text: str, parsed: ParsedMessage, action: str | None,
    position_id: int | None = None,
) -> int:
    """Insert (or update) a signal_messages row. Returns row id."""
    migrate()
    import json
    from trading.intelligence.signals.parser import to_dict
    with ledger.connect() as conn:
        cur = conn.execute(
            """INSERT INTO signal_messages
               (source, channel_msg_id, received_at, message_text,
                kind, symbol, parsed_json, action_taken, position_id)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source, channel_msg_id) DO UPDATE
                 SET action_taken = excluded.action_taken,
                     position_id = COALESCE(excluded.position_id, signal_messages.position_id)
               RETURNING id""",
            (
                source, channel_msg_id, received_at.isoformat(timespec="minutes"),
                text, parsed.kind, parsed.symbol, json.dumps(to_dict(parsed)),
                action, position_id,
            ),
        )
        return int(cur.fetchone()[0])
