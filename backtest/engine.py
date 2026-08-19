"""Ereignisbasierte Backtest-Engine.

Design-Entscheidungen (bewusst konservativ, damit Ergebnisse nicht geschoent sind):
  * Ein Signal von Bar t wird zum OPEN von Bar t+1 ausgefuehrt -> kein Look-ahead.
  * Stop-Loss wird VOR Take-Profit geprueft, wenn beide im selben Bar liegen
    (pessimistische Annahme, da die Intrabar-Reihenfolge unbekannt ist).
  * Gebuehren und Slippage werden auf jede Seite angewandt.
  * Positionsgroesse folgt dem Risikomodell: risk_per_trade * Equity / Stop-Abstand.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from core import indicators as ta
from core.types import Direction, Trade
from strategies.base import Strategy


@dataclass
class BacktestConfig:
    initial_capital: float = 10_000.0
    risk_per_trade: float = 0.01      # 1 % des Kapitals pro Trade
    atr_period: int = 14
    stop_atr_mult: float = 2.0
    take_profit_r: float = 2.0        # TP bei 2R; None = kein TP
    fee_rate: float = 0.0006          # pro Seite
    slippage_rate: float = 0.0005     # pro Seite
    allow_short: bool = True
    max_bars_in_trade: Optional[int] = None


@dataclass
class BacktestResult:
    strategy: str
    market: str
    symbol: str
    timeframe: str
    trades: list[Trade] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    config: BacktestConfig = field(default_factory=BacktestConfig)
    data_source: str = "unknown"

    @property
    def r_multiples(self) -> np.ndarray:
        return np.array([t.r_multiple for t in self.trades if not t.is_open])

    def trades_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame()
        return pd.DataFrame(
            [
                {
                    "strategy": t.strategy, "symbol": t.symbol, "timeframe": t.timeframe,
                    "direction": t.direction.value, "entry_time": t.entry_time,
                    "entry_price": t.entry_price, "exit_time": t.exit_time,
                    "exit_price": t.exit_price, "qty": t.qty, "pnl": t.pnl,
                    "r_multiple": t.r_multiple, "exit_reason": t.exit_reason,
                    "reason": t.reason,
                }
                for t in self.trades
            ]
        )


def run_backtest(
    strategy: Strategy,
    ohlcv: pd.DataFrame,
    *,
    market: str = "crypto",
    symbol: str = "UNKNOWN",
    timeframe: str = "1h",
    config: BacktestConfig | None = None,
    data_source: str = "unknown",
) -> BacktestResult:
    cfg = config or BacktestConfig()
    strategy.validate(ohlcv)
    signals = strategy.compute(ohlcv)
    atr = ta.atr(ohlcv, cfg.atr_period)

    equity = cfg.initial_capital
    equity_points: list[float] = []
    trades: list[Trade] = []
    open_trade: Trade | None = None
    bars_in_trade = 0

    idx = ohlcv.index
    o, h, l, c = (ohlcv[col].to_numpy() for col in ("open", "high", "low", "close"))
    dirs = signals["direction"].to_numpy()
    confs = signals["confidence"].to_numpy()
    reasons = signals["reason"].to_numpy()
    atr_v = atr.to_numpy()

    for i in range(1, len(ohlcv)):
        price_open = o[i]

        # --- 1. offene Position gegen den aktuellen Bar pruefen ------------
        if open_trade is not None:
            bars_in_trade += 1
            sign = open_trade.direction.sign
            exit_price = exit_reason = None

            hit_stop = (l[i] <= open_trade.stop_loss) if sign > 0 else (h[i] >= open_trade.stop_loss)
            hit_tp = False
            if open_trade.take_profit is not None:
                hit_tp = (h[i] >= open_trade.take_profit) if sign > 0 else (l[i] <= open_trade.take_profit)

            if hit_stop:  # pessimistisch: Stop schlaegt TP im selben Bar
                exit_price, exit_reason = open_trade.stop_loss, "stop_loss"
            elif hit_tp:
                exit_price, exit_reason = open_trade.take_profit, "take_profit"
            elif cfg.max_bars_in_trade and bars_in_trade >= cfg.max_bars_in_trade:
                exit_price, exit_reason = c[i], "max_bars"
            elif dirs[i - 1] != sign:  # Signal gedreht oder flat -> zum Open schliessen
                exit_price, exit_reason = price_open, "signal_exit"

            if exit_price is not None:
                fill = _apply_costs(exit_price, -sign, cfg)
                open_trade.exit_price = fill
                open_trade.exit_time = idx[i].to_pydatetime()
                open_trade.exit_reason = exit_reason
                open_trade.fees += abs(fill * open_trade.qty) * cfg.fee_rate
                equity += open_trade.pnl
                trades.append(open_trade)
                open_trade = None
                bars_in_trade = 0

        # --- 2. neues Signal von Bar i-1 zum Open von Bar i ausfuehren -----
        if open_trade is None:
            sig_dir = int(dirs[i - 1])
            if sig_dir != 0 and not (sig_dir < 0 and not cfg.allow_short):
                stop_dist = atr_v[i - 1] * cfg.stop_atr_mult
                if np.isfinite(stop_dist) and stop_dist > 0 and equity > 0:
                    entry = _apply_costs(price_open, sig_dir, cfg)
                    risk_cash = equity * cfg.risk_per_trade
                    qty = risk_cash / stop_dist
                    notional = qty * entry
                    if notional > equity * 10:  # Hebel hart deckeln
                        qty = equity * 10 / entry
                        # BUGFIX: notional muss zur GEDECKELTEN Menge passen. Vorher
                        # blieb `notional` auf dem ungedeckelten (bis zu 10x groesseren)
                        # Wert stehen, sodass die Einstiegsgebuehr unten auf einer
                        # Position berechnet wurde, die so nie eroeffnet wurde -> zu
                        # hohe Gebuehren, verfaelschter PnL und r_multiple bei engen Stops.
                        notional = qty * entry
                    stop = entry - sig_dir * stop_dist
                    tp = entry + sig_dir * stop_dist * cfg.take_profit_r if cfg.take_profit_r else None
                    open_trade = Trade(
                        strategy=strategy.name, market=market, symbol=symbol, timeframe=timeframe,
                        direction=Direction.LONG if sig_dir > 0 else Direction.SHORT,
                        entry_time=idx[i].to_pydatetime(), entry_price=entry, qty=qty,
                        stop_loss=stop, take_profit=tp,
                        reason=f"{reasons[i-1]} (conf {confs[i-1]:.2f})",
                        fees=abs(notional) * cfg.fee_rate,
                    )
                    bars_in_trade = 0

        # --- 3. Equity inkl. unrealisiertem PnL ---------------------------
        unrealized = 0.0
        if open_trade is not None:
            unrealized = (c[i] - open_trade.entry_price) * open_trade.direction.sign * open_trade.qty
        equity_points.append(equity + unrealized)

    if open_trade is not None:
        trades.append(open_trade)  # bleibt offen, geht nicht in die Statistik ein

    return BacktestResult(
        strategy=strategy.name, market=market, symbol=symbol, timeframe=timeframe,
        trades=trades,
        equity_curve=pd.Series(equity_points, index=idx[1:len(equity_points) + 1]),
        config=cfg, data_source=data_source,
    )


def _apply_costs(price: float, side_sign: int, cfg: BacktestConfig) -> float:
    """Slippage verschlechtert den Fill immer zu Lasten des Traders."""
    return price * (1 + side_sign * cfg.slippage_rate)
