from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    mode: str = "paper"
    ledger_db: str = "data/ledger.db"
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000

    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_access_token: str = ""

    anthropic_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    @property
    def ledger_path(self) -> Path:
        p = ROOT / self.ledger_db
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


class PortfolioCfg(BaseModel):
    capital: float
    max_positions: int
    max_pct_per_position: float
    reserve_cash_pct: float


class RiskCfg(BaseModel):
    default_stop_loss_pct: float
    default_target_r_multiple: float
    daily_loss_halt_pct: float
    weekly_loss_halt_pct: float
    no_average_down: bool


class StrategyCfg(BaseModel):
    version: str
    portfolio: PortfolioCfg
    risk: RiskCfg
    universe: dict
    filters: dict
    signals: dict


def load_strategy() -> StrategyCfg:
    with open(ROOT / "config" / "strategy.yaml") as f:
        return StrategyCfg(**yaml.safe_load(f))


def load_watchlist() -> list[str]:
    with open(ROOT / "config" / "watchlist.yaml") as f:
        return yaml.safe_load(f)["symbols"]


settings = Settings()
