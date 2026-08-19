"""Der "Funktioniert gerade"-Score.

BEWUSST KEINE BLACK BOX: der Score ist eine gewichtete Summe von vier offengelegten
Komponenten, jede auf 0..1 normiert. Alle Einzelkomponenten werden mitgeliefert und
im Dashboard einzeln angezeigt, damit nachvollziehbar bleibt, warum eine Strategie
oben oder unten steht.

  edge      (35 %) - Out-of-Sample-Erwartungswert je Trade (R) aus der Walk-Forward-Analyse
  robust    (30 %) - Monte-Carlo: Wahrscheinlichkeit profitabel + Drawdown-Risiko
  regime    (20 %) - passt das aktuelle Marktregime zur Strategiekategorie?
  recency   (15 %) - Performance der juengsten OOS-Fenster gegenueber dem Durchschnitt

Der Gesamtscore wird zusaetzlich mit einem Konfidenzfaktor aus der Trade-Anzahl
multipliziert. Wenige Trades => niedriger Score, egal wie gut die Zahlen aussehen.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from backtest.montecarlo import MonteCarloResult
from backtest.walkforward import WalkForwardResult
from stats.metrics import MIN_TRADES_FOR_SIGNIFICANCE
from stats.regime import Regime

WEIGHTS = {"edge": 0.35, "robustness": 0.30, "regime": 0.20, "recency": 0.15}


@dataclass
class ScoreBreakdown:
    total: float                       # 0..100
    components: dict[str, float] = field(default_factory=dict)   # roh, 0..1
    contributions: dict[str, float] = field(default_factory=dict)  # gewichtet, 0..1
    confidence_factor: float = 0.0
    n_trades: int = 0
    traffic_light: str = "red"
    explanation: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "total": self.total, "traffic_light": self.traffic_light,
            "n_trades": self.n_trades, "confidence_factor": self.confidence_factor,
            **{f"c_{k}": v for k, v in self.components.items()},
            "explanation": self.explanation, "warnings": "; ".join(self.warnings),
        }


def compute_score(
    wf: WalkForwardResult,
    mc: MonteCarloResult,
    regime: Regime,
    strategy_category: str,
    *,
    weights: dict[str, float] | None = None,
) -> ScoreBreakdown:
    w = {**WEIGHTS, **(weights or {})}
    warnings: list[str] = []

    # --- edge: OOS-Erwartungswert je Trade, 0.3R gilt als sehr gut ----------
    expectancy = wf.oos_metrics.expectancy_r
    edge = float(np.clip((expectancy + 0.1) / 0.4, 0.0, 1.0))

    # --- robustness: MC-Profitabilitaet, bestraft durch Worst-Case-Drawdown --
    dd_penalty = float(np.clip(mc.drawdown_ci_high / 0.4, 0.0, 1.0))
    robustness = float(np.clip(mc.prob_profitable * (1.0 - 0.6 * dd_penalty), 0.0, 1.0))
    if mc.prob_ruin > 0.05:
        warnings.append(f"Monte-Carlo: {mc.prob_ruin:.0%} der Pfade mit >= {mc.ruin_threshold:.0%} Drawdown.")

    # --- regime: passt das aktuelle Marktumfeld zur Strategie? --------------
    regime_fit = regime.fit_for(strategy_category)

    # --- recency: letzte 3 OOS-Fenster gegen den Gesamtschnitt --------------
    recency = _recency(wf)

    components = {"edge": edge, "robustness": robustness, "regime": regime_fit, "recency": recency}
    contributions = {k: components[k] * w[k] for k in components}

    n = wf.oos_metrics.n_trades
    confidence_factor = float(np.clip(n / MIN_TRADES_FOR_SIGNIFICANCE, 0.0, 1.0))
    if n < MIN_TRADES_FOR_SIGNIFICANCE:
        warnings.append(
            f"Nur {n} Out-of-Sample-Trades - Score ist entsprechend abgewertet "
            f"(Faktor {confidence_factor:.2f})."
        )
    if wf.efficiency < 0.5 and wf.windows:
        warnings.append(
            f"Walk-Forward-Effizienz {wf.efficiency:.2f} < 0.5 - deutlicher Overfitting-Verdacht."
        )

    total = float(sum(contributions.values()) * confidence_factor * 100)
    if total >= 60:
        light = "green"
    elif total >= 35:
        light = "yellow"
    else:
        light = "red"

    explanation = (
        f"Score {total:.1f}/100 = ("
        + " + ".join(f"{k} {components[k]:.2f}x{w[k]:.2f}" for k in components)
        + f") x Konfidenz {confidence_factor:.2f} x 100"
    )

    return ScoreBreakdown(
        total=total, components=components, contributions=contributions,
        confidence_factor=confidence_factor, n_trades=n, traffic_light=light,
        explanation=explanation, warnings=warnings,
    )


def _recency(wf: WalkForwardResult, n_recent: int = 3) -> float:
    windows = [win for win in wf.windows if win.test_metrics.n_trades > 0]
    if not windows:
        return 0.0
    recent = [win.test_metrics.expectancy_r for win in windows[-n_recent:]]
    overall = [win.test_metrics.expectancy_r for win in windows]
    recent_mean, overall_mean = float(np.mean(recent)), float(np.mean(overall))
    # 0.5 = wie im Schnitt; >0.5 = zuletzt besser als der eigene Durchschnitt
    delta = recent_mean - overall_mean
    base = float(np.clip(0.5 + delta / 0.4, 0.0, 1.0))
    # zusaetzlich absolute Anforderung: zuletzt negativ => hoechstens 0.4
    return base if recent_mean > 0 else min(base, 0.4)
