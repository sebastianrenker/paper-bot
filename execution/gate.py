"""Das Live-Gate: der einzige Weg von Paper nach Live.

Drei Bedingungen muessen gleichzeitig erfuellt sein, sonst bleibt der Modus PAPER:
  (a) config.yaml enthaelt `mode: live`
  (b) der Nutzer hat den interaktiven Bestaetigungsflow durchlaufen
      (Wortlaut-Bestaetigung + zufaelliger Bestaetigungscode)
  (c) fuer den jeweiligen Markt liegen echte API-Keys in der .env

Die Anwendung selbst darf `unlock_live()` NIE aufrufen - nur die CLI auf
ausdrueckliche Nutzereingabe hin. Das Gate ist prozesslokal und wird nicht
persistiert: nach jedem Neustart ist wieder Paper aktiv.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

from risk.manager import Mode

CONFIRM_PHRASE = "Ich verstehe, dass echtes Geld eingesetzt wird"

REQUIRED_ENV = {
    "crypto": ["EXCHANGE_API_KEY", "EXCHANGE_API_SECRET"],
    "stocks": ["ALPACA_API_KEY", "ALPACA_API_SECRET"],
    "forex": ["OANDA_API_TOKEN", "OANDA_ACCOUNT_ID"],
}


@dataclass
class GateState:
    unlocked: bool = False
    token: str | None = None
    market: str | None = None


_STATE = GateState()


class LiveGateError(RuntimeError):
    pass


def is_unlocked() -> bool:
    return _STATE.unlocked


def assert_live_unlocked(token: str | None) -> None:
    if not _STATE.unlocked or token is None or token != _STATE.token:
        raise LiveGateError(
            "Live-Modus ist gesperrt. Der Wechsel erfordert den manuellen "
            "Bestaetigungsflow (python -m cli enable-live)."
        )


def missing_credentials(market: str) -> list[str]:
    return [name for name in REQUIRED_ENV.get(market, []) if not os.getenv(name)]


def lock() -> None:
    """Kill-Switch fuer das Gate - jederzeit erlaubt, auch automatisch."""
    _STATE.unlocked = False
    _STATE.token = None
    _STATE.market = None


def unlock_live(
    market: str,
    *,
    config_mode: str,
    confirm_phrase: str,
    expected_code: str,
    entered_code: str,
    risk_checks_passed: bool,
) -> str:
    """Schaltet den Live-Modus frei und gibt das Gate-Token zurueck.

    Darf ausschliesslich aus einem interaktiven Nutzerdialog heraus aufgerufen
    werden, niemals aus Strategie-, Score- oder Scheduler-Code.
    """
    if config_mode != Mode.LIVE.value:
        raise LiveGateError(f"config.yaml steht auf mode: {config_mode} - erforderlich ist 'live'.")
    if confirm_phrase.strip() != CONFIRM_PHRASE:
        raise LiveGateError("Bestaetigungssatz stimmt nicht woertlich ueberein.")
    if not entered_code or entered_code.strip() != expected_code:
        raise LiveGateError("Bestaetigungscode falsch.")
    missing = missing_credentials(market)
    if missing:
        raise LiveGateError(f"Fehlende API-Keys in .env fuer {market}: {', '.join(missing)}")
    if not risk_checks_passed:
        raise LiveGateError("Risikolimits sind nicht vollstaendig konfiguriert.")

    _STATE.unlocked = True
    _STATE.token = secrets.token_hex(16)
    _STATE.market = market
    return _STATE.token


def new_confirmation_code() -> str:
    """Zufallscode, den der Nutzer abtippen muss - verhindert versehentliches Durchklicken."""
    return secrets.token_hex(3).upper()
