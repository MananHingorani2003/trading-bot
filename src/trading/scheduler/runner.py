"""APScheduler — runs the trading loop unattended on IST market hours.

Jobs (all Mon–Fri, Asia/Kolkata):
  08:30  Pre-market briefing only (uses overnight news)
  09:30  Full run_day — orchestrator pipeline + place orders
  15:35  EOD summary (already pushed by run_day; this is a safety re-send if 09:30 was skipped)

We deliberately do NOT trade in the first 15 min after open (extra volatility).
NSE holiday handling is best-effort: if `bars` for the day are empty we just
log and skip. APScheduler's `day_of_week='mon-fri'` covers weekends.

Run with: `uv run tt schedule` (foreground). On a VPS, wrap in systemd.
"""
from __future__ import annotations

import signal
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from trading.ai.client import AIClient
from trading.alerts import telegram as tg
from trading.calendar import why_closed
from trading.intelligence.briefing import build_briefing
from trading.orchestrator import run_day
from trading.settings import load_strategy

IST = ZoneInfo("Asia/Kolkata")


def _today_ist() -> date:
    return datetime.now(IST).date()


def job_briefing() -> None:
    today = _today_ist()
    closed = why_closed(today)
    if closed:
        print(f"[brief] skipping {today} — {closed}")
        tg.notify(
            f"😴 *{today.isoformat()} — markets closed*\n"
            f"_{closed}_\n\n"
            f"Bot is resting today. No briefing, no trades. "
            f"Next run-day will be the next trading session."
        )
        return
    try:
        cfg = load_strategy()
        b = build_briefing(AIClient(), day=today, strategy_version=cfg.version)
        print(f"[brief] {b.date} mood={b.mood}")
    except Exception as e:                  # noqa: BLE001
        print(f"[brief] FAILED: {e}")
        tg.notify_error("scheduled briefing", str(e))


def job_run_day(*, universe: str = "nifty500", universe_limit: int | None = None) -> None:
    today = _today_ist()
    closed = why_closed(today)
    if closed:
        # Briefing job already sent the holiday note at 08:30; silent here.
        print(f"[run-day] skipping {today} — {closed}")
        return
    try:
        # run_day already does its own briefing; pass skip_briefing if you ran the 08:30 job
        r = run_day(today, universe=universe, universe_limit=universe_limit, skip_briefing=True)   # type: ignore[arg-type]
        print(f"[run-day] fills={r.fills} buys={r.opus_buys} sells={r.sells}")
    except Exception as e:                  # noqa: BLE001
        print(f"[run-day] FAILED: {e}")
        tg.notify_error("scheduled run-day", str(e))


def build_scheduler(*, universe: str = "nifty500", universe_limit: int | None = None) -> BlockingScheduler:
    sched = BlockingScheduler(timezone=IST)
    # 08:30 IST — briefing
    sched.add_job(job_briefing, CronTrigger(day_of_week="mon-fri", hour=8, minute=30, timezone=IST),
                  id="briefing", max_instances=1, coalesce=True, misfire_grace_time=600)
    # 09:30 IST — full pipeline (15 min after open, avoids opening volatility)
    sched.add_job(
        lambda: job_run_day(universe=universe, universe_limit=universe_limit),
        CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=IST),
        id="run_day", max_instances=1, coalesce=True, misfire_grace_time=1800,
    )
    return sched


def run_forever(*, universe: str = "nifty500", universe_limit: int | None = None) -> None:
    sched = build_scheduler(universe=universe, universe_limit=universe_limit)
    tg.notify(f"⏰ *Scheduler started* — universe={universe}, limit={universe_limit or 'all'}")
    print("scheduler started; next runs:")
    for job in sched.get_jobs():
        next_t = job.trigger.get_next_fire_time(None, datetime.now(IST))
        print(f"  {job.id}: {next_t}")
    # Graceful shutdown
    def _stop(signum, frame):                # noqa: ARG001
        print("scheduler stopping...")
        tg.notify("⏹ Scheduler stopped")
        sched.shutdown(wait=False)
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        pass
