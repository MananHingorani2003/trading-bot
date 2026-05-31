"""Daily decision loop. One `run_day(today)` call walks the full pipeline:

  pre-market briefing → universe → screen → trend signals →
  Haiku rank → Sonnet analyse (with news) → Opus decide (with mood) →
  risk gate (with earnings blackout) → paper broker → thesis to KB
  → review open positions → Opus sell on rules-flagged ones

Designed to be deterministic given a (date, ledger, AI key) tuple so a single
run is replayable from logs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from trading.ai.client import AIClient
from trading.ai.tiers import haiku_rank, opus_decide, opus_sell_review, sonnet_analyse
from trading.alerts import telegram as tg
from trading.broker.base import Order
from trading.broker.paper import PaperBroker
from trading.calendar import why_closed
from trading.data.fundamentals import fetch_many as fetch_funds
from trading.data.prices import fetch_many as fetch_bars
from trading.data.universe import load_symbols as load_universe
from trading.intelligence import news as news_mod
from trading.intelligence.briefing import build_briefing
from trading.intelligence.events import in_blackout
from trading.intelligence.watchlist import watchlist_candidates
from trading.knowledge import kb, ledger
from trading.knowledge.migrations import migrate
from trading.risk.gates import gate_buy, stop_and_target
from trading.settings import load_strategy
from trading.strategy.momentum import momentum_candidates
from trading.strategy.screen import screen_many
from trading.strategy.trend import Candidate, exit_signal, trend_candidates

TopN_AFTER_RULES = 30
TopN_AFTER_HAIKU = 8
TopN_AFTER_SONNET = 5
MAX_AGGRESSIVE_FROM_RULES = 8         # cap raw momentum candidates fed into the AI


@dataclass
class DayReport:
    day: date
    mood: str = "?"
    universe_size: int = 0
    after_screen: int = 0
    n_candidates: int = 0
    n_aggressive_candidates: int = 0
    n_watchlist_recheck: int = 0
    haiku_picks: list[str] = field(default_factory=list)
    sonnet_recommends: list[str] = field(default_factory=list)
    opus_buys: list[str] = field(default_factory=list)        # core picks
    aggressive_buys: list[str] = field(default_factory=list)  # aggressive picks
    sells: list[str] = field(default_factory=list)
    holds_after_review: list[str] = field(default_factory=list)
    fills: int = 0
    errors: list[str] = field(default_factory=list)


def _bars_until(bars: dict, day: date) -> dict:
    out = {}
    for s, df in bars.items():
        sub = df.loc[:str(day)]
        if not sub.empty:
            out[s] = sub
    return out


def _needs_sell_review(df, *, cfg, position, current_price: float, today: date) -> tuple[bool, str]:
    """Quick rules-only triage: does this position need Opus to look at it?

    Triggers: trend exit signal, near stop (within 2% above), near target
    (within 2% below), unrealised loss > stop_pct, holding past horizon, earnings
    within blackout window. Anything else → HOLD without spending API.
    """
    should_exit, reason = exit_signal(
        df, fast=cfg.signals["trend"]["fast_ema"], slow=cfg.signals["trend"]["slow_ema"],
    )
    if should_exit:
        return True, f"exit_signal: {reason}"

    pnl_pct = (current_price - position["avg_price"]) / position["avg_price"] * 100
    if pnl_pct < -cfg.risk.default_stop_loss_pct * 100:
        return True, f"unrealised pnl {pnl_pct:+.1f}% < stop"
    if pnl_pct > cfg.risk.default_target_r_multiple * cfg.risk.default_stop_loss_pct * 100:
        return True, f"unrealised pnl {pnl_pct:+.1f}% >= target"

    blacked, why = in_blackout(position["symbol"], today, days=2)
    if blacked:
        return True, f"earnings blackout: {why}"

    return False, "no review trigger"


def run_day(
    today: date,
    *,
    universe_limit: int | None = None,
    universe: Literal["nifty500", "watchlist"] = "nifty500",
    history_days: int = 200,
    skip_briefing: bool = False,
) -> DayReport:
    cfg = load_strategy()
    migrate()

    if universe == "watchlist":
        from trading.settings import load_watchlist
        symbols = load_watchlist()
    else:
        symbols = load_universe()
    if universe_limit:
        symbols = symbols[:universe_limit]

    report = DayReport(day=today)
    report.universe_size = len(symbols)
    print(f"[day {today}] universe size: {len(symbols)}")

    closed = why_closed(today)
    if closed:
        msg = f"market closed ({closed}); skipping"
        print(f"[day {today}] {msg}")
        tg.notify(f"😴 Skipped {today} — {closed}", silent=True)
        report.errors.append(msg)
        return report

    if tg.is_halted():
        msg = f"HALT flag set ({tg.HALT_FILE}); aborting run-day"
        print(f"[day {today}] {msg}")
        tg.notify(f"⛔ Skipped run-day {today} — {msg}")
        report.errors.append(msg)
        return report

    client = AIClient()
    if not client.available:
        print(f"[day {today}] NO ANTHROPIC_API_KEY — running rules-only")

    # 1) Pre-market briefing (cheap Sonnet; injects mood into Opus decide later)
    mood_snapshot: dict | None = None
    if not skip_briefing:
        b = build_briefing(client, day=today, strategy_version=cfg.version)
        report.mood = b.mood
        mood_snapshot = {
            "mood": b.mood, "mood_reason": b.mood_reason,
            "watch_today": b.watch_today, "positions_at_risk": b.positions_at_risk,
        }
        print(f"[day {today}] mood: {b.mood} ({b.mood_reason})")

    # 2) Market data
    bars_all = fetch_bars(symbols, today - timedelta(days=history_days + 60), today)
    bars = _bars_until(bars_all, today)
    funds = fetch_funds(list(bars.keys()))

    # 3) Screen
    screens = screen_many(bars, funds, universe_cfg=cfg.universe)
    passed = [s.symbol for s in screens if s.passed]
    report.after_screen = len(passed)
    print(f"[day {today}] screen passed: {len(passed)}/{len(symbols)}")

    bars_pass = {s: bars[s] for s in passed}
    cands = trend_candidates(
        bars_pass,
        fast=cfg.signals["trend"]["fast_ema"],
        slow=cfg.signals["trend"]["slow_ema"],
        adx_min=cfg.signals["trend"]["adx_min"],
    )[:TopN_AFTER_RULES]
    report.n_candidates = len(cands)
    print(f"[day {today}] trend candidates (core): {len(cands)}")

    # --- Aggressive sleeve: news-catalyst breakouts ---
    aggressive_cfg = cfg.sleeves.get("aggressive") if cfg.sleeves else None
    agg_cands_raw = []
    if aggressive_cfg:
        try:
            # company map for news lookups
            from trading.data.universe import load_nifty500
            comp_map = {r["symbol"]: r["company"] for r in load_nifty500()}
        except Exception:                       # noqa: BLE001
            comp_map = {}
        agg_cands_raw = momentum_candidates(
            bars_pass, funds, comp_map, cfg=aggressive_cfg,
        )[:MAX_AGGRESSIVE_FROM_RULES]
        report.n_aggressive_candidates = len(agg_cands_raw)
        print(f"[day {today}] momentum candidates (aggressive): {len(agg_cands_raw)}")

    # Convert MomentumCandidate -> Candidate so AI tiers can reuse their shapes.
    # Keep a side-table tracking which symbols are aggressive sleeve.
    agg_symbols: set[str] = set()
    for m in agg_cands_raw:
        agg_symbols.add(m.symbol)
        if any(c.symbol == m.symbol for c in cands):
            continue                            # avoid double-listing if both signals fire
        cands.append(Candidate(
            symbol=m.symbol, last_close=m.last_close,
            fast_ema=m.last_close, slow_ema=m.last_close,
            adx=0.0, rsi=m.rsi, atr_pct=m.atr_pct,
            pct_from_high=0.0, vol_surge=m.vol_surge,
            score=m.score,
            reason=f"[aggressive] {m.reason} | latest: {m.latest_news_title}",
        ))

    # --- Watchlist sweep: force-include recently WATCH'd symbols even if
    #     they aren't in today's trend/momentum candidates. Sonnet's history
    #     lookup will surface the original "wait for X" reasoning. ---
    wl_cands = watchlist_candidates(bars_pass)
    existing_syms = {c.symbol for c in cands}
    added = 0
    for wc in wl_cands:
        if wc.symbol in existing_syms:
            continue
        cands.append(wc)
        added += 1
    report.n_watchlist_recheck = added
    if added:
        print(f"[day {today}] watchlist re-check: {added} symbols force-included")
    # Re-sort so high-priority watchlist entries surface to Haiku
    cands.sort(key=lambda c: c.score, reverse=True)
    cands = cands[:TopN_AFTER_RULES + len(wl_cands)]

    # 4) Reconstruct cash for this day's broker session
    broker = PaperBroker(starting_cash=cfg.portfolio.capital)
    with ledger.connect() as conn:
        open_positions = [dict(r) for r in ledger.open_positions(conn)]
    invested = sum(p["qty"] * p["avg_price"] for p in open_positions)
    broker._cash = max(0.0, cfg.portfolio.capital - invested)

    # 5) Sell-side review on each open position (Opus only on flagged ones)
    news_mod.refresh_all()
    news_mod.prune_old()

    # Build a symbol→company map (better per-symbol news matching)
    company_by_symbol: dict[str, str] = {}
    try:
        from trading.data.universe import load_nifty500
        company_by_symbol = {row["symbol"]: row["company"] for row in load_nifty500()}
    except Exception:                       # noqa: BLE001
        pass
    for p in open_positions:
        sym = p["symbol"]
        df = bars.get(sym)
        if df is None or df.empty:
            continue
        current_price = float(df["Close"].iloc[-1])
        flagged, why = _needs_sell_review(df, cfg=cfg, position=p, current_price=current_price, today=today)
        if not flagged:
            report.holds_after_review.append(sym)
            continue

        # Pull original thesis for context
        with ledger.connect() as conn:
            thesis_row = conn.execute(
                "SELECT summary_md, target_price, stop_price, time_horizon FROM theses WHERE id=?",
                (p["thesis_id"],),
            ).fetchone() if p["thesis_id"] else None

        days_held = (today - date.fromisoformat(p["opened_at"][:10])).days
        technicals = {"current_price": current_price, "rules_flag": why}

        sd = opus_sell_review(
            client,
            symbol=sym, qty=p["qty"], avg_price=p["avg_price"],
            opened_at=p["opened_at"], days_held=days_held,
            current_price=current_price,
            thesis_md=(thesis_row["summary_md"] if thesis_row else ""),
            target_price=(thesis_row["target_price"] if thesis_row else None),
            stop_price=(thesis_row["stop_price"] if thesis_row else None),
            time_horizon=(thesis_row["time_horizon"] if thesis_row else None),
            technicals=technicals,
            rules_flag=why,
            strategy_version=cfg.version,
        )

        ts = f"{today.isoformat()}T15:20:00"
        if sd.action == "EXIT":
            try:
                fill = broker.place_order(Order(symbol=sym, side="SELL", qty=p["qty"]), ref_price=current_price, ts=ts)
                report.sells.append(sym)
                report.fills += 1
                tg.notify_fill("SELL", sym, p["qty"], fill.price, p["qty"] * fill.price)
            except Exception as e:                  # noqa: BLE001
                report.errors.append(f"sell {sym}: {e}")
        elif sd.action == "TRIM" and sd.trim_pct > 0:
            trim_qty = max(1, int(p["qty"] * sd.trim_pct))
            try:
                fill = broker.place_order(Order(symbol=sym, side="SELL", qty=trim_qty), ref_price=current_price, ts=ts)
                report.sells.append(f"{sym}(trim {trim_qty})")
                report.fills += 1
                tg.notify_fill("SELL", f"{sym} (trim)", trim_qty, fill.price, trim_qty * fill.price)
            except Exception as e:                  # noqa: BLE001
                report.errors.append(f"trim {sym}: {e}")
        else:
            report.holds_after_review.append(f"{sym}(opus-hold)")

    # 6) Buy pipeline
    if cands:
        picks = haiku_rank(client, cands, top_n=TopN_AFTER_HAIKU, strategy_version=cfg.version)
        report.haiku_picks = picks
        cand_by_sym = {c.symbol: c for c in cands}

        shortlist: list = []
        watchlist: list = []                    # 'watch' fallback if no consider_buy
        for sym in picks:
            cand = cand_by_sym.get(sym)
            if not cand:
                continue
            hits = news_mod.for_symbol(
                sym, company=company_by_symbol.get(sym), days=7, limit=5,
            )
            news_lines = [h.title for h in hits]
            analysis = sonnet_analyse(
                client, cand, funds.get(sym),
                strategy_version=cfg.version, news_lines=news_lines,
            )
            if not analysis:
                continue
            if analysis.recommendation == "consider_buy":
                report.sonnet_recommends.append(sym)
                shortlist.append((cand, analysis))
            elif analysis.recommendation == "watch":
                watchlist.append((cand, analysis))
            if len(shortlist) >= TopN_AFTER_SONNET:
                break

        # If Sonnet was too conservative and we got no clear buys, promote the
        # 'watch' recommendations into the shortlist so Opus still gets to
        # decide. Opus is the final gatekeeper anyway; better to let it see
        # candidates with full context than to silently exit with no buys.
        if not shortlist and watchlist:
            print(f"[day {today}] no consider_buy from Sonnet; promoting {len(watchlist)} 'watch' to Opus")
            shortlist = watchlist[:TopN_AFTER_SONNET]
            report.sonnet_recommends.extend(c.symbol for c, _ in shortlist)

        with ledger.connect() as conn:
            n_open = len(ledger.open_positions(conn))
        capacity = max(0, cfg.portfolio.max_positions - n_open)

        buys = opus_decide(
            client, shortlist,
            cash=broker.cash(), n_open=n_open,
            max_positions=cfg.portfolio.max_positions,
            capacity=capacity, capital=cfg.portfolio.capital,
            mood=mood_snapshot, strategy_version=cfg.version,
        )

        for buy in buys:
            cand = cand_by_sym.get(buy.symbol)
            if not cand:
                continue

            # Route to the right sleeve based on which signal generator produced it
            sleeve = "aggressive" if buy.symbol in agg_symbols else "core"

            # Earnings blackout — last check before risk gate
            blackout, why = in_blackout(buy.symbol, today, days=2)
            if blackout:
                with ledger.connect() as conn:
                    did = ledger.insert_decision(
                        conn, kind="buy_exec", symbol=buy.symbol, action="SKIP",
                        strategy_version=cfg.version, ai_tier="rules",
                        reasoning_md=f"[{sleeve}] earnings blackout: {why}",
                    )
                    kb.index_decision(conn, did)
                report.errors.append(f"buy {buy.symbol} blocked: {why}")
                continue

            gate = gate_buy(
                symbol=buy.symbol, price=cand.last_close, cfg=cfg,
                cash_available=broker.cash(), sleeve=sleeve,
            )
            ts = f"{today.isoformat()}T10:00:00"
            with ledger.connect() as conn:
                did = ledger.insert_decision(
                    conn, kind="buy_exec", symbol=buy.symbol,
                    action="BUY" if gate.allowed else "SKIP",
                    strategy_version=cfg.version, ai_tier="opus",
                    reasoning_md=(
                        f"[{sleeve}] gate={gate.reason} qty={gate.qty} conf={buy.confidence:.2f}\n\n"
                        f"thesis: {buy.thesis_md}"
                    ),
                )
                kb.index_decision(conn, did)

            if not gate.allowed:
                report.errors.append(f"buy {buy.symbol} blocked ({sleeve}): {gate.reason}")
                continue

            try:
                fill = broker.place_order(
                    Order(symbol=buy.symbol, side="BUY", qty=gate.qty,
                          decision_id=did, sleeve=sleeve),
                    ref_price=cand.last_close, ts=ts,
                )
                tg.notify_fill(
                    f"BUY[{sleeve[:3]}]", buy.symbol, gate.qty, fill.price,
                    gate.qty * fill.price,
                )
                stop, target = stop_and_target(
                    entry=fill.price, atr_pct=cand.atr_pct, cfg=cfg, sleeve=sleeve,
                )
                with ledger.connect() as conn:
                    pos = ledger.find_open_position(conn, buy.symbol)
                    thid = conn.execute(
                        """INSERT INTO theses (position_id, summary_md, target_price, stop_price,
                                              time_horizon, catalysts, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (pos["id"], f"[{sleeve}] " + buy.thesis_md, target, stop,
                         buy.time_horizon, ",".join(buy.catalysts), ts),
                    ).lastrowid
                    conn.execute("UPDATE positions SET thesis_id=? WHERE id=?", (thid, pos["id"]))
                    kb.index_thesis(conn, int(thid))
                if sleeve == "aggressive":
                    report.aggressive_buys.append(buy.symbol)
                else:
                    report.opus_buys.append(buy.symbol)
                report.fills += 1
            except Exception as e:                  # noqa: BLE001
                report.errors.append(f"buy {buy.symbol}: {e}")

    # EOD summary
    with ledger.connect() as conn:
        realised_today = conn.execute(
            "SELECT COALESCE(SUM(realised_pnl),0) p FROM tax_realisations WHERE sell_date=?",
            (today.isoformat(),),
        ).fetchone()["p"] or 0.0
    tg.notify_day_summary(
        today.isoformat(),
        n_fills=report.fills,
        buys=report.opus_buys,
        sells=report.sells,
        errors=report.errors,
        realised_today=realised_today,
    )

    return report
