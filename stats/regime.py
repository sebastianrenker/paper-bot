"""Marktregime-Erkennung: Trend vs. Range, Volatilitaetsniveau."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core import indicators as ta


@dataclass
class Regime:
    adx: float
    trend_strength: float      # 0..1, aus ADX abgeleitet
    volatility_pct: float      # ATR in % vom Preis
    vol_percentile: float      # 0..1 relativ zur eigenen Historie
    label: str                 # trend | range | volatile_range

    def fit_for(self, category: str) -> float:
        """Wie gut passt das aktuelle Regime zu dieser Strategiekategorie? 0..1.

        Trendfolge/Momentum profitieren von hohem ADX, Mean-Reversion vom Gegenteil,
        Breakout von niedriger Vola (Kompression vor dem Ausbruch).
        """
        if category in ("trend", "momentum"):
            return self.trend_strength
        if category == "mean_reversion":
            return 1.0 - self.trend_strength
        if category == "breakout":
            return float(np.clip(1.0 - self.vol_percentile, 0.0, 1.0))
        if category == "structure":
            return float(1.0 - abs(self.trend_strength - 0.5) * 2 * 0.5)  # mittleres Regime bevorzugt
        return 0.5


def detect_regime(ohlcv: pd.DataFrame, adx_period: int = 14, lookback: int = 250) -> Regime:
    adx_series = ta.adx(ohlcv, adx_period)
    adx_now = float(adx_series.iloc[-1])
    atr_now = float(ta.atr(ohlcv, adx_period).iloc[-1])
    price = float(ohlcv["close"].iloc[-1])
    vol_pct = atr_now / price if price else 0.0

    atr_hist = (ta.atr(ohlcv, adx_period) / ohlcv["close"]).tail(lookback).dropna()
    vol_percentile = float((atr_hist <= vol_pct).mean()) if len(atr_hist) else 0.5

    # ADX 20-40 ist die uebliche Spanne "beginnender bis starker Trend"
    trend_strength = float(np.clip((adx_now - 15) / 25, 0.0, 1.0))
    if trend_strength > 0.5:
        label = "trend"
    elif vol_percentile > 0.7:
        label = "volatile_range"
    else:
        label = "range"

    return Regime(adx_now, trend_strength, vol_pct, vol_percentile, label)
