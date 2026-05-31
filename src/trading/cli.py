"""tt CLI — backtest, init-db, dashboard."""
from __future__ import annotations

from datetime import date, timedelta

import typer
from rich.console import Console
from rich.table import Table

from trading.backtest.runner import run_backtest
from trading.knowledge import ledger
from trading.knowledge.migrations import migrate as run_migrations
from trading.orchestrator import run_day
from trading.tax.fifo import fy_for, fy_summary

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def init_db():
    """Create the SQLite ledger if it doesn't exist."""
    p = ledger.init_db()
    v = run_migrations()
    console.print(f"[green]ledger initialised at {p} (schema v{v})")


@app.command("run-day")
def run_day_cmd(
    day_iso: str = typer.Option("", "--day", help="YYYY-MM-DD; default = yesterday"),
    universe: str = typer.Option("nifty500", help="nifty500 | watchlist"),
    universe_limit: int = typer.Option(0, help="Cap universe size for faster runs (0=all)"),
):
    """Run one full day end-to-end (screen → AI → broker)."""
    from datetime import date, timedelta
    d = date.fromisoformat(day_iso) if day_iso else date.today() - timedelta(days=1)
    r = run_day(d, universe_limit=(universe_limit or None), universe=universe)   # type: ignore[arg-type]

    tbl = Table(title=f"Run-day {d}")
    tbl.add_column("metric"); tbl.add_column("value")
    tbl.add_row("Universe size",       str(r.universe_size))
    tbl.add_row("After screen",        str(r.after_screen))
    tbl.add_row("Trend candidates (core)",      str(r.n_candidates))
    tbl.add_row("Momentum candidates (aggr.)",  str(r.n_aggressive_candidates))
    tbl.add_row("Haiku top picks",     ", ".join(r.haiku_picks) or "—")
    tbl.add_row("Sonnet recommends",   ", ".join(r.sonnet_recommends) or "—")
    tbl.add_row("Core buys",           ", ".join(r.opus_buys) or "—")
    tbl.add_row("Aggressive buys",     ", ".join(r.aggressive_buys) or "—")
    tbl.add_row("Sells",               ", ".join(r.sells) or "—")
    tbl.add_row("Fills",               str(r.fills))
    if r.errors:
        tbl.add_row("Errors",          "\n".join(r.errors))
    console.print(tbl)


@app.command("kb-search")
def kb_search(query: str):
    """FTS5 search across decisions, theses, journals."""
    from trading.knowledge import kb as kb_mod
    with ledger.connect() as conn:
        hits = kb_mod.search(conn, query, limit=15)
    if not hits:
        console.print("[yellow]no hits")
        return
    for h in hits:
        console.print(f"[cyan]{h.ts} {h.kind} {h.symbol or ''}[/cyan]")
        console.print(f"  {h.body[:300]}\n")


@app.command("universe-refresh")
def universe_refresh():
    """Force-refresh the Nifty 500 list."""
    from trading.data.universe import fetch_nifty500, load_nifty500
    fetch_nifty500(force=True)
    items = load_nifty500()
    console.print(f"[green]loaded {len(items)} symbols")


@app.command("brief")
def brief(day_iso: str = typer.Option("", "--day", help="YYYY-MM-DD; default today")):
    """Generate today's pre-market briefing without running the trading loop."""
    from datetime import date
    from trading.ai.client import AIClient
    from trading.intelligence.briefing import build_briefing
    from trading.settings import load_strategy
    d = date.fromisoformat(day_iso) if day_iso else date.today()
    cfg = load_strategy()
    b = build_briefing(AIClient(), day=d, strategy_version=cfg.version)
    tbl = Table(title=f"Briefing — {b.date}")
    tbl.add_column("field"); tbl.add_column("value")
    tbl.add_row("mood", b.mood)
    tbl.add_row("reason", b.mood_reason)
    tbl.add_row("watch today", ", ".join(b.watch_today) or "—")
    tbl.add_row("positions at risk", "\n".join(b.positions_at_risk) or "—")
    console.print(tbl)
    console.print("\n[bold]Body:[/bold]\n" + b.briefing_md)


@app.command("news-refresh")
def news_refresh():
    """Pull latest RSS feeds into the headlines store; prune past 7-day window."""
    from trading.intelligence import news as news_mod
    stats = news_mod.refresh_all()
    removed = news_mod.prune_old()
    tbl = Table(title="News refresh")
    tbl.add_column("source"); tbl.add_column("new rows", justify="right")
    for src, n in stats.items():
        tbl.add_row(src, str(n))
    tbl.add_row("[muted]pruned (>7d)[/muted]", str(removed))
    console.print(tbl)


