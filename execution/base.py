"""Gemeinsames Broker-Interface fuer Paper und Live.

Paper- und Live-Adapter implementieren dasselbe Interface; unterschiedlich sind nur
Endpoint und Keys. Der Wechsel erfolgt AUSSCHLIESSLICH ueber `execution.gate`, nie
durch die Anwendungslogik selbst.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal, Optional

from core.types import Order, Position


class BrokerAdapter(ABC):
    mode: Literal["paper", "live"]
    market: str
    name: str = "broker"

    @abstractmethod
    def place_order(
        self, symbol: str, side: Literal["buy", "sell"], qty: float,
        sl: Optional[float] = None, tp: Optional[float] = None,
        strategy: str = "", **meta,
    ) -> Order: ...

    @abstractmethod
    def close_position(self, symbol: str, price: Optional[float] = None,
                       strategy: str = "") -> None: ...

    @abstractmethod
    def get_positions(self, strategy: Optional[str] = None) -> list[Position]: ...

    @abstractmethod
    def get_account_balance(self) -> float: ...

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.__class__.__name__} market={self.market} mode={self.mode}>"
