"""Telegram alerts via the public Bot API. No SDK needed — single HTTPS POST.

Set up:
  1. Talk to @BotFather on Telegram → /newbot → save token in .env as TELEGRAM_BOT_TOKEN
  2. Start a chat with your bot, send any message
  3. Visit https://api.telegram.org/bot<TOKEN>/getUpdates → copy "chat":{"id": ...}
  4. Save the chat id in .env as TELEGRAM_CHAT_ID
  5. Test: `uv run tt tg-test`

All push functions are no-ops if either env var is missing — the bot stays
runnable on a fresh install without Telegram configured.

Kill switch: a HALT file at data/HALT pauses all trading. The orchestrator
checks it before placing any order. Telegram commands are NOT polled (would
need a long-running process); flip the flag via SSH or `tt halt`.
"""
from __future__ import annotations

from pathlib import Path

import httpx

from trading.settings import ROOT, settings

HALT_FILE = ROOT / "data" / "HALT"
TG_API = "https://api.telegram.org/bot{token}/sendMessage"


def _configured() -> bool:
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def notify(text: str, *, parse_mode: str = "Markdown", silent: bool = False) -> bool:
    """Push a message to the configured chat. Returns True on success."""
    if not _configured():
        return False
    try:
        r = httpx.post(
            TG_API.format(token=settings.telegram_bot_token),
            json={
                "chat_id": settings.telegram_chat_id,
                "text": text[:4000],            # Telegram limit ~4096
                "parse_mode": parse_mode,
                "disable_notification": silent,
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:                  # noqa: BLE001
        print(f"[tg] notify failed: {e}")
        return False


# ---- typed helpers — keep messages concise; Telegram is for a phone screen ----

def notify_briefing(day: str, mood: str, mood_reason: str, body: str) -> bool:
    emoji = {"risk_on": "🟢", "neutral": "🟡", "risk_off": "🔴"}.get(mood, "•")
    msg = (
        f"*{emoji} Pre-market {day}*\n"
        f"Mood: *{mood}* — {mood_reason}\n\n"
        f"{body[:1500]}"
    )
    return notify(msg)


def notify_fill(side: str, symbol: str, qty: int, price: float, net: float) -> bool:
    arrow = "🟢⬆" if side == "BUY" else "🔴⬇"
    msg = (
        f"{arrow} *{side} {symbol}*\n"
        f"{qty} @ ₹{price:,.2f} — net ₹{net:,.2f}"
    )
    return notify(msg, silent=False)


def notify_day_summary(day: str, n_fills: int, buys: list[str], sells: list[str],
                       errors: list[str], realised_today: float = 0.0) -> bool:
    sym_buys = ", ".join(buys) or "—"
    sym_sells = ", ".join(sells) or "—"
    err_part = ""
    if errors:
        err_part = "\n\n_Blocked / errors:_\n" + "\n".join(f"• {e}" for e in errors[:8])
    msg = (
        f"*EOD {day}*\n"
        f"Fills: {n_fills}\n"
        f"Buys: {sym_buys}\n"
        f"Sells: {sym_sells}\n"
        f"Realised P&L today: ₹{realised_today:,.2f}"
        f"{err_part}"
    )
    return notify(msg)


def notify_error(where: str, err: str) -> bool:
    return notify(f"⚠️ *Error in {where}*\n```\n{err[:1000]}\n```")


# ---- halt switch ----

def is_halted() -> bool:
    return HALT_FILE.exists()


def halt(reason: str = "manual") -> None:
    HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    HALT_FILE.write_text(reason)
    notify(f"⛔ *Trading HALTED* — {reason}")


def unhalt() -> None:
    if HALT_FILE.exists():
        HALT_FILE.unlink()
    notify("✅ *Trading RESUMED*")
