"""Selbst-Optimierung MIT Overfitting-Waechter.

Das ist die ehrliche Umsetzung von "der Bot verbessert sich selbst":

  1. Fuer eine Strategie/Markt/Timeframe-Kombination wird das Parameter-Grid per
     WALK-FORWARD getestet - Auswahl auf Trainings-, Bewertung auf Out-of-Sample-Fenstern.
  2. Ein Parametersatz wird NUR uebernommen, wenn er drei Huerden besteht:
       - positiver Out-of-Sample-Erwartungswert (echter Vorsprung, nicht nur In-Sample)
       - Walk-Forward-Effizienz >= Schwelle (OOS haelt mit In-Sample mit -> kein Overfit)
       - genug Out-of-Sample-Trades (statistische Aussagekraft)
  3. Besteht kein Kandidat die Huerden, bleibt es bei den Defaults und die Verbesserung
     wird ABGELEHNT. Ablehnen ist hier der Normalfall und ein Feature, kein Fehler.

Wiederholtes Optimieren auf DENSELBEN Daten macht nicht besser, sondern nur
overfitteter. Echte Verbesserung entsteht dadurch, dass diese Funktion ueber die
ZEIT mit stetig neuen Marktdaten neu laeuft und nur das uebernimmt, was den
Out-of-Sample-Test immer wieder besteht.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

from backtest.engine import BacktestConfig
from backtest.walkforward import param_grid, walk_forward
from strategies.base import Strategy

log = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    strategy: str
    symbol: str
    timeframe: str
    accepted: bool
    chosen_params: dict
    default_params: dict
    oos_expectancy_r: float
    oos_trades: int
    wf_efficiency: float
    candidates_tested: int
    verdict: str
    ranking: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def optimize_strategy(
    strategy_cls: type[Strategy],
    ohlcv: pd.DataFrame,
    grid: dict[str, Iterable],
    *,
    base_params: dict | None = None,
    config: BacktestConfig | None = None,
    market: str = "crypto",
    symbol: str = "UNKNOWN",
    timeframe: str = "1h",
    train_bars: int = 500,
    test_bars: int = 125,
    min_oos_trades: int = 30,
    min_efficiency: float = 0.5,
    min_expectancy: float = 0.0,
) -> OptimizationResult:
    defaults = strategy_cls.default_params()
    candidates = param_grid(grid) if grid else [{}]
    ranking: list[dict] = []

    for params in candidates:
        full = dict(base_params or {}, **params)
        wf = walk_forward(
            strategy_cls, ohlcv, train_bars=train_bars, test_bars=test_bars,
            base_params=full, config=config, market=market, symbol=symbol, timeframe=timeframe,
        )
        m = wf.oos_metrics
        # Kombiniertes, ehrliches Kriterium: OOS-Erwartungswert, gedaempft durch
        # Stichprobengroesse und bestraft, wenn OOS deutlich schlechter als IS ist.
        eff = wf.efficiency
        score = m.expectancy_r * np.sqrt(max(m.n_trades, 0)) * float(np.clip(eff, 0.0, 1.5))
        ranking.append({
            "params": full, "oos_expectancy_r": m.expectancy_r, "oos_trades": m.n_trades,
            "wf_efficiency": eff, "max_drawdown": m.max_drawdown, "selection_score": score,
        })

    ranking.sort(key=lambda r: r["selection_score"], reverse=True)
    best = ranking[0]

    accepted = (
        best["oos_expectancy_r"] > min_expectancy
        and best["oos_trades"] >= min_oos_trades
        and best["wf_efficiency"] >= min_efficiency
    )
    if accepted:
        verdict = (
            f"UEBERNOMMEN: OOS-Erwartung {best['oos_expectancy_r']:+.3f} R ueber "
            f"{best['oos_trades']} Trades, Effizienz {best['wf_efficiency']:.2f}."
        )
        chosen = best["params"]
    else:
        reasons = []
        if best["oos_expectancy_r"] <= min_expectancy:
            reasons.append(f"OOS-Erwartung {best['oos_expectancy_r']:+.3f} R nicht positiv")
        if best["oos_trades"] < min_oos_trades:
            reasons.append(f"nur {best['oos_trades']} OOS-Trades (< {min_oos_trades})")
        if best["wf_efficiency"] < min_efficiency:
            reasons.append(f"Effizienz {best['wf_efficiency']:.2f} < {min_efficiency} (Overfit-Verdacht)")
        verdict = "ABGELEHNT (Defaults bleiben): " + "; ".join(reasons) + "."
        chosen = dict(defaults)

    return OptimizationResult(
        strategy=strategy_cls.name, symbol=symbol, timeframe=timeframe,
        accepted=accepted, chosen_params=chosen, default_params=dict(defaults),
        oos_expectancy_r=best["oos_expectancy_r"], oos_trades=best["oos_trades"],
        wf_efficiency=best["wf_efficiency"], candidates_tested=len(candidates),
        verdict=verdict, ranking=ranking[:10],
    )
