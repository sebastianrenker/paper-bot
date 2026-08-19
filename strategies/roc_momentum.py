"""17 - Rate-of-Change / Time-Series-Momentum.

Klassisches Time-Series-Momentum (u.a. AQR): ist der Preis ueber N Perioden gestiegen,
tendiert er weiter zu steigen. Long bei ROC > Schwelle, Short bei ROC < -Schwelle.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class RocMomentum(Strategy):
    name = "roc_momentum"
    category = "momentum"
    description = "Time-Series-Momentum: Vorzeichen und Staerke der N-Perioden-Rendite."

    @staticmethod
    def default_params() -> dict:
        return {"period": 20, "threshold": 2.0, "smooth": 3}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        r = ta.roc(ohlcv["close"], p["period"])
        if p["smooth"] > 1:
            r = r.rolling(p["smooth"]).mean()

        direction = pd.Series(0, index=ohlcv.index)
        direction[r > p["threshold"]] = 1
        direction[r < -p["threshold"]] = -1

        conf = ((r.abs() - p["threshold"]) / (p["threshold"] * 3)).clip(0, 1).fillna(0.0)
        conf[direction == 0] = 0.0

        out["direction"] = direction
        out["confidence"] = conf
        out["reason"] = np.where(direction > 0, f"{p['period']}-Perioden-Momentum positiv",
                                 np.where(direction < 0, f"{p['period']}-Perioden-Momentum negativ", ""))
        warmup = (p["period"] + p["smooth"]) * 2
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
