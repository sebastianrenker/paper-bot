"""Risikomanagement, Circuit Breaker und Kill-Switch.

Pflichtmodul: jede Order laeuft durch `check_order()`. Verstoesst sie gegen ein
Limit, wird sie abgelehnt - es gibt keinen Bypass-Pfad. Der Circuit Breaker
schaltet den Betriebsmodus bei Ueberschreiten des Tagesverlustlimits automatisch
auf PAPER zurueck; ein Zurueckschalten auf LIVE ist nur manuell moeglich.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Callable, Optional

log = logging.getLogger(__name__)


class Mode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


@dataclass
class RiskLimits:
    risk_per_trade: float = 0.01          # Anteil des Kapitals pro Trade
    max_open_positions: int = 3
    max_daily_loss: float = 0.03          # 3 % Tagesverlust -> Circuit Breaker
    max_total_drawdown: float = 0.15      # 15 % Gesamtdrawdown -> Kill-Switch
    max_position_notional_pct: float = 0.25
    require_stop_loss: bool = True
    # Mindest-Stop-Abstand als Anteil des Einstiegspreises (Bugfix Stop-Loss-Loop).
    # Ist der berechnete Stop naeher als das am Einstiegspreis, wird die Order ABGELEHNT
    # (nicht der Stop aufgeweitet - Begruendung siehe AUDIT_UND_RECHERCHE.md). 0.25 %
    # liegt klar ueber dem Round-Trip-Kostenband (Gebuehr ~0.06 % + Slippage ~0.05 % je
    # Seite), sodass ein Stop nicht faktisch auf dem Einstieg/innerhalb der Kosten liegt.
    min_stop_pct: float = 0.0025

    def __post_init__(self) -> None:
        if not 0 < self.risk_per_trade <= 0.05:
            raise ValueError("risk_per_trade muss in (0, 0.05] liegen - mehr als 5 % pro Trade ist unvertretbar.")
        if not 0 < self.max_daily_loss <= 0.5:
            raise ValueError("max_daily_loss muss in (0, 0.5] liegen.")
        if self.max_open_positions < 1:
            raise ValueError("max_open_positions muss >= 1 sein.")
        if not 0 <= self.min_stop_pct < 0.5:
            raise ValueError("min_stop_pct muss in [0, 0.5) liegen.")


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""
    adjusted_qty: Optional[float] = None


@dataclass
class RiskState:
    day: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    day_start_equity: float = 0.0
    peak_equity: float = 0.0
    realized_today: float = 0.0
    tripped: bool = False
    trip_reason: str = ""


class RiskManager:
    """Zentrale Risikokontrolle. `on_mode_change` wird aufgerufen, wenn der
    Circuit Breaker den Modus zurueckschaltet (z. B. um den Broker-Adapter zu tauschen).
    `on_trip`/`on_reset` erlauben es Aufrufern (z. B. cli.py), den Trip-Zustand
    dauerhaft zu persistieren (siehe Bugfix-Hinweis unten)."""

    def __init__(
        self,
        limits: RiskLimits,
        initial_equity: float,
        mode: Mode = Mode.PAPER,
        on_mode_change: Callable[[Mode, str], None] | None = None,
        on_trip: Callable[[str], None] | None = None,
        on_reset: Callable[[], None] | None = None,
    ) -> None:
        self.limits = limits
        self.mode = mode
        self.equity = initial_equity
        self.state = RiskState(day_start_equity=initial_equity, peak_equity=initial_equity)
        self.on_mode_change = on_mode_change
        self.on_trip = on_trip
        self.on_reset = on_reset
        self.audit: list[dict] = []

    # ---- Kapital-/Tagesbuchhaltung ---------------------------------------
    def update_equity(self, equity: float, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        if now.date() != self.state.day:
            # BUGFIX: `tripped`/`trip_reason` NICHT mehr zuruecksetzen. Vorher wurde
            # beim Tageswechsel ein komplett neuer RiskState erzeugt (inkl. tripped=False)
            # - der Circuit Breaker loeste sich dadurch JEDEN Tag automatisch wieder, ganz
            # ohne den dokumentierten manuellen reset(confirm=True)-Weg. Nur die
            # Tages-Buchhaltung (day, day_start_equity, realized_today) wird zurueckgesetzt;
            # eine ausgeloeste Sperre bleibt bis zur bewussten Entscheidung aktiv.
            was_tripped, reason = self.state.tripped, self.state.trip_reason
            self.state = RiskState(day=now.date(), day_start_equity=equity,
                                   peak_equity=max(self.state.peak_equity, equity),
                                   tripped=was_tripped, trip_reason=reason)
            log.info("Neuer Handelstag %s - Tagesverlust-Zaehler zurueckgesetzt (Circuit "
                     "Breaker bleibt %s).", now.date(), "AKTIV" if was_tripped else "inaktiv")
        self.equity = equity
        self.state.peak_equity = max(self.state.peak_equity, equity)
        self._check_circuit_breaker()

    @property
    def daily_pnl_pct(self) -> float:
        base = self.state.day_start_equity
        return (self.equity - base) / base if base else 0.0

    @property
    def total_drawdown(self) -> float:
        peak = self.state.peak_equity
        return (peak - self.equity) / peak if peak else 0.0

    # ---- Order-Pruefung ---------------------------------------------------
    def position_size(self, entry_price: float, stop_loss: float) -> float:
        stop_dist = abs(entry_price - stop_loss)
        if stop_dist <= 0:
            return 0.0
        qty = (self.equity * self.limits.risk_per_trade) / stop_dist
        max_notional = self.equity * self.limits.max_position_notional_pct
        return min(qty, max_notional / entry_price) if entry_price > 0 else 0.0

    def check_order(
        self, *, symbol: str, qty: float, entry_price: float,
        stop_loss: float | None, open_positions: int,
    ) -> RiskDecision:
        if self.state.tripped:
            return self._deny(symbol, f"Circuit Breaker aktiv: {self.state.trip_reason}")
        if self.limits.require_stop_loss and stop_loss is None:
            return self._deny(symbol, "Order ohne Stop-Loss ist nicht zulaessig.")
        if open_positions >= self.limits.max_open_positions:
            return self._deny(symbol, f"Limit offener Positionen erreicht ({self.limits.max_open_positions}).")
        if qty <= 0 or entry_price <= 0:
            return self._deny(symbol, "Ungueltige Menge oder Preis.")
        # Bugfix Stop-Loss-Loop: bei ATR nahe Null lag der Stop praktisch auf dem
        # Einstieg und wurde vom eigenen Kerzen-High/Low sofort wieder ausgeloest ->
        # Dauerschleife aus Neu-Eroeffnung und Stop, bis der Tagesverlust-Breaker griff.
        # Ein zu enger Stop wird daher hart abgelehnt statt platziert.
        if stop_loss is not None:
            min_dist = entry_price * self.limits.min_stop_pct
            if abs(entry_price - stop_loss) < min_dist:
                return self._deny(
                    symbol, f"Stop-Abstand {abs(entry_price - stop_loss):.6g} zu klein "
                            f"(< {self.limits.min_stop_pct:.2%} = {min_dist:.6g}) - Order abgelehnt.")

        max_qty = self.position_size(entry_price, stop_loss) if stop_loss is not None else 0.0
        if max_qty <= 0:
            return self._deny(symbol, "Berechnete maximale Positionsgroesse ist 0.")
        adjusted = min(qty, max_qty)

        decision = RiskDecision(True, "ok", adjusted)
        self._log("order_allowed", symbol=symbol, qty=adjusted, requested=qty)
        return decision

    # ---- Circuit Breaker / Kill-Switch ------------------------------------
    def _check_circuit_breaker(self) -> None:
        if self.state.tripped:
            return
        if self.daily_pnl_pct <= -self.limits.max_daily_loss:
            self.trip(f"Tagesverlust {self.daily_pnl_pct:.2%} <= Limit -{self.limits.max_daily_loss:.2%}")
        elif self.total_drawdown >= self.limits.max_total_drawdown:
            self.trip(f"Gesamtdrawdown {self.total_drawdown:.2%} >= Limit {self.limits.max_total_drawdown:.2%}")

    def trip(self, reason: str) -> None:
        """Circuit Breaker ausloesen: Modus faellt auf PAPER zurueck."""
        self.state.tripped = True
        self.state.trip_reason = reason
        previous = self.mode
        if self.mode == Mode.LIVE:
            self.mode = Mode.PAPER
            if self.on_mode_change:
                self.on_mode_change(Mode.PAPER, reason)
        log.error("CIRCUIT BREAKER ausgeloest (%s -> %s): %s", previous.value, self.mode.value, reason)
        self._log("circuit_breaker", reason=reason, previous_mode=previous.value, new_mode=self.mode.value)
        if self.on_trip:
            self.on_trip(reason)

    def kill_switch(self, reason: str = "Manuell ausgeloest") -> None:
        """Sofortiger Not-Aus aus dem UI. Immer erlaubt, nie automatisch rueckgaengig."""
        self.trip(reason)

    def reset(self, confirm: bool = False) -> None:
        """Circuit Breaker manuell entsperren. Setzt den Modus NICHT auf LIVE zurueck -
        dafuer ist der explizite Bestaetigungsflow noetig."""
        if not confirm:
            raise PermissionError("reset() erfordert confirm=True - bewusste manuelle Entscheidung.")
        self.state.tripped = False
        self.state.trip_reason = ""
        self._log("risk_reset", mode=self.mode.value)
        if self.on_reset:
            self.on_reset()

    # ---- intern -----------------------------------------------------------
    def _deny(self, symbol: str, reason: str) -> RiskDecision:
        log.warning("Order fuer %s abgelehnt: %s", symbol, reason)
        self._log("order_denied", symbol=symbol, reason=reason)
        return RiskDecision(False, reason)

    def _log(self, event: str, **fields) -> None:
        self.audit.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event, "mode": self.mode.value,
            "equity": self.equity, "daily_pnl_pct": self.daily_pnl_pct,
            **fields,
        })
