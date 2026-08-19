"""8 - Support/Resistance + Price Action (regelbasiert)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import indicators as ta
from strategies.base import Strategy


class SupportResistance(Strategy):
    name = "support_resistance"
    category = "structure"
    description = (
        "Erkennt bestaetigte Swing-Highs/Lows, bildet daraus Zonen und handelt die "
        "Reaktion (Rejection-Kerze) an der Zone."
    )

    @staticmethod
    def default_params() -> dict:
        return {
            "swing_left": 3,
            "swing_right": 3,
            "zone_atr_mult": 0.5,
            "wick_ratio": 1.5,
            "hold_bars": 3,
        }

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.empty_frame(ohlcv.index)
        is_low, is_high = ta.swing_points(ohlcv, p["swing_left"], p["swing_right"])
        atr = ta.atr(ohlcv)

        # letzte bestaetigte Zonen (ffill => nur Vergangenheit)
        support = ohlcv["low"].where(is_low).ffill()
        resistance = ohlcv["high"].where(is_high).ffill()
        tol = atr * p["zone_atr_mult"]

        body = (ohlcv["close"] - ohlcv["open"]).abs().replace(0.0, np.nan)
        lower_wick = ohlcv[["open", "close"]].min(axis=1) - ohlcv["low"]
        upper_wick = ohlcv["high"] - ohlcv[["open", "close"]].max(axis=1)

        at_support = (ohlcv["low"] <= support + tol) & (ohlcv["low"] >= support - tol)
        at_resistance = (ohlcv["high"] >= resistance - tol) & (ohlcv["high"] <= resistance + tol)

        bull_rejection = at_support & (lower_wick / body >= p["wick_ratio"]) & (ohlcv["close"] > ohlcv["open"])
        bear_rejection = at_resistance & (upper_wick / body >= p["wick_ratio"]) & (ohlcv["close"] < ohlcv["open"])

        raw = pd.Series(0, index=ohlcv.index)
        raw[bull_rejection.fillna(False)] = 1
        raw[bear_rejection.fillna(False)] = -1
        direction = raw.replace(0, np.nan).ffill(limit=max(p["hold_bars"] - 1, 0)).fillna(0).astype(int)

        wick_strength = np.where(direction > 0, lower_wick / body, np.where(direction < 0, upper_wick / body, 0.0))
        conf = (pd.Series(wick_strength, index=ohlcv.index) / (p["wick_ratio"] * 2)).clip(0, 1).fillna(0.0)
        conf[direction == 0] = 0.0

        out["direction"] = direction
        out["confidence"] = conf
        out["reason"] = np.where(
            direction > 0, "Bullische Rejection an Support-Zone",
            np.where(direction < 0, "Baerische Rejection an Resistance-Zone", ""),
        )
        warmup = (p["swing_left"] + p["swing_right"]) * 4
        out.iloc[:warmup, out.columns.get_loc("direction")] = 0
        out.iloc[:warmup, out.columns.get_loc("confidence")] = 0.0
        return out
