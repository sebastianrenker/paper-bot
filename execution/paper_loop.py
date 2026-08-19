"""Paper-Trading-Loop: Strategien laufen auf Live-Daten gegen ein simuliertes Konto.

Jede Order laeuft zwingend durch den RiskManager. Der Loop kennt keinen Pfad, der
den Modus selbsttaetig auf LIVE hebt - der Broker wird von aussen injiziert.

WICHTIG (Bugfix): Positionen werden pro (Strategie, Symbol) verwaltet - nicht mehr
nur pro Symbol. Laufen mehrere Strategien gleichzeitig auf demselben Symbol (der
Standardfall, wenn `strategies: []` in config.yaml alle registrierten Strategien
aktiviert), sah der Loop vorher "die" Position des Symbols, unabhaengig davon, welche
Strategie sie eroeffnet hatte - jede andere Strategie mit einem abweichenden Signal
schloss sie dadurch sofort wieder. Bestaetigt im echten Lauf (bot_run.log/trading.db):
donchian_breakout eroeffnete 21 Positionen, 0 davon selbst geschlossen - fast alle
wurden Sekunden spaeter von einer anderen Strategie (z. B. connors_rsi2) ueber
'signal_exit' wieder zugemacht, reiner Gebuehren-/Slippage-Verlust ohne dass die
eroeffnende Strategie je eine Chance auf ihren eigenen Stop/Take-Profit hatte.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from core import indicators as ta
from core.store import Store
from core.types import Direction
from data.loader import TIMEFRAME_MINUTES, DataLoader, MarketSpec
from execution.base import BrokerAdapter
from risk.manager import Mode, RiskManager
from strategies import REGISTRY

log = logging.getLogger(__name__)


def closed_bars(ohlcv: pd.DataFrame, timeframe: str, now: datetime | None = None) -> pd.DataFrame:
    """Entfernt den letzten Balken, falls er noch NICHT abgeschlossen ist (laeuft).

    Bugfix (Look-Ahead): ccxt/yfinance liefern live IMMER den aktuellen, noch offenen
    Balken als letzte Zeile mit. Ihn fuer Signal-, ATR- und Stop-Berechnung zu benutzen
    heisst, auf einer unvollstaendigen Kerze zu handeln, deren Werte sich bis zum
    Bar-Ende noch beliebig aendern - das Signal flackert und stimmt nicht mit der im
    Backtest validierten Logik (die nur abgeschlossene Kerzen sieht) ueberein. Der
    Balken gilt als 'laeuft noch', solange jetzt < Balken-Oeffnungszeit + Timeframe."""
    if len(ohlcv) < 2:
        return ohlcv
    minutes = TIMEFRAME_MINUTES.get(timeframe, 0)
    if minutes <= 0:
        return ohlcv
    now = now or datetime.now(timezone.utc)
    last_open = ohlcv.index[-1].to_pydatetime()
    if last_open.tzinfo is None:
        last_open = last_open.replace(tzinfo=timezone.utc)
    if now < last_open + timedelta(minutes=minutes):
        return ohlcv.iloc[:-1]
    return ohlcv


@dataclass
class PaperLoopConfig:
    poll_seconds: int = 300
    bars: int = 600
    stop_atr_mult: float = 2.0
    take_profit_r: float = 2.0
    atr_period: int = 14
    # Positionsgroesse zusaetzlich mit der Strategie-Konfidenz skalieren (0..1).
    # Bugfix: die von jeder Strategie berechnete `confidence` floss bisher NIRGENDS
    # in die Positionsgroesse ein - ein Signal mit 5% Konfidenz bekam exakt dieselbe
    # Groesse wie eines mit 95%. min_confidence_scale ist der Bodenwert bei confidence=0,
    # damit ein knapp ueber der Schwelle liegendes Signal nicht auf (fast) 0 faellt.
    min_confidence_scale: float = 0.4

    def size_multiplier(self, confidence: float) -> float:
        c = max(0.0, min(confidence, 1.0))
        return self.min_confidence_scale + (1.0 - self.min_confidence_scale) * c


class PaperTradingLoop:
    def __init__(
        self, broker: BrokerAdapter, risk: RiskManager, store: Store,
        combos: list[tuple[str, str, str, str]],   # (strategy, market, symbol, timeframe)
        config: PaperLoopConfig | None = None, loader: DataLoader | None = None,
        strategy_params: dict[str, dict] | None = None,
        combo_params: dict[tuple, dict] | None = None,
    ) -> None:
        if broker.is_live and risk.mode != Mode.LIVE:
            raise RuntimeError("Live-Broker bei nicht-freigeschaltetem Live-Modus - abgebrochen.")
        self.broker = broker
        self.risk = risk
        self.store = store
        self.combos = combos
        self.cfg = config or PaperLoopConfig()
        self.loader = loader or DataLoader()
        self.strategy_params = strategy_params or {}
        # Pro-Kombination validierte Parameter (aus der Selbst-Optimierung).
        # Mutable: serve aktualisiert dieses Dict live -> der Bot passt sich an.
        self.combo_params: dict[tuple, dict] = combo_params if combo_params is not None else {}
        self._running = False
        # Balken-Debounce (Bugfix Stop-Loss-Loop): merkt sich pro (strategy, symbol)
        # den Timestamp des abgeschlossenen Balkens, auf dem zuletzt eine Position durch
        # Stop-Loss/Take-Profit geschlossen wurde. Auf genau diesem Balken darf dieselbe
        # Kombination KEINE neue Position eroeffnen - erst wenn ein neuer, spaeter
        # geschlossener Balken vorliegt. Ohne das eroeffnete die Strategie auf demselben
        # (ruhigen) Balken im Minutentakt neu, wurde sofort wieder ausgestoppt und
        # verbrannte reine Gebuehren/Slippage, bis der Tagesverlust-Breaker ausloeste.
        self._blocked_bar: dict[tuple[str, str], pd.Timestamp] = {}
        self._breaker_hold_logged = False

    def params_for(self, strategy_name: str, symbol: str, timeframe: str) -> dict:
        """Bevorzugt die validierten Pro-Kombination-Parameter, sonst die Strategie-Defaults."""
        key = (strategy_name, symbol, timeframe)
        if key in self.combo_params:
            return self.combo_params[key]
        return self.strategy_params.get(strategy_name, {})

    def run_forever(self) -> None:
        self._running = True
        while self._running:
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - Loop darf nicht sterben
                log.exception("Fehler im Paper-Loop-Tick")
            time.sleep(self.cfg.poll_seconds)

    def stop(self, reason: str = "manuell gestoppt") -> None:
        self._running = False
        self.store.log("loop_stopped", mode=self.risk.mode.value, reason=reason)

    def tick(self) -> None:
        # (strategy, symbol) -> (market, timeframe), zum Anreichern persistierter Trades.
        combo_lookup = {(s, sym): (m, tf) for s, m, sym, tf in self.combos}

        for strategy_name, market, symbol, timeframe in self.combos:
            spec = MarketSpec(market, symbol, timeframe)
            try:
                ohlcv = self.loader.load(spec, bars=self.cfg.bars, refresh=True)
            except Exception as exc:  # noqa: BLE001 - im strikten Modus: Symbol ohne Echtdaten ueberspringen
                log.warning("Tick ueberspringt %s (keine Echtdaten): %s", spec.slug, exc)
                continue
            if len(ohlcv) < 100:
                continue
            self._process(strategy_name, spec, ohlcv)

        self._flush_closed_trades(combo_lookup)

        equity = self.broker.get_account_balance()
        self.risk.update_equity(equity)
        self.store.save_equity_point(equity, source=self.broker.mode)
        self._persist_state(equity)
        if self.risk.state.tripped:
            self._flatten_all(self.risk.state.trip_reason)
            self._flush_closed_trades(combo_lookup)
        else:
            # Breaker wurde (z. B. per reset_breaker) aufgehoben -> Hold-Log wieder scharf.
            self._breaker_hold_logged = False

    def _flush_closed_trades(self, combo_lookup: dict[tuple, tuple]) -> None:
        """Schreibt seit dem letzten Flush geschlossene Paper-Trades strukturiert in
        die `trades`-Tabelle (Bugfix: vorher gab es dort NIE einen Eintrag mit
        source='paper' - reale Performance war nur unstrukturiert im Audit-Log sichtbar,
        die Positions-Kollision waere darueber nie aufgefallen)."""
        closed = getattr(self.broker, "closed_trades", None)
        if not closed:
            return
        rows = []
        for t in closed:
            market, timeframe = combo_lookup.get((t["strategy"], t["symbol"]), ("", ""))
            risk_amt = None
            if t.get("stop_loss") is not None:
                risk_amt = abs(t["entry_price"] - t["stop_loss"]) * t["qty"]
            r_multiple = (t["pnl"] / risk_amt) if risk_amt else 0.0
            rows.append({
                "strategy": t["strategy"], "market": market, "symbol": t["symbol"],
                "timeframe": timeframe, "direction": t["direction"],
                "entry_time": t["entry_time"], "entry_price": t["entry_price"],
                "exit_time": t["exit_time"], "exit_price": t["exit_price"],
                "qty": t["qty"], "pnl": t["pnl"], "r_multiple": r_multiple,
                "exit_reason": t["exit_reason"], "reason": "",
            })
        self.store.save_trades(rows, source=self.broker.mode)
        closed.clear()

    def _persist_state(self, equity: float) -> None:
        """Positionen + Heartbeat in die DB schreiben, damit das Dashboard live mitliest."""
        positions = self.broker.get_positions()
        snapshot = []
        for p in positions:
            mark = self.broker.mark_price(p.symbol) if hasattr(self.broker, "mark_price") else p.avg_price
            snapshot.append({
                "symbol": p.symbol, "strategy": getattr(p, "strategy", ""), "qty": p.qty,
                "avg_price": p.avg_price, "stop_loss": p.stop_loss, "take_profit": p.take_profit,
                "mark_price": mark, "unrealized": (mark - p.avg_price) * p.qty,
                "entry_time": getattr(p, "entry_time", None),
            })
        self.store.save_positions_snapshot(snapshot, source=self.broker.mode)
        self.store.save_heartbeat(
            self.broker.mode, equity=equity, open_positions=len(positions),
            daily_pnl_pct=self.risk.daily_pnl_pct, tripped=self.risk.state.tripped,
            note=self.risk.state.trip_reason,
        )

    # ---- intern -----------------------------------------------------------
    def _process(self, strategy_name: str, spec: MarketSpec, ohlcv: pd.DataFrame) -> None:
        # Signal/ATR NUR aus abgeschlossenen Kerzen (Look-Ahead-Fix, siehe closed_bars).
        # Der aktuelle Marktpreis + die Stop-Pruefung nutzen weiter den laufenden Balken.
        signal_ohlcv = closed_bars(ohlcv, spec.timeframe)
        if len(signal_ohlcv) < 50:
            return
        bar_key = (strategy_name, spec.symbol)
        bar_ts = signal_ohlcv.index[-1]   # Timestamp des zuletzt abgeschlossenen Balkens
        last = ohlcv.iloc[-1]
        current_price = float(last["close"])
        self.broker.update_price(spec.symbol, current_price)

        # 1. Stops der bestehenden Position DIESER Strategie gegen den laufenden Bar pruefen
        if hasattr(self.broker, "check_stops"):
            hit = self.broker.check_stops(spec.symbol, float(last["high"]), float(last["low"]),
                                          strategy=strategy_name)
            if hit:
                self.store.log("position_closed", mode=self.broker.mode, strategy=strategy_name,
                               symbol=spec.symbol, exit_reason=hit)
                # Debounce: nach Stop/Take-Profit auf DIESEM Balken keine Neu-Eroeffnung
                # derselben Kombination mehr, bis ein neuer Balken schliesst.
                if hit in ("stop_loss", "take_profit"):
                    self._blocked_bar[bar_key] = bar_ts

        strategy = REGISTRY[strategy_name](**self.params_for(strategy_name, spec.symbol, spec.timeframe))
        signal = strategy.generate_signal(signal_ohlcv)
        # Nur die eigene Position dieser Strategie auf diesem Symbol zaehlt als "current" -
        # andere Strategien auf demselben Symbol werden hier bewusst NICHT angefasst.
        current = next(
            (p for p in self.broker.get_positions(strategy=strategy_name) if p.symbol == spec.symbol), None
        )

        # 2. Gegensignal oder FLAT -> bestehende Position DIESER Strategie schliessen
        if current is not None:
            current_dir = Direction.LONG if current.qty > 0 else Direction.SHORT
            if signal.direction != current_dir:
                self.broker.close_position(spec.symbol, strategy=strategy_name, exit_reason="signal_exit")
                self.store.log("position_closed", mode=self.broker.mode, strategy=strategy_name,
                               symbol=spec.symbol, exit_reason="signal_exit", signal=signal.direction.value)
                current = None

        if signal.direction == Direction.FLAT or current is not None:
            return

        # Debounce-Sperre: auf diesem Balken wurde die Kombination bereits ausgestoppt.
        if self._blocked_bar.get(bar_key) == bar_ts:
            self.store.log("order_debounced", mode=self.broker.mode, strategy=strategy_name,
                           symbol=spec.symbol, reason="bereits auf diesem Balken ausgestoppt")
            return

        # 3. Neues Signal -> Risikopruefung -> Order (Einstieg zum aktuellen Marktpreis,
        #    ATR aus abgeschlossenen Kerzen).
        price = current_price
        atr = float(ta.atr(signal_ohlcv, self.cfg.atr_period).iloc[-1])
        if not atr > 0:
            return
        sign = signal.direction.sign
        stop = price - sign * atr * self.cfg.stop_atr_mult
        tp = price + sign * atr * self.cfg.stop_atr_mult * self.cfg.take_profit_r

        # Portfolio-weites Limit (max_open_positions) zaehlt ueber ALLE Strategien -
        # das bleibt bewusst so, dieses Limit ist ein Gesamt-Risikolimit, kein Limit
        # pro Strategie.
        all_positions = self.broker.get_positions()

        base_qty = self.risk.position_size(price, stop)
        qty = base_qty * self.cfg.size_multiplier(signal.confidence)
        decision = self.risk.check_order(
            symbol=spec.symbol, qty=qty, entry_price=price, stop_loss=stop,
            open_positions=len(all_positions),
        )
        if not decision.allowed:
            self.store.log("order_denied", mode=self.broker.mode, strategy=strategy_name,
                           symbol=spec.symbol, reason=decision.reason)
            return

        self.broker.place_order(
            spec.symbol, "buy" if sign > 0 else "sell", decision.adjusted_qty,
            sl=stop, tp=tp, strategy=strategy_name, signal_reason=signal.reason,
            confidence=signal.confidence,
        )
        self.store.log("order_placed", mode=self.broker.mode, strategy=strategy_name,
                       symbol=spec.symbol, side="buy" if sign > 0 else "sell",
                       qty=decision.adjusted_qty, price=price, stop_loss=stop,
                       take_profit=tp, reason=signal.reason, confidence=signal.confidence)

    def _flatten_all(self, reason: str) -> None:
        """Bei Circuit-Breaker-Trip: alle Positionen glattstellen und in einen sicheren
        WARTEZUSTAND gehen - der Loop laeuft weiter (tickt), platziert aber keine neuen
        Orders (das verhindert bereits `risk.state.tripped` in check_order), bis der
        Nutzer bewusst 'reset_breaker' schickt oder stoppt.

        Bugfix Prio 2: frueher rief diese Methode `self.stop()` und beendete damit den
        GESAMTEN Dauerbetrieb-Prozess (die aeussere `while loop._running`-Schleife in
        cmd_serve haengt daran). Am 2026-08-06 hat der Trip so den ganzen Bot beendet -
        er lief danach nie wieder an, weil ihn niemand manuell neu startete. Ein Trip
        soll nur den HANDEL anhalten, nicht den Prozess toeten."""
        for pos in list(self.broker.get_positions()):
            self.broker.close_position(pos.symbol, strategy=getattr(pos, "strategy", ""),
                                       exit_reason="forced_flat")
            self.store.log("forced_flat", mode=self.broker.mode, symbol=pos.symbol,
                           strategy=getattr(pos, "strategy", ""), reason=reason)
        if not self._breaker_hold_logged:
            self.store.log("breaker_hold", mode=self.broker.mode, reason=reason)
            log.warning("Circuit Breaker aktiv - Handel pausiert (Loop laeuft weiter): %s", reason)
            self._breaker_hold_logged = True
