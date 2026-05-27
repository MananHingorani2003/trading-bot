"""NSE trading calendar — weekend + holiday check."""
from __future__ import annotations

from datetime import date
from functools import lru_cache

import yaml

from trading.settings import ROOT

HOLIDAYS_PATH = ROOT / "config" / "holidays.yaml"


@lru_cache(maxsize=1)
def _holidays() -> dict[date, str]:
    if not HOLIDAYS_PATH.exists():
        return {}
    raw = yaml.safe_load(HOLIDAYS_PATH.read_text()) or {}
    return {
        date.fromisoformat(h["date"]): h.get("reason", "holiday")
        for h in (raw.get("holidays") or [])
    }


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5    # 5=Sat, 6=Sun


def holiday_reason(d: date) -> str | None:
    return _holidays().get(d)


def is_trading_day(d: date) -> bool:
    return not is_weekend(d) and holiday_reason(d) is None


def why_closed(d: date) -> str | None:
    if is_weekend(d):
        return "weekend"
    r = holiday_reason(d)
    if r:
        return f"NSE holiday: {r}"
    return None
