"""Pre-market briefing — combines cues + news + open-position news into a Sonnet
call that writes a journal entry and emits a `mood` snapshot for the day.

Cost: ~$0.01-0.03 per briefing. If no AI key, builds a deterministic rules-only
briefing instead.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date

from trading.ai import prompts
from trading.ai.client import SONNET, AIClient, parse_json_object
from trading.intelligence import news as news_mod
from trading.intelligence.sentiment import cues_to_dict, fetch_cues
from trading.knowledge import kb, ledger


@dataclass
class Briefing:
    date: str
    mood: str                       # 'risk_on' | 'neutral' | 'risk_off'
    mood_reason: str
    watch_today: list[str]
    positions_at_risk: list[str]
    briefing_md: str


def _rules_only_briefing(day: date, cues: list, headlines: list) -> Briefing:
    # Use Dow + India VIX as a coarse risk read
    cues_map = {c.name: c for c in cues}
    dow = cues_map.get("dow")
    vix = cues_map.get("india_vix")
    dow_pct = dow.pct_1d if dow else 0.0
    vix_close = vix.close if vix else 0.0

    if (dow_pct or 0.0) > 0.5 and (vix_close or 20) < 18:
        mood = "risk_on"
    elif (dow_pct or 0.0) < -0.5 or (vix_close or 0) > 22:
        mood = "risk_off"
    else:
        mood = "neutral"

    body = "Rules-only briefing (no AI):\n" + "\n".join(
        f"- {c.name}: {c.close} ({c.pct_1d:+.2f}% 1d)"
        for c in cues if c.close is not None
    )
    body += f"\n- {len(headlines)} headlines pulled across feeds"

    return Briefing(
        date=day.isoformat(), mood=mood,
        mood_reason=f"dow {dow_pct or 0:+.2f}%, vix {vix_close or 0:.1f}",
        watch_today=[], positions_at_risk=[], briefing_md=body,
    )


def build_briefing(client: AIClient, *, day: date, strategy_version: str) -> Briefing:
    cues = fetch_cues(as_of=day)
    news_mod.refresh_all()                           # pull latest into DB
    news_mod.prune_old()                             # keep 7-day window
    headlines = news_mod.recent(days=2, limit=200)   # short list used by fallback briefing
    macro = news_mod.macro_top(days=2, limit=20)     # last 48h for the prompt

    # Build a symbol→company map once for nicer per-position filtering
    company_by_symbol: dict[str, str] = {}
    try:
        from trading.data.universe import load_nifty500
        company_by_symbol = {row["symbol"]: row["company"] for row in load_nifty500()}
    except Exception:                                # noqa: BLE001
        pass

    # Per-open-position recent news (FTS5, last 7 days)
    with ledger.connect() as conn:
        positions = [dict(r) for r in ledger.open_positions(conn)]
    pos_news_lines: list[str] = []
    for p in positions:
        hits = news_mod.for_symbol(
            p["symbol"], company=company_by_symbol.get(p["symbol"]), days=7, limit=3,
        )
        if hits:
            for h in hits:
                pos_news_lines.append(f"  {p['symbol']}: {h.title}")
        else:
            pos_news_lines.append(f"  {p['symbol']}: (no news in last 7d)")
    pos_news_text = "\n".join(pos_news_lines) or "  (no open positions)"

    if not client.available:
        b = _rules_only_briefing(day, cues, headlines)
    else:
        macro_text = "\n".join(f"  - [{h.source}] {h.title}" for h in macro)
        user = prompts.BRIEFING_USER_TMPL.format(
            date=day.isoformat(),
            cues_json=json.dumps(cues_to_dict(cues)),
            macro_news=macro_text,
            positions_news=pos_news_text,
        )
        r = client.message(
            model=SONNET, system=prompts.BRIEFING_SYSTEM, user=user,
            max_tokens=800, temperature=0.3,
        )
        try:
            d = parse_json_object(
                r.text,
                required_keys=["mood", "briefing_md", "mood_reason"],
            )
            b = Briefing(
                date=day.isoformat(),
                mood=str(d.get("mood", "neutral")),
                mood_reason=str(d.get("mood_reason", "")),
                watch_today=list(d.get("watch_today") or []),
                positions_at_risk=list(d.get("positions_at_risk") or []),
                briefing_md=str(d.get("briefing_md", "")),
            )
            # Sanity: if Sonnet returned an object that LOOKS valid but is
            # actually empty, surface its raw response so we don't ship a
            # silent blank briefing.
            if not b.briefing_md and not b.mood_reason:
                b.briefing_md = (
                    "(Sonnet returned an empty object — raw response below)\n\n"
                    + r.text[:1500]
                )
        except Exception as e:                  # noqa: BLE001
            b = _rules_only_briefing(day, cues, headlines)
            b.briefing_md += (
                f"\n\n(sonnet parse failed: {e})\n\n"
                f"--- raw response ---\n{r.text[:1500]}"
            )

    # Persist: journal row + KB index + decision row
    with ledger.connect() as conn:
        conn.execute(
            """INSERT INTO journals (date, premarket, notes)
               VALUES (?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET premarket=excluded.premarket""",
            (b.date, b.briefing_md, f"mood={b.mood}"),
        )
        did = ledger.insert_decision(
            conn, kind="premarket",
            reasoning_md=b.briefing_md,
            strategy_version=strategy_version,
            ai_tier="sonnet" if client.available else "rules",
            market_snapshot={
                "mood": b.mood, "mood_reason": b.mood_reason,
                "watch_today": b.watch_today,
                "positions_at_risk": b.positions_at_risk,
                "cues": cues_to_dict(cues),
            },
        )
        kb.index_decision(conn, did)
        kb.index_journal(conn, b.date, b.briefing_md)

    return b
