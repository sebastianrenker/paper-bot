"""13 - Ichimoku-Trendfolge: Preis relativ zur Wolke + Tenkan/Kijun-Kreuzung."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class IchimokuTrend(Strategy):
    name = "ichimoku_trend"
    category = "trend"
    description = "Long ueber der Wolke mit Tenkan>Kijun, Short unter der Wolke mit Tenkan<Kijun."

    @staticmethod
    def default_params() -> dict:
        return {"tenkan": 9, "kijun": 26, "senkou_b": 52}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        conv, base, span_a, span_b = ta.ichimoku(ohlcv, p["tenkan"], p["kijun"], p["senkou_b"])
        close = ohlcv["close"]
        cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
        cloud_bot = pd.concat([span_a, span_b], axis=1).min(axis=1)

        long_sig = (close > cloud_top) & (conv > base)
        short_sig = (close < cloud_bot) & (conv < base)

        direction = pd.Series(0, index=ohlcv.index)
        direction[long_sig] = 1
        direction[short_sig] = -1

        thickness = (cloud_top - cloud_bot).replace(0.0, np.nan)
        dist = np.where(direction > 0, close - cloud_top, np.where(direction < 0, cloud_bot - close, 0.0))
        conf = (pd.Series(dist, index=ohlcv.index) / thickness).clip(0, 1).fillna(0.0)
        conf[direction == 0] = 0.0

        out["direction"] = direction
        out["confidence"] = conf
        out["reason"] = np.where(direction > 0, "Preis ueber Wolke, Tenkan > Kijun",
                                 np.where(direction < 0, "Preis unter Wolke, Tenkan < Kijun", ""))
        warmup = p["senkou_b"] * 2
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
