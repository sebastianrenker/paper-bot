"""16 - CCI Mean-Reversion.

Recherche-Basis (quantifiedstrategies.com, ~43k Testtrades): CCI < -100 ueberverkauft,
> +100 ueberkauft. Einstieg bei Rueckkehr aus dem Extrem (Kreuzung der Schwelle).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class CciReversion(Strategy):
    name = "cci_reversion"
    category = "mean_reversion"
    description = "Einstieg, wenn der CCI aus dem Extrem (< -100 / > +100) zurueckkehrt."

    @staticmethod
    def default_params() -> dict:
        return {"period": 20, "lower": -100, "upper": 100}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        c = ta.cci(ohlcv, p["period"])

        # Rueckkehr aus dem Extrem: vorher jenseits der Schwelle, jetzt zurueck darueber/darunter
        long_sig = (c > p["lower"]) & (c.shift(1) <= p["lower"])
        short_sig = (c < p["upper"]) & (c.shift(1) >= p["upper"])

        direction = pd.Series(0, index=ohlcv.index)
        direction[long_sig] = 1
        direction[short_sig] = -1

        conf = (c.shift(1).abs() / 200).clip(0, 1)
        conf[direction == 0] = 0.0

        out["direction"] = direction
        out["confidence"] = conf.fillna(0.0)
        out["reason"] = np.where(direction > 0, "CCI kehrt aus Ueberverkauft zurueck",
                                 np.where(direction < 0, "CCI kehrt aus Ueberkauft zurueck", ""))
        warmup = p["period"] * 3
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
