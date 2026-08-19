"""12 - DMI/ADX-Trendfolge: +DI/-DI-Kreuzung mit ADX-Bestaetigung."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class DmiTrend(Strategy):
    name = "dmi_trend"
    category = "trend"
    description = "+DI ueber -DI (bzw. umgekehrt), nur wenn ADX ausreichend hoch ist."

    @staticmethod
    def default_params() -> dict:
        return {"period": 14, "adx_min": 25}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        plus_di, minus_di, adx = ta.dmi(ohlcv, p["period"])
        strong = adx >= p["adx_min"]

        direction = pd.Series(0, index=ohlcv.index)
        direction[strong & (plus_di > minus_di)] = 1
        direction[strong & (minus_di > plus_di)] = -1

        spread = (plus_di - minus_di).abs()
        conf = (spread / 40).clip(0, 1).fillna(0.0)
        conf[direction == 0] = 0.0

        out["direction"] = direction
        out["confidence"] = conf
        out["reason"] = np.where(direction > 0, "+DI > -DI, ADX bestaetigt",
                                 np.where(direction < 0, "-DI > +DI, ADX bestaetigt", ""))
        warmup = p["period"] * 3
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
