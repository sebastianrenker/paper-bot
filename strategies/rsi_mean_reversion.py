"""2 - RSI Mean-Reversion mit Divergenz-Check."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class RsiMeanReversion(Strategy):
    name = "rsi_mean_reversion"
    category = "mean_reversion"
    description = "RSI im Extrembereich, bestaetigt durch bullische/baerische Divergenz."

    @staticmethod
    def default_params() -> dict:
        return {
            "period": 14,
            "oversold": 30,
            "overbought": 70,
            "divergence_lookback": 14,
            "require_divergence": True,
        }

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        close = ohlcv["close"]
        r = ta.rsi(close, p["period"])
        lb = p["divergence_lookback"]

        price_min = close.rolling(lb).min()
        price_max = close.rolling(lb).max()
        rsi_at_min = r.rolling(lb).min()
        rsi_at_max = r.rolling(lb).max()

        # Bullische Divergenz: Preis macht neues Tief, RSI nicht.
        bull_div = (close <= price_min) & (r > rsi_at_min)
        bear_div = (close >= price_max) & (r < rsi_at_max)

        long_sig = r < p["oversold"]
        short_sig = r > p["overbought"]
        if p["require_divergence"]:
            long_sig &= bull_div
            short_sig &= bear_div

        direction = pd.Series(0, index=ohlcv.index)
        direction[long_sig] = 1
        direction[short_sig] = -1

        # Konfidenz: je weiter der RSI ueber die Schwelle hinaus ist, desto hoeher
        conf_long = ((p["oversold"] - r) / p["oversold"]).clip(0, 1)
        conf_short = ((r - p["overbought"]) / (100 - p["overbought"])).clip(0, 1)
        conf = pd.Series(0.0, index=ohlcv.index)
        conf[long_sig] = conf_long[long_sig]
        conf[short_sig] = conf_short[short_sig]

        out["direction"] = direction
        out["confidence"] = conf.fillna(0.0)
        out["reason"] = np.where(
            direction > 0, "RSI oversold + bull. Divergenz",
            np.where(direction < 0, "RSI overbought + baer. Divergenz", ""),
        )
        warmup = max(p["period"], lb) * 2
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
