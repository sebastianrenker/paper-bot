"""Performance-Kennzahlen.

Bewusst wird KEINE Kennzahl allein als "Erfolgswahrscheinlichkeit" ausgewiesen.
Jede Metrik traegt eine Angabe zur Stichprobengroesse, damit im Dashboard
statistisch schwache Ergebnisse gekennzeichnet werden koennen.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

# Unter dieser Trade-Zahl ist jede Auswertung statistisch nicht belastbar.
MIN_TRADES_FOR_SIGNIFICANCE = 30

PERIODS_PER_YEAR = {
    "1m": 525_600, "5m": 105_120, "15m": 35_040, "30m": 17_520,
    "1h": 8_760, "4h": 2_190, "1d": 252,
}


@dataclass
class Metrics:
    n_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    expectancy_r: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0        # als positiver Anteil, 0.25 = 25 %
    total_return: float = 0.0
    cagr: float = 0.0
    statistically_significant: bool = False
    warning: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def compute_metrics(
    r_multiples: np.ndarray | list[float],
    equity_curve: pd.Series,
    timeframe: str = "1h",
) -> Metrics:
    r = np.asarray([x for x in r_multiples if np.isfinite(x)], dtype=float)
    m = Metrics(n_trades=int(r.size))

    if r.size:
        wins, losses = r[r > 0], r[r <= 0]
        m.win_rate = float(wins.size / r.size)
        gross_win = float(wins.sum())
        gross_loss = float(-losses.sum())
        m.profit_factor = float(gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0
        m.avg_r = float(r.mean())
        m.expectancy_r = float(r.mean())  # Erwartungswert je Trade in R

    if equity_curve is not None and len(equity_curve) > 1:
        eq = equity_curve.dropna()
        rets = eq.pct_change().dropna()
        ppy = PERIODS_PER_YEAR.get(timeframe, 8_760)
        if len(rets) > 1 and rets.std(ddof=0) > 0:
            m.sharpe = float(rets.mean() / rets.std(ddof=0) * np.sqrt(ppy))
        downside = rets[rets < 0]
        if len(downside) > 1 and downside.std(ddof=0) > 0:
            m.sortino = float(rets.mean() / downside.std(ddof=0) * np.sqrt(ppy))
        m.max_drawdown = float(max_drawdown(eq))
        if eq.iloc[0] > 0:
            m.total_return = float(eq.iloc[-1] / eq.iloc[0] - 1)
            years = len(eq) / ppy
            if years > 0 and eq.iloc[-1] > 0:
                m.cagr = float((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1)

    m.statistically_significant = m.n_trades >= MIN_TRADES_FOR_SIGNIFICANCE
    if not m.statistically_significant:
        m.warning = (
            f"Nur {m.n_trades} Trades (< {MIN_TRADES_FOR_SIGNIFICANCE}) - "
            "Ergebnis ist statistisch NICHT aussagekraeftig."
        )
    return m


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = (equity - peak) / peak.replace(0.0, np.nan)
    return float(-dd.min()) if len(dd.dropna()) else 0.0


def drawdown_series(equity: pd.Series) -> pd.Series:
    peak = equity.cummax()
    return ((equity - peak) / peak.replace(0.0, np.nan)).fillna(0.0)
