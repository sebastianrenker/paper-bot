"""5 - VWAP-Reversion (nur Intraday-Timeframes)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class VwapReversion(Strategy):
    name = "vwap_reversion"
    category = "mean_reversion"
    timeframes = ["5m", "15m", "30m", "1h"]  # bewusst kein Daily: VWAP ist ein Intraday-Konzept
    description = "Rueckkehr zum Session-VWAP nach Abweichung von mind. N Standardabweichungen."

    @staticmethod
    def default_params() -> dict:
        return {"deviation_period": 20, "entry_sigma": 2.0, "exit_sigma": 0.5}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        close = ohlcv["close"]
        vw = ta.vwap(ohlcv)
        dev = close - vw
        sigma = dev.rolling(p["deviation_period"]).std(ddof=0).replace(0.0, np.nan)
        z = (dev / sigma).fillna(0.0)

        direction = pd.Series(0, index=ohlcv.index)
        direction[z <= -p["entry_sigma"]] = 1    # unter VWAP -> Rueckkehr nach oben erwartet
        direction[z >= p["entry_sigma"]] = -1

        conf = ((z.abs() - p["entry_sigma"]) / p["entry_sigma"]).clip(0, 1)
        conf[direction == 0] = 0.0

        out["direction"] = direction
        out["confidence"] = conf.fillna(0.0)
        out["reason"] = np.where(
            direction > 0, "Preis > 2 Sigma unter VWAP",
            np.where(direction < 0, "Preis > 2 Sigma ueber VWAP", ""),
        )
        warmup = p["deviation_period"] * 2
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
