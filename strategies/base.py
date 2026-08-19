"""Strategie-Interface (Plug-in-Prinzip).

Jede Strategie implementiert `compute()` vektorisiert ueber den gesamten DataFrame.
`generate_signal()` (das im Auftrag geforderte Interface) ist daraus abgeleitet und
liefert das Signal fuer die letzte Kerze - so kann Backtest und Live-Loop denselben
Code nutzen, ohne dass Execution- oder UI-Code angefasst werden muss.

WICHTIG (Look-ahead-Vermeidung): `compute()` darf pro Zeile nur Daten bis
einschliesslich dieser Zeile verwenden. Die Backtest-Engine handelt das Signal von
Bar t erst zum Open von Bar t+1.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from core.types import Direction, Signal

REQUIRED_COLUMNS = ("open", "high", "low", "close", "volume")


class Strategy(ABC):
    name: str = "unnamed"
    category: str = "generic"
    markets: list[str] = ["crypto", "stocks", "forex"]
    timeframes: list[str] = ["15m", "1h", "4h", "1d"]
    description: str = ""

    def __init__(self, **params: Any) -> None:
        merged = dict(self.default_params())
        unknown = set(params) - set(merged)
        if unknown:
            raise ValueError(f"{self.name}: unbekannte Parameter {sorted(unknown)}")
        merged.update(params)
        self.params = merged

    # ---- von Subklassen zu implementieren -------------------------------
    @staticmethod
    @abstractmethod
    def default_params() -> dict[str, Any]:
        """Default-Parameter; gleichzeitig die Whitelist erlaubter Parameter."""

    @abstractmethod
    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """Gibt DataFrame mit Spalten 'direction' (int -1/0/1), 'confidence'
        (float 0..1) und 'reason' (str) zurueck, indexgleich zu `ohlcv`."""

    # ---- gemeinsame Infrastruktur ---------------------------------------
    def generate_signal(self, ohlcv: pd.DataFrame) -> Signal:
        """LONG / SHORT / FLAT + Konfidenz fuer die zuletzt geschlossene Kerze."""
        self.validate(ohlcv)
        frame = self.compute(ohlcv)
        if frame.empty:
            return Signal(Direction.FLAT, 0.0, "keine Daten")
        row = frame.iloc[-1]
        direction = {1: Direction.LONG, -1: Direction.SHORT, 0: Direction.FLAT}[int(row["direction"])]
        return Signal(
            direction=direction,
            confidence=float(row["confidence"]),
            reason=str(row.get("reason", "")),
            timestamp=ohlcv.index[-1].to_pydatetime(),
        )

    @staticmethod
    def validate(ohlcv: pd.DataFrame) -> None:
        missing = [c for c in REQUIRED_COLUMNS if c not in ohlcv.columns]
        if missing:
            raise ValueError(f"OHLCV fehlen Spalten: {missing}")
        if not isinstance(ohlcv.index, pd.DatetimeIndex):
            raise ValueError("OHLCV braucht einen DatetimeIndex")
        if not ohlcv.index.is_monotonic_increasing:
            raise ValueError("OHLCV muss chronologisch sortiert sein")

    def empty_frame(self, index: pd.Index) -> pd.DataFrame:
        return pd.DataFrame(
            {"direction": 0, "confidence": 0.0, "reason": ""},
            index=index,
        )

    def supports(self, market: str, timeframe: str) -> bool:
        return market in self.markets and timeframe in self.timeframes

    @property
    def key(self) -> str:
        return self.name

    def __repr__(self) -> str:  # pragma: no cover - nur Debug-Ausgabe
        return f"{self.__class__.__name__}({self.params})"
