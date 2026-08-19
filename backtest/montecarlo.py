"""Monte-Carlo-Simulation ueber die Trade-Verteilung.

Ein einzelner Backtest ist genau EIN Pfad aus einer Verteilung moeglicher Pfade.
Durch Resampling der Trade-Reihenfolge (und optional Bootstrap mit Zuruecklegen)
entsteht ein Konfidenzintervall - das ist die Grundlage der im Dashboard gezeigten
"Wahrscheinlichkeit", nicht ein einzelner Backtest-Lauf.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class MonteCarloResult:
    n_runs: int
    n_trades: int
    prob_profitable: float          # Anteil der Pfade mit Endkapital > Startkapital
    median_return: float
    return_ci_low: float            # 5. Perzentil
    return_ci_high: float           # 95. Perzentil
    median_max_drawdown: float
    drawdown_ci_high: float         # 95. Perzentil des Drawdowns (Worst Case)
    prob_ruin: float                # Anteil Pfade mit Drawdown >= ruin_threshold
    ruin_threshold: float
    reliable: bool                  # False, wenn zu wenige Trades fuer eine Aussage
    note: str = ""

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def monte_carlo(
    r_multiples: np.ndarray | list[float],
    *,
    risk_per_trade: float = 0.01,
    n_runs: int = 1_000,
    bootstrap: bool = True,
    ruin_threshold: float = 0.5,
    seed: int = 7,
    min_trades: int = 30,
) -> MonteCarloResult:
    r = np.asarray([x for x in r_multiples if np.isfinite(x)], dtype=float)
    n = r.size
    if n == 0:
        return MonteCarloResult(
            n_runs=0, n_trades=0, prob_profitable=0.0, median_return=0.0,
            return_ci_low=0.0, return_ci_high=0.0, median_max_drawdown=0.0,
            drawdown_ci_high=0.0, prob_ruin=0.0, ruin_threshold=ruin_threshold,
            reliable=False, note="Keine Trades - keine Aussage moeglich.",
        )

    rng = np.random.default_rng(seed)
    if bootstrap:
        # Ziehen mit Zuruecklegen: beruecksichtigt auch, dass die beobachtete
        # Trade-Verteilung selbst nur eine Stichprobe ist.
        paths = rng.choice(r, size=(n_runs, n), replace=True)
    else:
        paths = np.array([rng.permutation(r) for _ in range(n_runs)])

    # Multiplikatives Kapitalwachstum: jeder Trade riskiert `risk_per_trade` der Equity.
    growth = 1.0 + paths * risk_per_trade
    growth = np.clip(growth, 1e-6, None)  # Totalverlust je Trade abfangen
    equity = np.cumprod(growth, axis=1)

    final = equity[:, -1]
    running_max = np.maximum.accumulate(equity, axis=1)
    drawdowns = 1.0 - equity / running_max
    max_dd = drawdowns.max(axis=1)

    reliable = n >= min_trades
    return MonteCarloResult(
        n_runs=n_runs,
        n_trades=n,
        prob_profitable=float((final > 1.0).mean()),
        median_return=float(np.median(final) - 1.0),
        return_ci_low=float(np.percentile(final, 5) - 1.0),
        return_ci_high=float(np.percentile(final, 95) - 1.0),
        median_max_drawdown=float(np.median(max_dd)),
        drawdown_ci_high=float(np.percentile(max_dd, 95)),
        prob_ruin=float((max_dd >= ruin_threshold).mean()),
        ruin_threshold=ruin_threshold,
        reliable=reliable,
        note="" if reliable else (
            f"Nur {n} Trades - das Konfidenzintervall ist sehr breit und die "
            "Schaetzung unzuverlaessig."
        ),
    )
