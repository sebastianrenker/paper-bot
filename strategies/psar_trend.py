"""18 - Parabolic-SAR-Trendfolge (Wilder)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class PsarTrend(Strategy):
    name = "psar_trend"
    category = "trend"
    description = "Parabolic SAR: Long solange der SAR unter dem Preis liegt, sonst Short."

    @staticmethod
    def default_params() -> dict:
        return {"af_start": 0.02, "af_step": 0.02, "af_max": 0.2, "adx_filter": 0}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        sar, trend = ta.psar(ohlcv, p["af_start"], p["af_step"], p["af_max"])
        direction = trend.astype(int)

        if p["adx_filter"]:
            adx = ta.adx(ohlcv, 14)
            direction = direction.where(adx >= p["adx_filter"], 0)

        atr = ta.atr(ohlcv, 14).replace(0.0, np.nan)
        conf = ((ohlcv["close"] - sar).abs() / atr).clip(0, 2).div(2).fillna(0.0)

        out["direction"] = direction
        out["confidence"] = conf
        out.loc[out["direction"] == 0, "confidence"] = 0.0
        out["reason"] = np.where(out["direction"] > 0, "SAR unter Preis (long)",
                                 np.where(out["direction"] < 0, "SAR ueber Preis (short)", ""))
        warmup = 20
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