@app.command("news")
def news_cmd(
    symbol: str = typer.Argument("", help="Symbol; empty = macro feed"),
    days: int = typer.Option(7, help="Lookback window"),
    limit: int = typer.Option(15, help="Max rows"),
):
    """Show news for a symbol or the macro top feed."""
    from trading.intelligence import news as news_mod
    if symbol:
        try:
            from trading.data.universe import load_nifty500
            cm = {r["symbol"]: r["company"] for r in load_nifty500()}
            company = cm.get(symbol.upper())
        except Exception:                   # noqa: BLE001
            company = None
        hits = news_mod.for_symbol(symbol.upper(), company=company, days=days, limit=limit)
        console.print(f"[cyan]{symbol.upper()} — last {days}d ({len(hits)} hits)[/cyan]")
    else:
        hits = news_mod.recent(days=days, limit=limit)
        console.print(f"[cyan]Macro — last {days}d ({len(hits)} hits)[/cyan]")
    if not hits:
        console.print("[yellow]no hits — try `tt news-refresh` first")
        return
    for h in hits:
        console.print(f"[muted]{h.published_iso[:16]}[/muted] [{h.source}] {h.title}")


@app.command("cues")
def cues_cmd():
    """Print current global cues (Dow, Nifty, VIX, crude, gold, USD/INR)."""
    from trading.intelligence.sentiment import fetch_cues
    tbl = Table(title="Global cues")
    tbl.add_column("name"); tbl.add_column("ticker"); tbl.add_column("close", justify="right"); tbl.add_column("1d %", justify="right")
    for c in fetch_cues():
        pct = f"{c.pct_1d:+.2f}%" if c.pct_1d is not None else "—"
        close = f"{c.close:,.2f}" if c.close is not None else "—"
        tbl.add_row(c.name, c.ticker, close, pct)
    console.print(tbl)


@app.command("tg-test")
def tg_test(text: str = typer.Argument("hello from tt", help="test message")):
    """Send a test message to Telegram."""
    from trading.alerts import telegram as tg
    if not tg._configured():
        console.print("[red]TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in .env")
        return
    if tg.notify(text):
        console.print("[green]sent")
    else:
        console.print("[red]API call failed — check token/chat id are correct")


@app.command("config-check")
def config_check():
    """Show whether all secrets/services are configured."""
    from trading.alerts import telegram as tg
    from trading.ai.client import AIClient
    from trading.settings import settings
    tbl = Table(title="Configuration")
    tbl.add_column("item"); tbl.add_column("status")
    tbl.add_row("ANTHROPIC_API_KEY", "[green]set" if settings.anthropic_api_key else "[red]MISSING")
    tbl.add_row("AIClient available", "[green]yes" if AIClient().available else "[red]no")
    tbl.add_row("TELEGRAM_BOT_TOKEN", "[green]set" if settings.telegram_bot_token else "[red]MISSING")
    tbl.add_row("TELEGRAM_CHAT_ID",   "[green]set" if settings.telegram_chat_id else "[red]MISSING")
    tbl.add_row("Telegram push", "[green]configured" if tg._configured() else "[red]disabled")
    tbl.add_row("Mode", settings.mode)
    tbl.add_row("Ledger DB", str(settings.ledger_path))
    console.print(tbl)


@app.command("halt")
def halt_cmd(reason: str = typer.Argument("manual halt")):
    """Pause all trading. Orchestrator will skip run-day until you `tt unhalt`."""
    from trading.alerts import telegram as tg
    tg.halt(reason)
    console.print(f"[yellow]HALTED — {reason}")


@app.command("unhalt")
def unhalt_cmd():
    """Resume trading."""
    from trading.alerts import telegram as tg
    tg.unhalt()
    console.print("[green]resumed")


@app.command("schedule")
def schedule_cmd(
    universe: str = typer.Option("nifty500", help="nifty500 | watchlist"),
    universe_limit: int = typer.Option(0, help="Cap for faster runs (0=all)"),
):
    """Run forever — pre-market brief at 08:30 IST, full pipeline at 09:30 IST."""
    from trading.scheduler.runner import run_forever
    run_forever(universe=universe, universe_limit=(universe_limit or None))


