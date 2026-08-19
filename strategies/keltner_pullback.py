"""10 - Keltner-Pullback zum 20-EMA mit ADX-Trendfilter.

Recherche-Basis: Pullback zum 20-EMA im bestaetigten Trend (ADX) gilt quellen-
uebergreifend als eine der robustesten Setups (opofinance, liberatedstocktrader).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class KeltnerPullback(Strategy):
    name = "keltner_pullback"
    category = "trend"
    description = "Im ADX-Trend: Einstieg beim Ruecklauf an den EMA/unteres Keltner-Band."

    @staticmethod
    def default_params() -> dict:
        return {"ema_period": 20, "atr_period": 20, "mult": 2.0, "adx_min": 25}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        lower, mid, upper = ta.keltner(ohlcv, p["ema_period"], p["atr_period"], p["mult"])
        adx = ta.adx(ohlcv, p["atr_period"])
        trend_up = mid > mid.shift(p["ema_period"])
        trend_dn = mid < mid.shift(p["ema_period"])
        strong = adx >= p["adx_min"]

        # Long: Aufwaertstrend + Preis faellt an/unter das mittlere Band zurueck
        long_sig = strong & trend_up & (ohlcv["low"] <= mid)
        short_sig = strong & trend_dn & (ohlcv["high"] >= mid)

        direction = pd.Series(0, index=ohlcv.index)
        direction[long_sig] = 1
        direction[short_sig] = -1

        conf = ((adx - p["adx_min"]) / 25).clip(0, 1).fillna(0.0)
        conf[direction == 0] = 0.0

        out["direction"] = direction
        out["confidence"] = conf
        out["reason"] = np.where(direction > 0, "Pullback zum EMA im Aufwaertstrend (ADX)",
                                 np.where(direction < 0, "Pullback zum EMA im Abwaertstrend (ADX)", ""))
        warmup = max(p["ema_period"], p["atr_period"]) * 2
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
