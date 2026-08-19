"""9 - Supertrend (ATR-Trendfolge)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class Supertrend(Strategy):
    name = "supertrend"
    category = "trend"
    description = "ATR-basiertes Supertrend-Band; Long ueber, Short unter dem Band."

    @staticmethod
    def default_params() -> dict:
        return {"period": 10, "mult": 3.0, "adx_filter": 20}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        _, direction = ta.supertrend(ohlcv, p["period"], p["mult"])

        if p["adx_filter"]:
            adx = ta.adx(ohlcv, p["period"])
            direction = direction.where(adx >= p["adx_filter"], 0)

        atr = ta.atr(ohlcv, p["period"]).replace(0.0, np.nan)
        dist = (ohlcv["close"] - (ohlcv["high"] + ohlcv["low"]) / 2).abs() / atr
        conf = dist.clip(0, 2).div(2).fillna(0.0)

        out["direction"] = direction.astype(int)
        out["confidence"] = conf
        out.loc[out["direction"] == 0, "confidence"] = 0.0
        out["reason"] = np.where(out["direction"] > 0, "Supertrend long",
                                 np.where(out["direction"] < 0, "Supertrend short", ""))
        warmup = p["period"] * 3
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