@app.command("watchlist")
def watchlist_cmd(days: int = typer.Option(14, help="WATCH lookback window")):
    """Show currently active watchlist (recently WATCH'd symbols)."""
    from trading.intelligence.watchlist import active_watches
    watches = active_watches(max_age_days=days, limit=50)
    tbl = Table(title=f"Active watchlist (last {days}d)")
    tbl.add_column("symbol"); tbl.add_column("last WATCH"); tbl.add_column("days ago", justify="right")
    tbl.add_column("reason excerpt", overflow="fold")
    if not watches:
        console.print("[yellow]watchlist empty — no recent WATCH decisions")
        return
    for w in watches:
        # CLI: truncate to keep terminal output tidy; dashboard shows full
        tbl.add_row(w.symbol, w.last_watch_ts[:10], str(w.days_ago), w.last_reason[:200])
    console.print(tbl)


@app.command("sync-prod")
def sync_prod(
    vps_ip: str = typer.Option("65.0.55.227", help="VPS public IP"),
    key: str = typer.Option("~/.ssh/lightsail-mumbai.pem", help="SSH private key path"),
    user: str = typer.Option("ubuntu", help="SSH user on VPS"),
    snapshot_dir: str = typer.Option("~/Backups", help="Where to keep timestamped snapshots"),
    launch_dashboard: bool = typer.Option(
        False, "--dashboard", "-d",
        help="After syncing, launch the dashboard and open it in your browser",
    ),
    host: str = typer.Option("127.0.0.1", help="Dashboard host (used with --dashboard)"),
    port: int = typer.Option(8000, help="Dashboard port (used with --dashboard)"),
):
    """One-shot: scp the prod ledger.db from the VPS to local data/ledger.db.

    Also backs up the existing local db first, then saves a timestamped
    snapshot in ~/Backups so you have a recovery point. Safe to run anytime.

    Add --dashboard (-d) to also launch the dashboard and pop your browser
    open right after syncing — useful for "pull and browse" in one step.
    """
    import subprocess
    from datetime import datetime
    from pathlib import Path
    from shutil import copy2

    local = Path("data/ledger.db").resolve()
    local.parent.mkdir(parents=True, exist_ok=True)
    snap_dir = Path(snapshot_dir).expanduser()
    snap_dir.mkdir(parents=True, exist_ok=True)
    expanded_key = str(Path(key).expanduser())

    # 1. Backup existing local first
    if local.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = snap_dir / f"ledger-localmac-before-sync-{ts}.db"
        copy2(local, backup)
        console.print(f"[muted]backed up existing local → {backup}[/muted]")

    # 2. SCP from VPS
    src = f"{user}@{vps_ip}:/opt/trading-terminal/data/ledger.db"
    console.print(f"[cyan]fetching {src} → {local}[/cyan]")
    r = subprocess.run(
        ["scp", "-i", expanded_key, "-o", "StrictHostKeyChecking=accept-new", src, str(local)],
        check=False,
    )
    if r.returncode != 0:
        console.print(f"[red]scp failed (exit {r.returncode}). check key path, IP, and VPS reachability.")
        raise typer.Exit(1)

    # 3. Timestamped snapshot (one per day, overwrites if rerun same day)
    snap = snap_dir / f"ledger-prod-{datetime.now().strftime('%Y%m%d')}.db"
    copy2(local, snap)
    size_kb = local.stat().st_size / 1024
    console.print(f"[green]synced ({size_kb:.1f} KB). daily snapshot: {snap}")

    # 4. Optional: launch dashboard + open browser
    if launch_dashboard:
        import threading
        import time
        import webbrowser
        import uvicorn

        url = f"http://{host}:{port}"
        console.print(f"[cyan]launching dashboard at {url} — Ctrl+C to stop[/cyan]")

        # Open the browser shortly after uvicorn starts so the page actually loads
        def _open_after_delay():
            time.sleep(1.5)
            webbrowser.open(url)

        threading.Thread(target=_open_after_delay, daemon=True).start()
        uvicorn.run("trading.dashboard.app:app", host=host, port=port, reload=False, log_level="warning")


