"""Broker interface. PaperBroker and (later) KiteBroker implement this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

Side = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT"]
Product = Literal["CNC", "MIS"]   # delivery vs intraday — v1 only CNC


@dataclass
class Order:
    symbol: str
    side: Side
    qty: int
    order_type: OrderType = "MARKET"
    limit_price: float | None = None
    product: Product = "CNC"
    client_order_id: str | None = None     # for idempotency
    decision_id: int | None = None


@dataclass
class Fill:
    order_id: str
    symbol: str
    side: Side
    qty: int
    price: float
    ts: str                                  # ISO
    trade_id: int                            # ledger row id


@dataclass
class Position:
    symbol: str
    qty: int
    avg_price: float


class Broker(ABC):
    @abstractmethod
    def place_order(self, order: Order, *, ref_price: float, ts: str) -> Fill: ...

    @abstractmethod
    def positions(self) -> list[Position]: ...

    @abstractmethod
    def cash(self) -> float: ...
