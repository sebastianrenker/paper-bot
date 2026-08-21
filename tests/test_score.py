"""Tests fuer Regime-Erkennung und den "Funktioniert gerade"-Score."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.montecarlo import monte_carlo
from backtest.walkforward import WalkForwardResult, WalkForwardWindow
from stats.metrics import Metrics
from stats.regime import detect_regime
from stats.score import compute_score


def _wf(expectancy: float, n_trades: int, n_windows: int = 4) -> WalkForwardResult:
    wf = WalkForwardResult(strategy="s", symbol="X", timeframe="1h")
    per_window = max(n_trades // n_windows, 1)
    for _ in range(n_windows):
        m = Metrics(n_trades=per_window, expectancy_r=expectancy, avg_r=expectancy)
        wf.windows.append(WalkForwardWindow(
            train_start=pd.Timestamp("2024-01-01"), train_end=pd.Timestamp("2024-02-01"),
            test_start=pd.Timestamp("2024-02-02"), test_end=pd.Timestamp("2024-03-01"),
            chosen_params={}, train_metrics=m, test_metrics=m,
        ))
    wf.oos_metrics = Metrics(n_trades=n_trades, expectancy_r=expectancy, avg_r=expectancy)
    wf.oos_r_multiples = np.full(n_trades, expectancy)
    return wf


def test_detect_regime_labels_trend(trending_up):
    regime = detect_regime(trending_up)
    assert regime.label == "trend"
    assert regime.trend_strength > 0.5


def test_regime_fit_prefers_matching_category(trending_up):
    regime = detect_regime(trending_up)
    assert regime.fit_for("trend") > regime.fit_for("mean_reversion")


def test_score_is_higher_for_better_edge(ohlcv):
    regime = detect_regime(ohlcv)
    good = compute_score(_wf(0.25, 120), monte_carlo(np.full(120, 0.25), n_runs=300), regime, "trend")
    bad = compute_score(_wf(-0.1, 120), monte_carlo(np.full(120, -0.1), n_runs=300), regime, "trend")
    assert good.total > bad.total
    assert bad.traffic_light == "red"


def test_score_penalized_by_small_sample(ohlcv):
    regime = detect_regime(ohlcv)
    many = compute_score(_wf(0.25, 120), monte_carlo(np.full(120, 0.25), n_runs=300), regime, "trend")
    few = compute_score(_wf(0.25, 5), monte_carlo(np.full(5, 0.25), n_runs=300), regime, "trend")
    assert few.total < many.total
    assert few.confidence_factor < 1.0
    assert any("Out-of-Sample-Trades" in w for w in few.warnings)


def test_score_components_are_transparent(ohlcv):
    regime = detect_regime(ohlcv)
    s = compute_score(_wf(0.2, 100), monte_carlo(np.full(100, 0.2), n_runs=300), regime, "trend")
    assert set(s.components) == {"edge", "robustness", "regime", "recency"}
    assert all(0.0 <= v <= 1.0 for v in s.components.values())
    assert 0.0 <= s.total <= 100.0
    assert "Score" in s.explanation and "Konfidenz" in s.explanation


def test_score_bounds(ohlcv):
    regime = detect_regime(ohlcv)
    extreme = compute_score(_wf(10.0, 500), monte_carlo(np.full(500, 10.0), n_runs=300), regime, "trend")
    assert extreme.total <= 100.0


def test_walk_forward_efficiency_warning(ohlcv):
    """Starke In-Sample-Performance bei schwachem OOS muss als Overfitting gewarnt werden."""
    wf = _wf(0.02, 100)
    for w in wf.windows:
        w.train_metrics = Metrics(n_trades=50, expectancy_r=0.5, avg_r=0.5)
    assert wf.efficiency < 0.5
    s = compute_score(wf, monte_carlo(np.full(100, 0.02), n_runs=300), detect_regime(ohlcv), "trend")
    assert any("Overfitting" in w for w in s.warnings)


# ---- Ehrlichere Bewertung: Trial-Abschlag + Kosten-Robustheit ------------
def test_deflated_trial_factor_monotonic():
    from stats.score import deflated_trial_factor
    assert deflated_trial_factor(1) == 1.0
    assert deflated_trial_factor(10) < 1.0
    assert deflated_trial_factor(100) < deflated_trial_factor(10)
    assert deflated_trial_factor(10_000) >= 0.4  # Boden


def test_score_trial_penalty_reduces_total(ohlcv):
    regime = detect_regime(ohlcv)
    wf = _wf(0.2, 120)
    mc = monte_carlo(np.full(120, 0.2), n_runs=300)
    one = compute_score(wf, mc, regime, "trend", trials=1)
    many = compute_score(wf, mc, regime, "trend", trials=500)
    assert many.total < one.total
    assert many.trial_factor < 1.0
    assert any("Varianten getestet" in w for w in many.warnings)


def test_score_cost_robustness_penalty(ohlcv):
    regime = detect_regime(ohlcv)
    wf = _wf(0.2, 120)
    mc = monte_carlo(np.full(120, 0.2), n_runs=300)
    robust = compute_score(wf, mc, regime, "trend", cost_robust=True)
    fragile = compute_score(wf, mc, regime, "trend", cost_robust=False)
    assert fragile.total < robust.total
    assert any("DOPPELTEN Kosten" in w for w in fragile.warnings)


def test_score_backward_compatible_defaults(ohlcv):
    """Ohne die neuen Argumente aendert sich der Score nicht (trials=1, cost None)."""
    regime = detect_regime(ohlcv)
    wf = _wf(0.2, 120)
    mc = monte_carlo(np.full(120, 0.2), n_runs=300)
    s = compute_score(wf, mc, regime, "trend")
    assert s.trial_factor == 1.0 and s.cost_robust is None
