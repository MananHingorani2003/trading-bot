"""Signal poll orchestrator — fetch → parse → execute → record.

Called by:
  - the scheduler (every 30 min during market hours), and
  - `tt signal-poll` for manual testing.

Fully transactional in the sense that each message is recorded in
signal_messages before execution, so even if execution crashes we know
we saw the message and won't double-act on it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from trading.broker.paper import PaperBroker
from trading.calendar import why_closed
from trading.data.prices import fetch_daily
from trading.intelligence.signals import listener
from trading.knowledge import ledger
from trading.settings import load_strategy


@dataclass
class PollReport:
    fetched: int = 0
    new: int = 0
    buys_filled: int = 0
    sells_filled: int = 0
    skipped: int = 0
    rejected: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def _current_price(symbol: str) -> float | None:
    """Best-effort last close — cached parquet first."""
    end = date.today()
    start = end - timedelta(days=10)
    try:
        df = fetch_daily(symbol, start, end)
        if df is None or df.empty:
            return None
        return float(df["Close"].dropna().iloc[-1])
    except Exception:                              # noqa: BLE001
        return None


def _broker_with_current_cash(cfg) -> PaperBroker:
    """Reconstruct cash exactly like orchestrator.run_day."""
    broker = PaperBroker(starting_cash=cfg.portfolio.capital)
    with ledger.connect() as conn:
        invested = conn.execute(
            "SELECT COALESCE(SUM(qty*avg_price),0) FROM positions WHERE status='open'"
        ).fetchone()[0] or 0.0
    broker._cash = max(0.0, cfg.portfolio.capital - invested)
    return broker


def poll(source_name: str = "lemonnhq") -> PollReport:
    """Fetch recent signals from the source and act on new ones.

    Skips entirely on weekends / NSE holidays since orders won't fill."""
    report = PollReport()
    today = date.today()
    if why_closed(today):
        report.errors.append(f"market closed: {why_closed(today)}")
        return report

    cfg = load_strategy()
    src_cfg = (cfg.signal_sources or {}).get(source_name) or {}
    if not src_cfg.get("enabled", True):
        report.errors.append(f"{source_name} disabled in config")
        return report

    channel = src_cfg.get("channel", source_name)
    max_age = int(src_cfg.get("max_age_minutes", 720))
    messages = listener.fetch_recent(channel, since_minutes=max_age, limit=30)
    report.fetched = len(messages)
    if not messages:
        return report

    from trading.strategy.signal_follower import execute_buy, execute_update
    broker = _broker_with_current_cash(cfg)

    for msg in messages:
        if listener.already_seen(source_name, msg.channel_msg_id):
            continue
        report.new += 1
        action: str | None = None
        position_id: int | None = None

        try:
            if msg.parsed.kind == "buy" and msg.parsed.buy:
                price = _current_price(msg.parsed.buy.symbol)
                if price is None:
                    report.skipped += 1
                    action = "skipped"
                else:
                    r = execute_buy(msg.parsed.buy, current_price=price, cfg=cfg, broker=broker, source=source_name)
                    action = r.action
                    position_id = r.position_id
                    if r.action == "bought":
                        report.buys_filled += 1
                    else:
                        report.skipped += 1

            elif msg.parsed.kind == "update" and msg.parsed.update:
                price = _current_price(msg.parsed.update.symbol) or msg.parsed.update.exit_price
                r = execute_update(msg.parsed.update, current_price=price, cfg=cfg, broker=broker, source=source_name)
                action = r.action
                position_id = r.position_id
                if r.action == "sold":
                    report.sells_filled += 1
                else:
                    report.skipped += 1

            elif msg.parsed.kind == "rejected":
                action = "rejected"
                report.rejected += 1
            else:
                action = "parse_failed"
                report.skipped += 1
        except Exception as e:                      # noqa: BLE001
            action = f"error: {e}"
            report.errors.append(f"{msg.parsed.symbol or '?'}: {e}")
        finally:
            try:
                listener.record_message(
                    source=source_name,
                    channel_msg_id=msg.channel_msg_id,
                    received_at=msg.received_at,
                    text=msg.text,
                    parsed=msg.parsed,
                    action=action,
                    position_id=position_id,
                )
            except Exception as e:                  # noqa: BLE001
                report.errors.append(f"record failed: {e}")

    return report
