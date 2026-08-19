"""15 - Williams %R Mean-Reversion.

Recherche-Basis (quantifiedstrategies.com): tiefe Ueberverkauft-Zonen kaufen,
bei Preisstaerke aussteigen - hohe Trefferquoten bei geringer Marktzeit. Optionaler
SMA-Trendfilter reduziert Fehlsignale in Trends.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class WilliamsRReversion(Strategy):
    name = "williams_r_reversion"
    category = "mean_reversion"
    description = "Williams %R im Extrem (< -80 kaufen, > -20 verkaufen), optional mit Trendfilter."

    @staticmethod
    def default_params() -> dict:
        return {"period": 14, "oversold": -80, "overbought": -20, "trend_sma": 100}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        wr = ta.williams_r(ohlcv, p["period"])
        close = ohlcv["close"]

        long_sig = wr < p["oversold"]
        short_sig = wr > p["overbought"]
        if p["trend_sma"]:
            trend = ta.sma(close, p["trend_sma"])
            long_sig &= close > trend
            short_sig &= close < trend

        direction = pd.Series(0, index=ohlcv.index)
        direction[long_sig] = 1
        direction[short_sig] = -1

        conf = pd.Series(0.0, index=ohlcv.index)
        conf[long_sig] = ((p["oversold"] - wr) / (100 + p["oversold"])).clip(0, 1)[long_sig]
        conf[short_sig] = ((wr - p["overbought"]) / (-p["overbought"])).clip(0, 1)[short_sig]

        out["direction"] = direction
        out["confidence"] = conf.fillna(0.0)
        out["reason"] = np.where(direction > 0, "Williams %R stark ueberverkauft",
                                 np.where(direction < 0, "Williams %R stark ueberkauft", ""))
        warmup = max(p["period"], p["trend_sma"] or 0) * 2
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
