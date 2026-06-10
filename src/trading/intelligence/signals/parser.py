"""Parser for SEBI-registered analyst Telegram channels (LemonnHQ format).

Two message kinds we care about:

  1. BUY signal — kind='buy'
     📈 STOCKS | 📦 DELIVERY
     🟢 BUY WOCKPHARMA
     💰 Entry Range: ₹1880.00 - ₹1888.00
     🎯 Target: ₹2100.00
     🛑 Stop Loss: ₹1690.00
     ⏳ Valid for: 30 days
     ⏰ 09 Jun 2026, 01:46 PM

  2. Exit update — kind='update'
     Update on FEDERALBNK
     Book Profit at 315.00 (5.70% profit)
     -- or --
     Book Loss at 290.00 (-3.10%)

The parser is strict: anything that doesn't match returns kind='unknown'
and the caller logs+skips. Never executes orders on ambiguous text.

F&O messages (`📊 F&O`) are explicitly rejected at the kind check.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime

# --- header markers ---
_RE_STOCK_DELIVERY = re.compile(
    r"(STOCKS\s*\|\s*DELIVERY|STOCKS\s*\|\s*\W*DELIVERY)", re.I
)
_RE_FNO = re.compile(r"F\s*&\s*O", re.I)

# --- buy message ---
_RE_BUY_LINE = re.compile(r"🟢\s*BUY\s+([A-Z][A-Z0-9&\-]+)", re.I)
_RE_ENTRY = re.compile(r"Entry\s*Range[:\s]*₹?\s*([\d,]+\.?\d*)\s*-\s*₹?\s*([\d,]+\.?\d*)", re.I)
_RE_TARGET = re.compile(r"Target[:\s]*₹?\s*([\d,]+\.?\d*)", re.I)
_RE_STOP = re.compile(r"Stop\s*Loss[:\s]*₹?\s*([\d,]+\.?\d*)", re.I)
_RE_VALID = re.compile(r"Valid\s*for[:\s]*([0-9]+)\s*(day|week|month)s?", re.I)
_RE_TS = re.compile(r"(\d{1,2}\s+\w+\s+\d{4}),?\s+(\d{1,2}:\d{2})\s*(AM|PM)?", re.I)

# --- update message ---
_RE_UPDATE_HEAD = re.compile(r"Update\s+on\s+([A-Z][A-Z0-9&\-]+)", re.I)
_RE_BOOK_PROFIT = re.compile(r"Book\s*Profit\s*at\s*₹?\s*([\d,]+\.?\d*)", re.I)
_RE_BOOK_LOSS = re.compile(r"Book\s*Loss\s*at\s*₹?\s*([\d,]+\.?\d*)", re.I)


@dataclass
class BuySignal:
    symbol: str
    entry_low: float
    entry_high: float
    target: float
    stop_loss: float
    valid_days: int
    issued_at: str | None
    raw_text: str


@dataclass
class UpdateSignal:
    symbol: str
    side: str               # 'book_profit' | 'book_loss'
    exit_price: float
    raw_text: str


@dataclass
class ParsedMessage:
    kind: str               # 'buy' | 'update' | 'unknown' | 'rejected'
    reason: str             # why rejected/unknown
    symbol: str | None = None
    buy: BuySignal | None = None
    update: UpdateSignal | None = None


def _f(s: str) -> float:
    return float(s.replace(",", ""))


def _parse_ts(text: str) -> str | None:
    m = _RE_TS.search(text)
    if not m:
        return None
    date_part, time_part, ampm = m.group(1), m.group(2), m.group(3)
    fmt = "%d %b %Y %I:%M %p" if ampm else "%d %b %Y %H:%M"
    try:
        dt = datetime.strptime(f"{date_part} {time_part} {ampm or ''}".strip(), fmt.strip())
        return dt.isoformat(timespec="minutes")
    except ValueError:
        return None


def parse(text: str) -> ParsedMessage:
    """Identify and parse a channel message. Returns kind='unknown' or
    'rejected' if not actionable; caller logs+skips those."""
    if not text or not text.strip():
        return ParsedMessage(kind="unknown", reason="empty message")

    # Reject F&O outright
    if _RE_FNO.search(text):
        return ParsedMessage(kind="rejected", reason="F&O message — not equity delivery")

    # ---- Update message? ----
    m_update = _RE_UPDATE_HEAD.search(text)
    if m_update:
        symbol = m_update.group(1).upper()
        if (m := _RE_BOOK_PROFIT.search(text)):
            return ParsedMessage(
                kind="update", reason="ok", symbol=symbol,
                update=UpdateSignal(symbol, "book_profit", _f(m.group(1)), text),
            )
        if (m := _RE_BOOK_LOSS.search(text)):
            return ParsedMessage(
                kind="update", reason="ok", symbol=symbol,
                update=UpdateSignal(symbol, "book_loss", _f(m.group(1)), text),
            )
        return ParsedMessage(
            kind="unknown",
            reason=f"Update on {symbol} but no book profit/loss directive",
            symbol=symbol,
        )

    # ---- BUY signal? Require explicit STOCKS|DELIVERY header ----
    if not _RE_STOCK_DELIVERY.search(text):
        return ParsedMessage(kind="rejected", reason="missing STOCKS|DELIVERY header")

    m_buy = _RE_BUY_LINE.search(text)
    if not m_buy:
        return ParsedMessage(kind="unknown", reason="no BUY line")
    symbol = m_buy.group(1).upper()

    m_entry = _RE_ENTRY.search(text)
    m_target = _RE_TARGET.search(text)
    m_stop = _RE_STOP.search(text)
    if not (m_entry and m_target and m_stop):
        return ParsedMessage(
            kind="unknown",
            reason=f"missing entry/target/stop on BUY {symbol}",
            symbol=symbol,
        )

    valid_days = 30
    if (m := _RE_VALID.search(text)):
        n = int(m.group(1)); unit = m.group(2).lower()
        valid_days = n if unit == "day" else n * 7 if unit == "week" else n * 30

    buy = BuySignal(
        symbol=symbol,
        entry_low=_f(m_entry.group(1)),
        entry_high=_f(m_entry.group(2)),
        target=_f(m_target.group(1)),
        stop_loss=_f(m_stop.group(1)),
        valid_days=valid_days,
        issued_at=_parse_ts(text),
        raw_text=text,
    )
    return ParsedMessage(kind="buy", reason="ok", symbol=symbol, buy=buy)


def to_dict(p: ParsedMessage) -> dict:
    return {
        "kind": p.kind, "reason": p.reason, "symbol": p.symbol,
        "buy": asdict(p.buy) if p.buy else None,
        "update": asdict(p.update) if p.update else None,
    }
