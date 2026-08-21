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
    trial_factor: float = 1.0          # Abschlag fuer die Zahl getesteter Varianten
    cost_robust: bool | None = None    # ueberlebt der Vorteil doppelte Kosten?
    n_trades: int = 0
    traffic_light: str = "red"
    explanation: str = ""
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "total": self.total, "traffic_light": self.traffic_light,
            "n_trades": self.n_trades, "confidence_factor": self.confidence_factor,
            "trial_factor": self.trial_factor, "cost_robust": self.cost_robust,
            **{f"c_{k}": v for k, v in self.components.items()},
            "explanation": self.explanation, "warnings": "; ".join(self.warnings),
        }


def deflated_trial_factor(trials: int) -> float:
    """Abschlag fuer Selektions-Verzerrung (Idee der Deflated Sharpe Ratio, Bailey & de
    Prado): je mehr Varianten man durchprobiert, desto wahrscheinlicher ist der beste
    Treffer nur Zufall. Kein exakter DSR, aber ein transparenter, monoton fallender
    Haircut: 1 Versuch -> 1.0, 10 -> ~0.85, 100 -> ~0.70, 1000 -> ~0.55 (Boden 0.4)."""
    if trials <= 1:
        return 1.0
    return float(np.clip(1.0 - 0.15 * np.log10(trials), 0.4, 1.0))


def compute_score(
    wf: WalkForwardResult,
    mc: MonteCarloResult,
    regime: Regime,
    strategy_category: str,
    *,
    weights: dict[str, float] | None = None,
    trials: int = 1,
    cost_robust: bool | None = None,
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

    # --- Trial-Count-Abschlag (Overfitting/Selektions-Verzerrung) -----------
    trial_factor = deflated_trial_factor(trials)
    if trials > 1 and trial_factor < 1.0:
        warnings.append(
            f"{trials} Varianten getestet - Score um Faktor {trial_factor:.2f} abgewertet "
            f"(Selektions-Verzerrung, Deflated-Sharpe-Idee)."
        )

    # --- Kosten-Robustheit: ueberlebt der Vorteil doppelte Kosten? ----------
    cost_factor = 1.0
    if cost_robust is False:
        cost_factor = 0.5
        warnings.append(
            "Vorteil verschwindet bei DOPPELTEN Kosten (Gebuehr/Slippage x2) - fragil, "
            "vermutlich nicht robust handelbar."
        )

    total = float(sum(contributions.values()) * confidence_factor * trial_factor * cost_factor * 100)
    if total >= 60:
        light = "green"
    elif total >= 35:
        light = "yellow"
    else:
        light = "red"

    explanation = (
        f"Score {total:.1f}/100 = ("
        + " + ".join(f"{k} {components[k]:.2f}x{w[k]:.2f}" for k in components)
        + f") x Konfidenz {confidence_factor:.2f} x Trials {trial_factor:.2f} x Kosten {cost_factor:.2f} x 100"
    )

    return ScoreBreakdown(
        total=total, components=components, contributions=contributions,
        confidence_factor=confidence_factor, trial_factor=trial_factor, cost_robust=cost_robust,
        n_trades=n, traffic_light=light, explanation=explanation, warnings=warnings,
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
