"""Live-Broker-Adapter - STANDARDMAESSIG DEAKTIVIERT.

Dieser Modul-Stub definiert die Adapter fuer echte Konten (ccxt / Alpaca / OANDA),
enthaelt aber bewusst KEINEN funktionsfaehigen Order-Code. Er ist der letzte
Implementierungsschritt (Plan 7.7) und darf erst gefuellt werden, wenn:

  1. der Paper-Betrieb ueber einen laengeren Zeitraum nachweislich stabil laeuft,
  2. das Risikomodul inkl. Circuit-Breaker-Test gruen ist,
  3. der Nutzer den Bestaetigungsflow in `execution.gate` bewusst durchlaufen hat.

Jeder Versuch, einen Live-Adapter ohne freigeschaltetes Gate zu instanziieren,
wirft eine Exception. Das ist Absicht und keine fehlende Implementierung.
"""
from __future__ import annotations

from typing import Literal, Optional

from core.types import Order, Position
from execution.base import BrokerAdapter

LIVE_TRADING_IMPLEMENTED = False


class LiveTradingNotEnabled(RuntimeError):
    pass


class LiveBrokerBase(BrokerAdapter):
    mode = "live"

    def __init__(self, market: str, credentials: dict, gate_token: str | None = None) -> None:
        from execution.gate import assert_live_unlocked  # lokaler Import: Zyklus vermeiden

        assert_live_unlocked(gate_token)
        if not LIVE_TRADING_IMPLEMENTED:
            raise LiveTradingNotEnabled(
                "Live-Trading ist in dieser Version nicht implementiert (Plan-Schritt 7.7). "
                "Nutze PaperBroker. Erst nach dokumentiertem Paper-Nachweis freischalten."
            )
        self.market = market
        self.credentials = credentials

    def place_order(self, symbol, side: Literal["buy", "sell"], qty, sl=None, tp=None,
                    strategy: str = "", **meta) -> Order:
        raise LiveTradingNotEnabled("Nicht implementiert.")

    def close_position(self, symbol: str, price: Optional[float] = None, strategy: str = "") -> None:
        raise LiveTradingNotEnabled("Nicht implementiert.")

    def get_positions(self, strategy: Optional[str] = None) -> list[Position]:
        raise LiveTradingNotEnabled("Nicht implementiert.")

    def get_account_balance(self) -> float:
        raise LiveTradingNotEnabled("Nicht implementiert.")


class CcxtLiveBroker(LiveBrokerBase):
    """Krypto ueber ccxt. Geplant: Exchange-Instanz aus .env-Keys, `create_order`
    mit gekoppelter Stop-Loss-Order. Noch nicht implementiert."""
    name = "ccxt"


class AlpacaLiveBroker(LiveBrokerBase):
    """US-Aktien ueber Alpaca. Paper- und Live-Endpoint teilen dasselbe API-Schema;
    unterschiedlich sind nur Base-URL und Keys. Noch nicht implementiert."""
    name = "alpaca"


class OandaLiveBroker(LiveBrokerBase):
    """Forex ueber OANDA v20. Demo- und Live-Umgebung teilen das API-Schema.
    Noch nicht implementiert."""
    name = "oanda"


LIVE_ADAPTERS = {
    "crypto": CcxtLiveBroker,
    "stocks": AlpacaLiveBroker,
    "forex": OandaLiveBroker,
}
