"""4 - MACD-Momentum: Linienkreuzung + Histogramm-Beschleunigung."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class MacdMomentum(Strategy):
    name = "macd_momentum"
    category = "momentum"
    description = "MACD kreuzt Signallinie, Histogramm muss in Signalrichtung beschleunigen."

    @staticmethod
    def default_params() -> dict:
        return {"fast": 12, "slow": 26, "signal": 9, "require_acceleration": True, "zero_line_filter": False}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        close = ohlcv["close"]
        macd_line, signal_line, hist = ta.macd(close, p["fast"], p["slow"], p["signal"])

        direction = np.sign(macd_line - signal_line).fillna(0.0)
        if p["require_acceleration"]:
            accel = hist.diff()
            direction = direction.where(np.sign(accel).fillna(0.0) == direction, 0.0)
        if p["zero_line_filter"]:
            direction = direction.where(np.sign(macd_line).fillna(0.0) == direction, 0.0)

        atr = ta.atr(ohlcv).replace(0.0, np.nan)
        conf = (hist.abs() / atr).clip(0, 1).fillna(0.0)

        out["direction"] = direction.fillna(0.0).astype(int)
        out["confidence"] = conf
        out.loc[out["direction"] == 0, "confidence"] = 0.0
        out["reason"] = np.where(
            out["direction"] > 0, "MACD > Signal, Histogramm steigend",
            np.where(out["direction"] < 0, "MACD < Signal, Histogramm fallend", ""),
        )
        warmup = p["slow"] + p["signal"]
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