@app.command("sleeves")
def sleeves_cmd():
    """Show core vs aggressive sleeve allocation + current usage."""
    from trading.settings import load_strategy
    cfg = load_strategy()
    with ledger.connect() as conn:
        positions = list(conn.execute(
            "SELECT symbol, qty, avg_price, sleeve FROM positions WHERE status='open'"
        ))
    if not cfg.sleeves:
        console.print("[yellow]No sleeves configured in strategy.yaml")
        return
    tbl = Table(title="Sleeve allocation (paper)")
    tbl.add_column("sleeve"); tbl.add_column("cap %", justify="right")
    tbl.add_column("cap INR", justify="right"); tbl.add_column("used INR", justify="right")
    tbl.add_column("positions", justify="right"); tbl.add_column("max", justify="right")
    for name, sl in cfg.sleeves.items():
        cap = cfg.portfolio.capital * sl.capital_pct
        used = sum(p["qty"] * p["avg_price"] for p in positions if (p["sleeve"] or "core") == name)
        n_open = sum(1 for p in positions if (p["sleeve"] or "core") == name)
        tbl.add_row(name, f"{sl.capital_pct*100:.0f}%", f"₹{cap:,.0f}",
                    f"₹{used:,.0f}", str(n_open), str(sl.max_positions))
    console.print(tbl)


@app.command("ai-cost")
def ai_cost():
    """Show Claude API spend per tier."""
    with ledger.connect() as conn:
        rows = list(conn.execute(
            "SELECT ai_tier, COUNT(*) n, SUM(tokens_in) ti, SUM(tokens_out) to_, SUM(cost_usd) cost "
            "FROM decisions WHERE ai_tier IS NOT NULL GROUP BY ai_tier ORDER BY cost DESC"
        ))
    tbl = Table(title="Claude API spend by tier")
    tbl.add_column("tier"); tbl.add_column("calls", justify="right")
    tbl.add_column("in", justify="right"); tbl.add_column("out", justify="right")
    tbl.add_column("USD", justify="right")
    total = 0.0
    for r in rows:
        c = r["cost"] or 0.0; total += c
        tbl.add_row(r["ai_tier"], str(r["n"]), f"{r['ti'] or 0:,}", f"{r['to_'] or 0:,}", f"${c:.4f}")
    tbl.add_row("[bold]TOTAL[/bold]", "", "", "", f"[bold]${total:.4f}[/bold]")
    console.print(tbl)


@app.command()
def backtest(
    days: int = typer.Option(180, help="Days of history to backtest"),
    end_iso: str = typer.Option("", help="End date YYYY-MM-DD; default today"),
):
    """Run the trivial EMA backtest on the watchlist."""
    end = date.fromisoformat(end_iso) if end_iso else date.today()
    start = end - timedelta(days=days)
    r = run_backtest(start, end)

    tbl = Table(title=f"Backtest {start} → {end}")
    tbl.add_column("metric"); tbl.add_column("value", justify="right")
    tbl.add_row("Starting cash",       f"₹{r.starting_cash:,.2f}")
    tbl.add_row("Ending cash",         f"₹{r.ending_cash:,.2f}")
    tbl.add_row("Open position value", f"₹{r.open_position_value:,.2f}")
    tbl.add_row("Total value",         f"₹{r.total_value:,.2f}")
    tbl.add_row("Net return",          f"{(r.total_value/r.starting_cash - 1)*100:+.2f}%")
    tbl.add_row("# trades",            str(r.n_trades))
    console.print(tbl)


@app.command()
def tax(fy: str = typer.Option("", help="e.g. FY2026-27; default current FY")):
    """Show tax summary for the given financial year."""
    fy = fy or fy_for(date.today())
    with ledger.connect() as conn:
        s = fy_summary(conn, fy)

    tbl = Table(title=f"Tax sheet — {fy}")
    tbl.add_column("metric"); tbl.add_column("value", justify="right")
    tbl.add_row("STCG (gain)",            f"₹{s.stcg_gain:,.2f}")
    tbl.add_row("LTCG (gain)",            f"₹{s.ltcg_gain:,.2f}")
    tbl.add_row("LTCG exempt (₹1.25L)",   f"₹{s.ltcg_exempt:,.2f}")
    tbl.add_row("LTCG taxable",           f"₹{s.ltcg_taxable:,.2f}")
    tbl.add_row("STCG tax (20%)",         f"₹{s.stcg_tax:,.2f}")
    tbl.add_row("LTCG tax (12.5%)",       f"₹{s.ltcg_tax:,.2f}")
    tbl.add_row("Total tax",              f"₹{s.total_tax:,.2f}")
    tbl.add_row("Net realised AFTER tax", f"₹{s.net_realised_after_tax:,.2f}")
    console.print(tbl)


@app.command()
def dashboard(host: str = "127.0.0.1", port: int = 8000):
    """Launch the read-only dashboard."""
    import uvicorn
    uvicorn.run("trading.dashboard.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
