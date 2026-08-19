"""11 - Stochastik-Mean-Reversion mit %K/%D-Kreuzung."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class StochasticReversion(Strategy):
    name = "stochastic_reversion"
    category = "mean_reversion"
    description = "Stochastik im Extrembereich, Einstieg bei %K-Kreuzung ueber/unter %D."

    @staticmethod
    def default_params() -> dict:
        return {"k_period": 14, "d_period": 3, "smooth": 3, "oversold": 20, "overbought": 80}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        k, d = ta.stochastic(ohlcv, p["k_period"], p["d_period"], p["smooth"])
        cross_up = (k > d) & (k.shift(1) <= d.shift(1))
        cross_dn = (k < d) & (k.shift(1) >= d.shift(1))

        long_sig = cross_up & (d < p["oversold"])
        short_sig = cross_dn & (d > p["overbought"])

        direction = pd.Series(0, index=ohlcv.index)
        direction[long_sig] = 1
        direction[short_sig] = -1

        conf = pd.Series(0.0, index=ohlcv.index)
        conf[long_sig] = ((p["oversold"] - d) / p["oversold"]).clip(0, 1)[long_sig]
        conf[short_sig] = ((d - p["overbought"]) / (100 - p["overbought"])).clip(0, 1)[short_sig]

        out["direction"] = direction
        out["confidence"] = conf.fillna(0.0)
        out["reason"] = np.where(direction > 0, "Stochastik-Kreuzung aus oversold",
                                 np.where(direction < 0, "Stochastik-Kreuzung aus overbought", ""))
        warmup = (p["k_period"] + p["d_period"] + p["smooth"]) * 2
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
