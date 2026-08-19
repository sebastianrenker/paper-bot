"""1 - EMA-Crossover / Trendfolge."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class EmaCrossover(Strategy):
    name = "ema_crossover"
    category = "trend"
    description = "Schneller EMA kreuzt langsamen EMA; optionaler Trendfilter ueber Langfrist-EMA."

    @staticmethod
    def default_params() -> dict:
        return {"fast": 9, "slow": 21, "trend_filter": 200, "min_slope_atr": 0.0}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        close = ohlcv["close"]
        fast, slow = ta.ema(close, p["fast"]), ta.ema(close, p["slow"])
        spread = fast - slow
        atr = ta.atr(ohlcv).replace(0.0, np.nan)

        direction = np.sign(spread).fillna(0.0)
        if p["trend_filter"]:
            trend = ta.ema(close, p["trend_filter"])
            allowed_long = close > trend
            allowed_short = close < trend
            direction = direction.where(
                ((direction > 0) & allowed_long) | ((direction < 0) & allowed_short), 0.0
            )
        if p["min_slope_atr"] > 0:
            slope = (fast - fast.shift(1)) / atr
            direction = direction.where(slope.abs() >= p["min_slope_atr"], 0.0)

        # Konfidenz: normierter EMA-Abstand, in ATR gemessen und gedeckelt
        conf = (spread.abs() / atr).clip(0, 2) / 2

        out["direction"] = direction.fillna(0.0).astype(int)
        out["confidence"] = conf.fillna(0.0)
        out.loc[out["direction"] == 0, "confidence"] = 0.0
        out["reason"] = np.where(
            out["direction"] > 0, f"EMA{p['fast']} > EMA{p['slow']}",
            np.where(out["direction"] < 0, f"EMA{p['fast']} < EMA{p['slow']}", ""),
        )
        # Warmup-Phase neutralisieren
        warmup = max(p["slow"], p["trend_filter"] or 0)
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
