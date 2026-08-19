"""3 - Bollinger-Band-Breakout aus komprimierter Range (Squeeze) mit Volumenbestaetigung."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class BollingerBreakout(Strategy):
    name = "bollinger_breakout"
    category = "breakout"
    description = "Ausbruch aus Bollinger-Squeeze, bestaetigt durch ueberdurchschnittliches Volumen."

    @staticmethod
    def default_params() -> dict:
        return {
            "period": 20,
            "std_mult": 2.0,
            "squeeze_quantile": 0.25,
            "squeeze_lookback": 100,
            "volume_mult": 1.5,
            "hold_bars": 5,
        }

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        close = ohlcv["close"]
        lower, mid, upper = ta.bollinger(close, p["period"], p["std_mult"])
        bw = ta.bandwidth(close, p["period"], p["std_mult"])

        # Squeeze: Bandbreite im unteren Quantil der letzten N Bars (nur Vergangenheit)
        bw_threshold = bw.rolling(p["squeeze_lookback"]).quantile(p["squeeze_quantile"])
        squeeze = (bw <= bw_threshold).shift(1).fillna(False)

        vol_ok = ohlcv["volume"] > ohlcv["volume"].rolling(p["period"]).mean() * p["volume_mult"]

        breakout_up = squeeze & (close > upper) & vol_ok
        breakout_dn = squeeze & (close < lower) & vol_ok

        raw = pd.Series(0, index=ohlcv.index)
        raw[breakout_up] = 1
        raw[breakout_dn] = -1
        # Signal fuer `hold_bars` halten, damit der Trade nicht sofort wieder flat wird
        direction = raw.replace(0, np.nan).ffill(limit=max(p["hold_bars"] - 1, 0)).fillna(0).astype(int)

        vol_ratio = ohlcv["volume"] / ohlcv["volume"].rolling(p["period"]).mean().replace(0.0, np.nan)
        conf = ((vol_ratio - 1) / 2).clip(0, 1).fillna(0.0)
        conf[direction == 0] = 0.0

        out["direction"] = direction
        out["confidence"] = conf
        out["reason"] = np.where(
            direction > 0, "Squeeze-Breakout nach oben (Vol bestaetigt)",
            np.where(direction < 0, "Squeeze-Breakout nach unten (Vol bestaetigt)", ""),
        )
        warmup = p["squeeze_lookback"] + p["period"]
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
