"""News fetchers — free RSS, persisted to SQLite with 7-day retention.

Why SQLite-backed (vs JSON cache):
- 7-day rolling history queryable by symbol or date (one RSS poll only sees ~50 items)
- FTS5 full-text search → smarter per-symbol filter using company names too
- Surfaces in the dashboard
- Deduped by URL across feed pulls

Free feeds, no auth:
  MoneyControl: markets, buzzing stocks, results
  ET Markets:   markets, stocks
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import feedparser

from trading.knowledge import ledger
from trading.knowledge.migrations import migrate

FEEDS = {
    "moneycontrol_markets": "https://www.moneycontrol.com/rss/marketreports.xml",
    "moneycontrol_buzz":    "https://www.moneycontrol.com/rss/buzzingstocks.xml",
    "moneycontrol_results": "https://www.moneycontrol.com/rss/results.xml",
    "et_markets":           "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "et_stocks":            "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
}

RETENTION_DAYS = 7


@dataclass
class Headline:
    source: str
    title: str
    summary: str
    link: str
    published_iso: str


# ---------- ingest ----------

def _parse_pub(s: str) -> str:
    """Normalize any common RSS date format to ISO yyyy-mm-ddThh:mm:ss."""
    if not s:
        return ""
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            return datetime.strptime(s, fmt).astimezone().isoformat(timespec="seconds")
        except ValueError:
            continue
    return s[:25]      # last-resort: keep prefix


def _ingest_feed(source: str, url: str) -> int:
    migrate()
    inserted = 0
    try:
        parsed = feedparser.parse(url)
    except Exception as e:                  # noqa: BLE001
        print(f"[news] {source} parse failed: {e}")
        return 0

    with ledger.connect() as conn:
        for e in parsed.entries[:80]:
            link = (e.get("link") or "").strip()
            if not link:
                continue
            title = (e.get("title") or "").strip()
            summary = re.sub(r"<[^>]+>", " ", e.get("summary") or "")[:600].strip()
            pub = _parse_pub(e.get("published") or e.get("updated") or "")
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO headlines (source, title, summary, link, published_iso, fetched_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (source, title, summary, link, pub, time.time()),
                )
                if cur.rowcount:
                    inserted += 1
            except Exception as ex:         # noqa: BLE001
                print(f"[news] insert fail: {ex}")
    return inserted


def refresh_all() -> dict[str, int]:
    """Poll every feed once. Returns {source: new_rows}."""
    return {src: _ingest_feed(src, url) for src, url in FEEDS.items()}


def prune_old(days: int = RETENTION_DAYS) -> int:
    cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
    migrate()
    with ledger.connect() as conn:
        cur = conn.execute(
            "DELETE FROM headlines WHERE COALESCE(published_iso,'') < ? "
            "AND fetched_at < ?",
            (cutoff, time.time() - days * 86400),
        )
        return cur.rowcount


# ---------- query ----------

def _row_to_headline(r) -> Headline:
    return Headline(
        source=r["source"], title=r["title"], summary=r["summary"] or "",
        link=r["link"] or "", published_iso=r["published_iso"] or "",
    )


def recent(days: int = 7, *, limit: int = 200) -> list[Headline]:
    migrate()
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with ledger.connect() as conn:
        rows = conn.execute(
            "SELECT source, title, summary, link, published_iso FROM headlines "
            "WHERE COALESCE(published_iso,'') >= ? "
            "ORDER BY published_iso DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
    return [_row_to_headline(r) for r in rows]


def _fts_query(needles: list[str]) -> str:
    """Build a safe FTS5 OR query. Strip non-alphanum to dodge tokenizer quirks."""
    parts = []
    for n in needles:
        n = re.sub(r"[^A-Za-z0-9]+", " ", n).strip()
        if len(n) < 3:
            continue
        # Quote multi-word names; otherwise prefix search
        if " " in n:
            parts.append(f'"{n}"')
        else:
            parts.append(f"{n}*")
    return " OR ".join(parts) if parts else ""


def for_symbol(
    symbol: str,
    *,
    company: str | None = None,
    days: int = 7,
    limit: int = 25,
) -> list[Headline]:
    """Per-symbol news in the last `days`. Uses FTS5; matches ticker + company name."""
    migrate()
    needles = [symbol]
    if company:
        # Strip suffixes like "Ltd", "Limited", "& Co" that match too broadly
        clean = re.sub(r"\b(ltd|limited|corp|inc|& co|company)\b\.?", "", company, flags=re.I).strip()
        if clean:
            needles.append(clean)
    q = _fts_query(needles)
    if not q:
        return []

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    with ledger.connect() as conn:
        rows = conn.execute(
            """SELECT h.source, h.title, h.summary, h.link, h.published_iso
               FROM headlines h
               JOIN headlines_fts f ON f.rowid = h.id
               WHERE headlines_fts MATCH ?
                 AND COALESCE(h.published_iso,'') >= ?
               ORDER BY h.published_iso DESC
               LIMIT ?""",
            (q, cutoff, limit),
        ).fetchall()
    return [_row_to_headline(r) for r in rows]


def macro_top(days: int = 1, limit: int = 25) -> list[Headline]:
    return recent(days=days, limit=limit)


# ---------- backward-compat shims used by older callers ----------

def fetch_all() -> list[Headline]:
    """Old callers used this to mean 'give me the latest news'.
    Now: refresh feeds + return the last 24h."""
    refresh_all()
    prune_old()
    return recent(days=1, limit=200)


def filter_by_symbol(headlines: list[Headline], symbol: str, company: str | None = None) -> list[Headline]:
    """Legacy in-memory filter — kept so existing callers don't break.
    Prefer for_symbol() which uses FTS5 on the persisted store."""
    needles = [symbol.lower()]
    if company:
        needles.append(company.lower().split()[0])
    out = []
    for h in headlines:
        blob = f"{h.title} {h.summary}".lower()
        if any(n in blob for n in needles if len(n) >= 3):
            out.append(h)
    return out
