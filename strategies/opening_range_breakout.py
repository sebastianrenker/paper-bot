"""6 - Opening-Range-Breakout (ORB)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.indicators import session_periods
from strategies.base import Strategy


class OpeningRangeBreakout(Strategy):
    name = "opening_range_breakout"
    category = "breakout"
    timeframes = ["5m", "15m", "30m", "1h"]
    description = "Ausbruch aus der High/Low-Range der ersten N Bars einer Handelssession."

    @staticmethod
    def default_params() -> dict:
        return {"range_bars": 4, "session": "D", "hold_until_session_end": True}

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        n = p["range_bars"]
        session = session_periods(ohlcv.index, p["session"])

        bar_in_session = ohlcv.groupby(session).cumcount()
        # Range der ersten n Bars, erst ab Bar n verfuegbar (kein Look-ahead)
        opening_high = ohlcv["high"].where(bar_in_session < n).groupby(session).cummax()
        opening_low = ohlcv["low"].where(bar_in_session < n).groupby(session).cummin()
        or_high = opening_high.groupby(session).ffill()
        or_low = opening_low.groupby(session).ffill()

        tradable = bar_in_session >= n
        close = ohlcv["close"]

        raw = pd.Series(0, index=ohlcv.index)
        raw[tradable & (close > or_high)] = 1
        raw[tradable & (close < or_low)] = -1

        if p["hold_until_session_end"]:
            direction = raw.replace(0, np.nan).groupby(session).ffill().fillna(0).astype(int)
        else:
            direction = raw

        rng = (or_high - or_low).replace(0.0, np.nan)
        excess = np.where(direction > 0, close - or_high, np.where(direction < 0, or_low - close, 0.0))
        conf = pd.Series(excess, index=ohlcv.index).div(rng).clip(0, 1).fillna(0.0)
        conf[direction == 0] = 0.0

        out["direction"] = direction
        out["confidence"] = conf
        out["reason"] = np.where(
            direction > 0, "Ausbruch ueber Opening Range",
            np.where(direction < 0, "Ausbruch unter Opening Range", ""),
        )
        return out
