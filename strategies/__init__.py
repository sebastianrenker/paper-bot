"""Strategie-Registry (Plug-in-Prinzip).

Neue Strategie hinzufuegen: Datei in diesem Ordner anlegen, von `Strategy` erben,
Klasse unten in REGISTRY eintragen. Weder Backtest-, Execution- noch UI-Code
muessen dafuer angefasst werden.
"""
from __future__ import annotations

from strategies.base import Strategy
from strategies.bollinger_breakout import BollingerBreakout
from strategies.cci_reversion import CciReversion
from strategies.connors_rsi2 import ConnorsRsi2
from strategies.dmi_trend import DmiTrend
from strategies.donchian_breakout import DonchianBreakout
from strategies.ema_crossover import EmaCrossover
from strategies.ichimoku_trend import IchimokuTrend
from strategies.keltner_pullback import KeltnerPullback
from strategies.macd_momentum import MacdMomentum
from strategies.opening_range_breakout import OpeningRangeBreakout
from strategies.roc_momentum import RocMomentum
from strategies.rsi_mean_reversion import RsiMeanReversion
from strategies.stochastic_reversion import StochasticReversion
from strategies.supertrend import Supertrend
from strategies.support_resistance import SupportResistance
from strategies.vwap_reversion import VwapReversion
from strategies.williams_r_reversion import WilliamsRReversion

REGISTRY: dict[str, type[Strategy]] = {
    cls.name: cls
    for cls in (
        EmaCrossover,
        RsiMeanReversion,
        BollingerBreakout,
        MacdMomentum,
        VwapReversion,
        OpeningRangeBreakout,
        DonchianBreakout,
        SupportResistance,
        Supertrend,
        KeltnerPullback,
        StochasticReversion,
        DmiTrend,
        IchimokuTrend,
        ConnorsRsi2,
        WilliamsRReversion,
        CciReversion,
        RocMomentum,
    )
}


def build(name: str, params: dict | None = None) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"Unbekannte Strategie '{name}'. Verfuegbar: {sorted(REGISTRY)}")
    return REGISTRY[name](**(params or {}))


def all_strategies(overrides: dict[str, dict] | None = None) -> list[Strategy]:
    overrides = overrides or {}
    return [build(name, overrides.get(name)) for name in REGISTRY]


__all__ = ["REGISTRY", "Strategy", "build", "all_strategies"]
