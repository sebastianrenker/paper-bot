"""Grossangelegter Monte-Carlo-Stresstest (Millionen simulierter Trades).

Speichersparend in Bloecken gerechnet, damit auch 1..10 Mio. Pfade auf einem
normalen Rechner laufen.

EHRLICHE EINORDNUNG: Millionen Simulationen BEWEISEN NICHT, dass eine Strategie in
Zukunft funktioniert. Sie ziehen lediglich sehr viele Stichproben aus der bereits
beobachteten Trade-Verteilung und zeigen damit, wie breit die moeglichen Ergebnisse
streuen - insbesondere die schlechten. Der Nutzen ist Risiko-Quantifizierung
(Worst-Case-Drawdown, Ruin-Wahrscheinlichkeit), nicht Gewinn-"Beweis".
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class StressResult:
    total_paths: int
    trades_per_path: int
    simulated_trades: int
    prob_profitable: float
    median_return: float
    return_p05: float
    return_p95: float
    worst_return: float
    median_max_drawdown: float
    drawdown_p95: float
    worst_drawdown: float
    prob_ruin: float
    ruin_threshold: float
    reliable: bool
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def stress_test(
    r_multiples: np.ndarray | list[float],
    *,
    risk_per_trade: float = 0.01,
    total_paths: int = 1_000_000,
    trades_per_path: int | None = None,
    ruin_threshold: float = 0.5,
    block: int = 20_000,
    seed: int = 11,
    min_trades: int = 30,
    progress=None,
) -> StressResult:
    r = np.asarray([x for x in r_multiples if np.isfinite(x)], dtype=float)
    n = r.size
    if n == 0:
        return StressResult(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, ruin_threshold, False,
                            "Keine Trades - keine Aussage moeglich.")
    horizon = trades_per_path or max(n, 100)
    rng = np.random.default_rng(seed)

    finals: list[np.ndarray] = []
    max_dds: list[np.ndarray] = []
    done = 0
    while done < total_paths:
        size = min(block, total_paths - done)
        draws = rng.choice(r, size=(size, horizon), replace=True)
        growth = np.clip(1.0 + draws * risk_per_trade, 1e-6, None)
        equity = np.cumprod(growth, axis=1)
        finals.append(equity[:, -1])
        running_max = np.maximum.accumulate(equity, axis=1)
        max_dds.append((1.0 - equity / running_max).max(axis=1))
        done += size
        if progress:
            progress(done, total_paths)

    final = np.concatenate(finals)
    max_dd = np.concatenate(max_dds)
    return StressResult(
        total_paths=int(final.size), trades_per_path=horizon,
        simulated_trades=int(final.size) * horizon,
        prob_profitable=float((final > 1.0).mean()),
        median_return=float(np.median(final) - 1.0),
        return_p05=float(np.percentile(final, 5) - 1.0),
        return_p95=float(np.percentile(final, 95) - 1.0),
        worst_return=float(final.min() - 1.0),
        median_max_drawdown=float(np.median(max_dd)),
        drawdown_p95=float(np.percentile(max_dd, 95)),
        worst_drawdown=float(max_dd.max()),
        prob_ruin=float((max_dd >= ruin_threshold).mean()),
        ruin_threshold=ruin_threshold,
        reliable=n >= min_trades,
        note="" if n >= min_trades else f"Nur {n} reale Trades als Basis - Verteilung unsicher.",
    )
