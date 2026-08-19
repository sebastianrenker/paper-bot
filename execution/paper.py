"""Paper-Broker (Dry-Run): simuliert Fills lokal, beruehrt nie echtes Geld.

Bewusst konservativ: Fills bekommen Slippage und Gebuehren wie im Backtest, damit
die Paper-Equity-Kurve mit der Backtest-Kurve vergleichbar bleibt.

WICHTIG (Bugfix): Positionen werden pro (strategy, symbol) getrackt, NICHT mehr nur
pro Symbol. Laeuft der Bot mit mehreren Strategien gleichzeitig auf demselben Symbol
(Standardfall bei `strategies: []` in config.yaml), fuehrte das Alt-Verhalten dazu,
dass eine Strategie der anderen laufend die gerade eroeffnete Position wieder
zuschloss (bestaetigt im Log: donchian_breakout eroeffnete, connors_rsi2 schloss
Sekunden spaeter via 'signal_exit' - immer wieder, nur Gebuehren/Slippage-Verlust).
`strategy=""` bleibt als Fallback fuer Aufrufer, die (noch) keine Strategie angeben -
verhaelt sich dann wie frueher (eine anonyme, globale Position je Symbol).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal, Optional

from core.types import Order, Position
from execution.base import BrokerAdapter

log = logging.getLogger(__name__)


class PaperBroker(BrokerAdapter):
    mode = "paper"
    name = "paper"

    def __init__(self, market: str = "crypto", starting_balance: float = 10_000.0,
                 fee_rate: float = 0.0006, slippage_rate: float = 0.0005) -> None:
        self.market = market
        self.starting_balance = starting_balance
        self.cash = starting_balance
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        # Key: (strategy, symbol) - siehe Modul-Docstring.
        self._positions: dict[tuple[str, str], Position] = {}
        self._prices: dict[str, float] = {}
        self.orders: list[Order] = []
        self.fills: list[dict] = []
        # Zuletzt realisierte (geschlossene) Trades, fuer die Persistenz in
        # core.store.Store.save_trades() durch den Aufrufer (paper_loop). Wird bei
        # jedem Realize-Ereignis angehaengt und vom Aufrufer nach dem Lesen geleert.
        self.closed_trades: list[dict] = []

    # ---- Preis-Feed -------------------------------------------------------
    def update_price(self, symbol: str, price: float) -> None:
        self._prices[symbol] = price

    def mark_price(self, symbol: str) -> float:
        return self._prices.get(symbol, 0.0)

    # ---- Interface --------------------------------------------------------
    def place_order(self, symbol, side: Literal["buy", "sell"], qty: float,
                    sl: Optional[float] = None, tp: Optional[float] = None,
                    strategy: str = "", **meta) -> Order:
        if qty <= 0:
            raise ValueError("qty muss > 0 sein")
        price = self._prices.get(symbol)
        if price is None:
            raise RuntimeError(f"Kein Preis fuer {symbol} bekannt - update_price() zuerst aufrufen.")

        sign = 1 if side == "buy" else -1
        fill = price * (1 + sign * self.slippage_rate)
        fee = abs(fill * qty) * self.fee_rate
        self.cash -= fee

        key = (strategy, symbol)
        pos = self._positions.get(key)
        signed_qty = sign * qty
        now = datetime.now(timezone.utc)
        if pos is None:
            self._positions[key] = Position(symbol, signed_qty, fill, sl, tp,
                                            strategy=strategy, entry_time=now)
        else:
            new_qty = pos.qty + signed_qty
            if abs(new_qty) < 1e-12:
                self._realize(key, fill, exit_reason=meta.get("exit_reason", "close"))
            elif pos.qty * new_qty < 0:      # Richtungswechsel
                self._realize(key, fill, exit_reason=meta.get("exit_reason", "reverse"))
                self._positions[key] = Position(symbol, new_qty, fill, sl, tp,
                                                strategy=strategy, entry_time=now)
            else:                             # Aufstockung
                pos.avg_price = (pos.avg_price * abs(pos.qty) + fill * qty) / abs(new_qty)
                pos.qty = new_qty
                if sl is not None:
                    pos.stop_loss = sl
                if tp is not None:
                    pos.take_profit = tp

        order = Order(symbol=symbol, side=side, qty=qty, stop_loss=sl, take_profit=tp,
                      meta={"strategy": strategy, **meta})
        self.orders.append(order)
        self.fills.append({
            "ts": now.isoformat(), "symbol": symbol, "side": side, "strategy": strategy,
            "qty": qty, "price": fill, "fee": fee, "mode": self.mode, **meta,
        })
        log.info("[PAPER] %s %s %s %.6f @ %.4f (SL %s)", strategy or "-", side.upper(), symbol, qty, fill, sl)
        return order

    def close_position(self, symbol: str, price: Optional[float] = None,
                       strategy: str = "", exit_reason: str = "close") -> None:
        key = (strategy, symbol)
        pos = self._positions.get(key)
        if pos is None:
            return
        if price is not None:
            # Bugfix: `price` wurde frueher berechnet, aber nie tatsaechlich verwendet -
            # der Fill kam immer aus dem zuletzt via update_price() gesetzten Preis. Das
            # war unschaedlich, solange Aufrufer vorher artig update_price() riefen, aber
            # eine Falle fuer jeden neuen Aufrufer. Jetzt wird ein explizit uebergebener
            # Preis auch tatsaechlich als aktueller Marktpreis gesetzt.
            self.update_price(symbol, price)
        self.place_order(symbol, "sell" if pos.qty > 0 else "buy", abs(pos.qty),
                         strategy=strategy, exit_reason=exit_reason)

    def get_positions(self, strategy: Optional[str] = None) -> list[Position]:
        """Alle offenen Positionen, optional auf eine Strategie gefiltert.

        `strategy=None` (Default) liefert ALLE Positionen ueber alle Strategien -
        das ist weiterhin die richtige Basis fuer portfolio-weite Risikoprüfungen
        (z. B. `max_open_positions`, Gesamt-Equity)."""
        return [p for (s, _sym), p in self._positions.items()
                if abs(p.qty) > 1e-12 and (strategy is None or s == strategy)]

    def get_account_balance(self) -> float:
        """Cash + unrealisierter PnL aller offenen Positionen (ueber alle Strategien)."""
        unrealized = sum(
            (self._prices.get(p.symbol, p.avg_price) - p.avg_price) * p.qty
            for p in self.get_positions()
        )
        return self.cash + unrealized

    # ---- intern -----------------------------------------------------------
    def _realize(self, key: tuple[str, str], exit_price: float, exit_reason: str = "close") -> None:
        pos = self._positions.pop(key, None)
        if pos is None:
            return
        pnl = (exit_price - pos.avg_price) * pos.qty
        self.cash += pnl
        strategy, symbol = key
        self.closed_trades.append({
            "strategy": strategy or "unbekannt", "symbol": symbol, "direction": "LONG" if pos.qty > 0 else "SHORT",
            "entry_time": pos.entry_time, "entry_price": pos.avg_price,
            "exit_time": datetime.now(timezone.utc), "exit_price": exit_price,
            "qty": abs(pos.qty), "pnl": pnl, "exit_reason": exit_reason,
            "stop_loss": pos.stop_loss,
        })

    def check_stops(self, symbol: str, high: float, low: float, strategy: str = "") -> Optional[str]:
        """Prueft SL/TP gegen einen neuen Bar. Stop wird pessimistisch zuerst geprueft."""
        pos = self._positions.get((strategy, symbol))
        if pos is None:
            return None
        long = pos.qty > 0
        if pos.stop_loss is not None and ((long and low <= pos.stop_loss) or (not long and high >= pos.stop_loss)):
            self.close_position(symbol, pos.stop_loss, strategy=strategy, exit_reason="stop_loss")
            return "stop_loss"
        if pos.take_profit is not None and ((long and high >= pos.take_profit) or (not long and low <= pos.take_profit)):
            self.close_position(symbol, pos.take_profit, strategy=strategy, exit_reason="take_profit")
            return "take_profit"
        return None
