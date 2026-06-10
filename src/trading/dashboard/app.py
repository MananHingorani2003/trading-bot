"""FastAPI dashboard. Read-only views over the ledger.

Routes:
  /              — overview (cash, open positions value, realised P&L FYTD, tax due)
  /positions     — currently open positions with unrealised P&L (post buy-side cost)
  /trades        — every fill with full cost breakdown
  /realised      — closed lots, net P&L AFTER all charges (the only number that matters)
  /tax           — current-FY tax sheet (STCG/LTCG, exemption, tax due)
  /decisions     — every reasoning event the bot logged
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from trading.knowledge import ledger
from trading.tax.fifo import fy_for, fy_summary

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="Trading Terminal")


def _q(conn, sql, params: tuple = ()):
    """Tiny SQL helper. `params` is a tuple/list of bind values, e.g. (fy,)."""
    return [dict(r) for r in conn.execute(sql, params)]


@app.get("/", response_class=HTMLResponse)
def overview(request: Request):
    fy = fy_for(date.today())
    with ledger.connect() as conn:
        positions = _q(conn, "SELECT * FROM positions WHERE status='open'")
        n_trades = conn.execute("SELECT COUNT(*) c FROM trades").fetchone()["c"]
        n_decisions = conn.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"]
        realised_fytd = conn.execute(
            "SELECT COALESCE(SUM(realised_pnl),0) p FROM tax_realisations WHERE fy=?", (fy,),
        ).fetchone()["p"]
        tax = fy_summary(conn, fy)
    return TEMPLATES.TemplateResponse(request, "overview.html", {
        "positions": positions,
        "n_trades": n_trades,
        "n_decisions": n_decisions,
        "realised_fytd": realised_fytd,
        "tax": tax,
        "fy": fy,
    })


@app.get("/positions", response_class=HTMLResponse)
def positions(request: Request):
    with ledger.connect() as conn:
        rows = _q(conn, "SELECT * FROM positions WHERE status='open' ORDER BY opened_at DESC")
    return TEMPLATES.TemplateResponse(request, "positions.html", {"positions": rows})


@app.get("/trades", response_class=HTMLResponse)
def trades(request: Request):
    with ledger.connect() as conn:
        rows = _q(conn, "SELECT * FROM trades ORDER BY ts DESC LIMIT 500")
    return TEMPLATES.TemplateResponse(request, "trades.html", {"trades": rows})


@app.get("/realised", response_class=HTMLResponse)
def realised(request: Request):
    """The numbers that actually matter — net P&L per closed lot, after every charge."""
    with ledger.connect() as conn:
        rows = _q(conn, """
            SELECT r.*,
                   (julianday(r.sell_date) - julianday(r.buy_date)) AS days
            FROM tax_realisations r
            ORDER BY r.sell_date DESC, r.id DESC
            LIMIT 500
        """)
        agg = conn.execute("""
            SELECT COALESCE(SUM(realised_pnl),0) total,
                   COALESCE(SUM(CASE WHEN realised_pnl > 0 THEN 1 ELSE 0 END),0) wins,
                   COALESCE(SUM(CASE WHEN realised_pnl <= 0 THEN 1 ELSE 0 END),0) losses
            FROM tax_realisations
        """).fetchone()
    return TEMPLATES.TemplateResponse(request, "realised.html", {
        "rows": rows, "agg": dict(agg),
    })


@app.get("/tax", response_class=HTMLResponse)
def tax(request: Request, fy: str | None = None):
    fy = fy or fy_for(date.today())
    with ledger.connect() as conn:
        s = fy_summary(conn, fy)
        per_lot = _q(conn,
            "SELECT * FROM tax_realisations WHERE fy=? ORDER BY sell_date DESC", (fy,),
        )
    return TEMPLATES.TemplateResponse(request, "tax.html", {
        "summary": s, "lots": per_lot, "fy": fy,
    })


@app.get("/decisions", response_class=HTMLResponse)
def decisions(request: Request):
    with ledger.connect() as conn:
        rows = _q(conn, "SELECT * FROM decisions ORDER BY ts DESC LIMIT 500")
    return TEMPLATES.TemplateResponse(request, "decisions.html", {"decisions": rows})


@app.get("/signals", response_class=HTMLResponse)
def signals_view(request: Request):
    with ledger.connect() as conn:
        rows = _q(conn, """
            SELECT received_at, source, kind, symbol, action_taken, message_text, position_id
            FROM signal_messages ORDER BY id DESC LIMIT 100
        """)
        agg = conn.execute(
            "SELECT COUNT(*) total, "
            "SUM(CASE WHEN action_taken='bought' THEN 1 ELSE 0 END) bought, "
            "SUM(CASE WHEN action_taken='sold' THEN 1 ELSE 0 END) sold "
            "FROM signal_messages"
        ).fetchone()
    return TEMPLATES.TemplateResponse(request, "signals.html", {
        "rows": rows, "agg": dict(agg) if agg else {},
    })


@app.get("/watchlist", response_class=HTMLResponse)
def watchlist_view(request: Request, days: int = 14):
    from trading.intelligence.watchlist import active_watches
    rows = active_watches(max_age_days=days, limit=50)
    return TEMPLATES.TemplateResponse(request, "watchlist.html", {
        "rows": rows, "days": days,
    })


@app.get("/news", response_class=HTMLResponse)
def news_view(request: Request, symbol: str | None = None, days: int = 7):
    from trading.intelligence import news as news_mod
    company = None
    if symbol:
        try:
            from trading.data.universe import load_nifty500
            cm = {r["symbol"]: r["company"] for r in load_nifty500()}
            company = cm.get(symbol.upper())
        except Exception:                   # noqa: BLE001
            pass
        rows = news_mod.for_symbol(symbol.upper(), company=company, days=days, limit=100)
    else:
        rows = news_mod.recent(days=days, limit=100)
    return TEMPLATES.TemplateResponse(request, "news.html", {
        "rows": rows, "symbol": symbol, "days": days,
    })


@app.get("/briefings", response_class=HTMLResponse)
def briefings(request: Request):
    with ledger.connect() as conn:
        rows = _q(conn, "SELECT date, premarket, notes FROM journals ORDER BY date DESC LIMIT 60")
    return TEMPLATES.TemplateResponse(request, "briefings.html", {"rows": rows})


@app.get("/thesis/{symbol}", response_class=HTMLResponse)
def thesis(request: Request, symbol: str):
    from trading.knowledge import kb as kb_mod
    with ledger.connect() as conn:
        pos = kb_mod.open_position_with_thesis(conn, symbol.upper())
        hist = kb_mod.history_for_symbol(conn, symbol.upper(), limit=30)
    return TEMPLATES.TemplateResponse(request, "thesis.html", {
        "symbol": symbol.upper(), "pos": pos, "history": hist,
    })
