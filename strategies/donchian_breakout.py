"""7 - Donchian-Channel-Breakout ("Turtle-Style")."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class DonchianBreakout(Strategy):
    name = "donchian_breakout"
    category = "trend"
    description = "Ausbruch aus dem N-Perioden-Hoch/Tief, Ausstieg am kuerzeren Gegenkanal."

    @staticmethod
    def default_params() -> dict:
        return {"entry_period": 20, "exit_period": 10}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        close = ohlcv["close"]
        # shift(1): der heutige Bar darf den Kanal nicht selbst mitbilden
        entry_low, entry_high = ta.donchian(ohlcv, p["entry_period"])
        entry_low, entry_high = entry_low.shift(1), entry_high.shift(1)
        exit_low, exit_high = ta.donchian(ohlcv, p["exit_period"])
        exit_low, exit_high = exit_low.shift(1), exit_high.shift(1)

        direction = np.zeros(len(ohlcv), dtype=int)
        state = 0
        c = close.to_numpy()
        eh, el = entry_high.to_numpy(), entry_low.to_numpy()
        xh, xl = exit_high.to_numpy(), exit_low.to_numpy()
        for i in range(len(c)):
            if state == 0:
                if not np.isnan(eh[i]) and c[i] > eh[i]:
                    state = 1
                elif not np.isnan(el[i]) and c[i] < el[i]:
                    state = -1
            elif state == 1 and not np.isnan(xl[i]) and c[i] < xl[i]:
                state = 0
            elif state == -1 and not np.isnan(xh[i]) and c[i] > xh[i]:
                state = 0
            direction[i] = state

        dir_s = pd.Series(direction, index=ohlcv.index)
        atr = ta.atr(ohlcv).replace(0.0, np.nan)
        dist = np.where(dir_s > 0, c - eh, np.where(dir_s < 0, el - c, 0.0))
        conf = (pd.Series(dist, index=ohlcv.index) / atr).clip(0, 2).div(2).fillna(0.0)
        conf[dir_s == 0] = 0.0

        out["direction"] = dir_s
        out["confidence"] = conf
        out["reason"] = np.where(
            dir_s > 0, f"Ausbruch ueber {p['entry_period']}-Perioden-Hoch",
            np.where(dir_s < 0, f"Ausbruch unter {p['entry_period']}-Perioden-Tief", ""),
        )
        return out
