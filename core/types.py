"""Gemeinsame Datentypen fuer Strategien, Backtest und Execution."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal, Optional


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"

    @property
    def sign(self) -> int:
        return {Direction.LONG: 1, Direction.SHORT: -1, Direction.FLAT: 0}[self]


@dataclass(frozen=True)
class Signal:
    """Ein Strategie-Signal fuer genau einen Zeitpunkt."""

    direction: Direction
    confidence: float = 0.0  # 0.0 .. 1.0
    reason: str = ""
    timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence muss in [0,1] liegen, war {self.confidence}")


@dataclass
class Trade:
    """Ein abgeschlossener (oder offener) Trade im Backtest/Paper-Betrieb."""

    strategy: str
    market: str
    symbol: str
    timeframe: str
    direction: Direction
    entry_time: datetime
    entry_price: float
    qty: float
    stop_loss: float
    take_profit: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""
    reason: str = ""
    fees: float = 0.0

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    @property
    def pnl(self) -> float:
        if self.exit_price is None:
            return 0.0
        return (self.exit_price - self.entry_price) * self.direction.sign * self.qty - self.fees

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry_price - self.stop_loss)

    @property
    def r_multiple(self) -> float:
        """PnL gemessen in Vielfachen des initial riskierten Betrags."""
        risk = self.risk_per_unit * self.qty
        if risk <= 0 or self.exit_price is None:
            return 0.0
        return self.pnl / risk


@dataclass
class Order:
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    client_id: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class Position:
    symbol: str
    qty: float          # vorzeichenbehaftet: >0 long, <0 short
    avg_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    # Welche Strategie diese Position haelt. Leerstring = anonym/unbekannt (Altverhalten).
    # WICHTIG: Positionen werden pro (strategy, symbol) getrackt, NICHT pro Symbol allein -
    # sonst schliessen sich mehrere gleichzeitig aktive Strategien auf demselben Symbol
    # gegenseitig die Positionen (siehe execution/paper.py).
    strategy: str = ""
    entry_time: Optional[datetime] = None
