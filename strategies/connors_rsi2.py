"""14 - Connors RSI-2 Mean-Reversion (Larry Connors).

Recherche-Basis (quantifiedstrategies.com, mql5): kurzer 2-Perioden-RSI faengt
kurzfristige Rueckschlaege im uebergeordneten Trend ab. Klassisch auf Aktien/Indizes
im Tageschart mit 200er-Trendfilter: nur Long, wenn Preis ueber der SMA200 und
RSI(2) im Extrem. Hier symmetrisch auch fuer Short umgesetzt.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class ConnorsRsi2(Strategy):
    name = "connors_rsi2"
    category = "mean_reversion"
    description = "RSI(2)-Extrem im Trend (Preis vs. SMA200). Klassische Connors-Mean-Reversion."

    @staticmethod
    def default_params() -> dict:
        return {"rsi_period": 2, "trend_sma": 200, "oversold": 10, "overbought": 90}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        close = ohlcv["close"]
        r = ta.rsi(close, p["rsi_period"])
        trend = ta.sma(close, p["trend_sma"])

        long_sig = (close > trend) & (r < p["oversold"])       # Rueckschlag im Aufwaertstrend kaufen
        short_sig = (close < trend) & (r > p["overbought"])    # Erholung im Abwaertstrend verkaufen

        direction = pd.Series(0, index=ohlcv.index)
        direction[long_sig] = 1
        direction[short_sig] = -1

        conf = pd.Series(0.0, index=ohlcv.index)
        conf[long_sig] = ((p["oversold"] - r) / p["oversold"]).clip(0, 1)[long_sig]
        conf[short_sig] = ((r - p["overbought"]) / (100 - p["overbought"])).clip(0, 1)[short_sig]

        out["direction"] = direction
        out["confidence"] = conf.fillna(0.0)
        out["reason"] = np.where(direction > 0, "RSI(2) oversold ueber SMA200",
                                 np.where(direction < 0, "RSI(2) overbought unter SMA200", ""))
        warmup = p["trend_sma"]
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
