"""Tiered AI orchestration: Haiku rank → Sonnet analyse → Opus decide.

Every tier call writes a row to `decisions` with token + cost metadata, even
when the AI client is unavailable (we log the rules-only fallback).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from trading.ai import prompts
from trading.ai.client import HAIKU, OPUS, SONNET, AIClient, parse_json, parse_json_object
from trading.data.fundamentals import Fundamentals
from trading.knowledge import kb, ledger
from trading.strategy.trend import Candidate


@dataclass
class SonnetAnalysis:
    symbol: str
    setup_quality: str
    key_positives: list[str]
    key_risks: list[str]
    fundamental_flag: str
    recommendation: str
    reason_md: str


@dataclass
class BuyDecision:
    symbol: str
    confidence: float
    thesis_md: str
    time_horizon: str
    catalysts: list[str]


@dataclass
class SellDecision:
    action: str               # 'HOLD' | 'TRIM' | 'EXIT'
    trim_pct: float           # 0.0 unless TRIM
    confidence: float
    reason_md: str


def _log_decision(**kwargs) -> int:
    with ledger.connect() as conn:
        did = ledger.insert_decision(conn, **kwargs)
        kb.index_decision(conn, did)
        return did


# ---- Tier 1 ----

def haiku_rank(
    client: AIClient, candidates: list[Candidate], *, top_n: int, strategy_version: str,
) -> list[str]:
    """Pick top_n symbols from candidates. Falls back to score-sort if AI unavailable."""
    if not candidates:
        return []
    if not client.available:
        picked = [c.symbol for c in candidates[:top_n]]
        _log_decision(
            kind="scan", reasoning_md=f"rules-only top{top_n} by score: {picked}",
            strategy_version=strategy_version, ai_tier="rules",
        )
        return picked

    payload = [
        {"sym": c.symbol, "score": c.score, "adx": c.adx, "rsi": c.rsi,
         "atr_pct": c.atr_pct, "ema_sep_pct": round((c.fast_ema - c.slow_ema) / c.slow_ema * 100, 2)}
        for c in candidates
    ]
    user = prompts.HAIKU_RANK_USER_TMPL.format(
        n=len(candidates), top_n=top_n, candidates_json=json.dumps(payload),
    )
    r = client.message(
        model=HAIKU, system=prompts.HAIKU_RANK_SYSTEM, user=user,
        max_tokens=300, temperature=0.1,
    )
    try:
        picks = parse_json(r.text)
        if not isinstance(picks, list):
            raise ValueError("expected list")
        picks = [str(s).upper() for s in picks][:top_n]
    except Exception as e:                       # noqa: BLE001
        picks = [c.symbol for c in candidates[:top_n]]
        _log_decision(
            kind="scan",
            reasoning_md=f"haiku parse failed ({e}); fell back to score-sort: {picks}",
            strategy_version=strategy_version, ai_tier="haiku",
            tokens_in=r.tokens_in, tokens_out=r.tokens_out, cost_usd=r.cost_usd,
        )
        return picks

    _log_decision(
        kind="scan",
        reasoning_md=f"haiku top{top_n}: {picks}",
        strategy_version=strategy_version, ai_tier="haiku",
        tokens_in=r.tokens_in, tokens_out=r.tokens_out, cost_usd=r.cost_usd,
    )
    return picks


# ---- Tier 2 ----

def sonnet_analyse(
    client: AIClient,
    candidate: Candidate,
    fund: Fundamentals | None,
    *, strategy_version: str,
    news_lines: list[str] | None = None,
) -> SonnetAnalysis | None:
    """Return None if AI unavailable — orchestrator should skip / pass-through."""
    if not client.available:
        # Rules-only: pass the candidate forward as 'decent' if score positive.
        rec = "consider_buy" if candidate.score > 0 else "skip"
        analysis = SonnetAnalysis(
            symbol=candidate.symbol,
            setup_quality="decent" if candidate.score > 0 else "weak",
            key_positives=[candidate.reason], key_risks=["no AI vetting available"],
            fundamental_flag="unknown" if not fund else "ok",
            recommendation=rec,
            reason_md=f"rules-only pass-through: {candidate.reason}",
        )
        _log_decision(
            kind="analyse", symbol=candidate.symbol, action=rec.upper(),
            strategy_version=strategy_version,
            reasoning_md=analysis.reason_md, ai_tier="rules",
        )
        return analysis

    with ledger.connect() as conn:
        hist = kb.history_for_symbol(conn, candidate.symbol, limit=5)
    hist_text = "\n".join(f"- {h['ts']} {h['kind']} {h['action'] or ''}: {h['reasoning_md'][:140]}" for h in hist) or "  (none)"

    user = prompts.SONNET_ANALYSE_USER_TMPL.format(
        symbol=candidate.symbol,
        industry=(fund.industry if fund else "?") or "?",
        technicals_json=json.dumps({
            "last_close": candidate.last_close, "fast_ema": round(candidate.fast_ema, 2),
            "slow_ema": round(candidate.slow_ema, 2), "adx": round(candidate.adx, 1),
            "rsi": round(candidate.rsi, 1), "atr_pct": round(candidate.atr_pct, 2),
            "pct_from_52w_high": candidate.pct_from_high,
            "volume_vs_20d_avg": candidate.vol_surge,
            "score": candidate.score,
        }),
        fundamentals_json=json.dumps(asdict(fund) if fund else {}, default=str),
        recent_news="\n".join(f"  - {n}" for n in (news_lines or [])) or "  (none)",
        history=hist_text,
    )
    r = client.message(
        model=SONNET, system=prompts.SONNET_ANALYSE_SYSTEM, user=user,
        max_tokens=600, temperature=0.2,
    )
    try:
        d = parse_json_object(
            r.text,
            required_keys=["recommendation", "setup_quality", "reason_md"],
        )
        analysis = SonnetAnalysis(symbol=candidate.symbol, **{k: d[k] for k in (
            "setup_quality", "key_positives", "key_risks",
            "fundamental_flag", "recommendation", "reason_md",
        )})
    except Exception as e:                       # noqa: BLE001
        _log_decision(
            kind="analyse", symbol=candidate.symbol, action="SKIP",
            strategy_version=strategy_version,
            reasoning_md=f"sonnet parse failed: {e}; raw: {r.text[:300]}",
            ai_tier="sonnet",
            tokens_in=r.tokens_in, tokens_out=r.tokens_out, cost_usd=r.cost_usd,
        )
        return None

    _log_decision(
        kind="analyse", symbol=candidate.symbol,
        action={"consider_buy": "CONSIDER", "watch": "WATCH", "skip": "SKIP"}.get(analysis.recommendation, "SKIP"),
        strategy_version=strategy_version, ai_tier="sonnet",
        reasoning_md=analysis.reason_md,
        tokens_in=r.tokens_in, tokens_out=r.tokens_out, cost_usd=r.cost_usd,
        market_snapshot={"setup_quality": analysis.setup_quality,
                         "positives": analysis.key_positives, "risks": analysis.key_risks},
    )
    return analysis


# ---- Tier 3 ----

def opus_decide(
    client: AIClient,
    shortlist: list[tuple[Candidate, SonnetAnalysis]],
    *, cash: float, n_open: int, max_positions: int, capacity: int,
    capital: float, mood: dict | None, strategy_version: str,
) -> list[BuyDecision]:
    """Final buy call. If AI unavailable, take top `capacity` of recommendation=consider_buy."""
    if capacity <= 0 or not shortlist:
        return []

    if not client.available:
        picks: list[BuyDecision] = []
        for cand, an in shortlist:
            if an.recommendation == "consider_buy" and len(picks) < capacity:
                picks.append(BuyDecision(
                    symbol=cand.symbol, confidence=0.5,
                    thesis_md=an.reason_md, time_horizon="2w",
                    catalysts=an.key_positives[:2],
                ))
        _log_decision(
            kind="decide",
            reasoning_md=f"rules-only buys: {[p.symbol for p in picks]}",
            strategy_version=strategy_version, ai_tier="rules", action="BUY",
        )
        return picks

    user = prompts.OPUS_DECIDE_USER_TMPL.format(
        cash=cash, n_open=n_open, max_positions=max_positions, capacity=capacity,
        mood_json=json.dumps(mood or {}),
        shortlist_json=json.dumps([
            {
                "symbol": c.symbol, "last_close": c.last_close,
                "technicals": {"adx": c.adx, "rsi": c.rsi, "atr_pct": c.atr_pct, "score": c.score},
                "analysis": asdict(a),
            }
            for c, a in shortlist
        ]),
    )
    r = client.message(
        model=OPUS,
        system=prompts.OPUS_DECIDE_SYSTEM.format(capital=capital, max_positions=max_positions),
        user=user, max_tokens=1200, temperature=0.2,
    )
    try:
        d = parse_json_object(
            r.text,
            required_keys=["buys", "skip_reason_for_others"],
        )
        buys_raw = d.get("buys", [])
        buys = [BuyDecision(**{k: b[k] for k in ("symbol", "confidence", "thesis_md", "time_horizon", "catalysts")}) for b in buys_raw][:capacity]
    except Exception as e:                       # noqa: BLE001
        _log_decision(
            kind="decide", action="SKIP",
            reasoning_md=f"opus parse failed: {e}; raw: {r.text[:300]}",
            strategy_version=strategy_version, ai_tier="opus",
            tokens_in=r.tokens_in, tokens_out=r.tokens_out, cost_usd=r.cost_usd,
        )
        return []

    _log_decision(
        kind="decide", action="BUY" if buys else "SKIP",
        reasoning_md=(
            f"opus buys: {[b.symbol for b in buys]}; "
            f"skip_reason: {d.get('skip_reason_for_others', '')}"
        ),
        strategy_version=strategy_version, ai_tier="opus",
        tokens_in=r.tokens_in, tokens_out=r.tokens_out, cost_usd=r.cost_usd,
    )
    return buys


# ---- Sell-side reasoning ----

def opus_sell_review(
    client: AIClient,
    *,
    symbol: str,
    qty: int,
    avg_price: float,
    opened_at: str,
    days_held: int,
    current_price: float,
    thesis_md: str,
    target_price: float | None,
    stop_price: float | None,
    time_horizon: str | None,
    technicals: dict,
    rules_flag: str,
    strategy_version: str,
) -> SellDecision:
    """Re-evaluate one open position with full thesis context.

    Called only when the rules layer has flagged the position for review
    (EMA breakdown, RSI breakdown, stop/target near, earnings approaching).
    Otherwise the orchestrator skips this — saves API cost.

    Without an API key: returns the rules verdict as a SellDecision.
    """
    unrealised = (current_price - avg_price) * qty
    unrealised_pct = (current_price - avg_price) / avg_price * 100 if avg_price else 0.0

    rules_action = "EXIT" if rules_flag.startswith("exit") else "HOLD"

    if not client.available:
        sd = SellDecision(
            action=rules_action, trim_pct=0.0, confidence=0.5,
            reason_md=f"rules-only ({rules_flag}); no AI vetting",
        )
        _log_decision(
            kind="sell_review", symbol=symbol, action=sd.action,
            strategy_version=strategy_version, ai_tier="rules",
            reasoning_md=sd.reason_md,
        )
        return sd

    with ledger.connect() as conn:
        hist = kb.history_for_symbol(conn, symbol, limit=8)
    hist_text = "\n".join(
        f"- {h['ts']} {h['kind']} {h['action'] or ''}: {h['reasoning_md'][:140]}"
        for h in hist
    ) or "  (none)"

    user = prompts.OPUS_SELL_USER_TMPL.format(
        symbol=symbol, opened_at=opened_at, avg_price=avg_price, qty=qty,
        days_held=days_held, current_price=current_price,
        unrealised=unrealised, unrealised_pct=unrealised_pct,
        thesis_md=thesis_md or "(no thesis recorded — bought before thesis logging)",
        target=f"₹{target_price:.2f}" if target_price else "(none)",
        stop=f"₹{stop_price:.2f}" if stop_price else "(none)",
        horizon=time_horizon or "(unset)",
        technicals_json=json.dumps(technicals),
        history=hist_text,
    )
    user += f"\n\nRules layer flagged this position: {rules_flag}"

    r = client.message(
        model=OPUS, system=prompts.OPUS_SELL_SYSTEM, user=user,
        max_tokens=600, temperature=0.2,
    )
    try:
        d = parse_json_object(
            r.text,
            required_keys=["action", "reason_md"],
        )
        sd = SellDecision(
            action=str(d.get("action", "HOLD")).upper(),
            trim_pct=float(d.get("trim_pct", 0.0) or 0.0),
            confidence=float(d.get("confidence", 0.5) or 0.5),
            reason_md=str(d.get("reason_md", "")),
        )
        if sd.action not in {"HOLD", "TRIM", "EXIT"}:
            sd.action = rules_action
    except Exception as e:                       # noqa: BLE001
        sd = SellDecision(
            action=rules_action, trim_pct=0.0, confidence=0.5,
            reason_md=f"opus parse failed ({e}); falling back to rules verdict {rules_action}",
        )

    _log_decision(
        kind="sell_review", symbol=symbol, action=sd.action,
        strategy_version=strategy_version, ai_tier="opus",
        reasoning_md=sd.reason_md,
        tokens_in=r.tokens_in, tokens_out=r.tokens_out, cost_usd=r.cost_usd,
    )
    return sd
