"""Tests fuer das Portfolio-Modul (Diversifikation, Gewichtung, Qualitaets-Huerden)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stats.portfolio import (
    QualityGates,
    build_portfolio,
    combine_equity,
    correlation_matrix,
    inverse_vol_weights,
    passes_gates,
    select_diversified,
)


def _row(score=70, n=60, exp=0.1, eff=0.8, light="green", src="live", **kw):
    return {"score": score, "n_trades": n, "expectancy_r": exp, "wf_efficiency": eff,
            "traffic_light": light, "data_source": src, "strategy": "s", "symbol": "X",
            "timeframe": "1h", "max_drawdown": 0.1, **kw}


# ---- Qualitaets-Huerden --------------------------------------------------
def test_gates_accept_good_row():
    assert passes_gates(_row(), QualityGates())


@pytest.mark.parametrize("bad", [
    {"n_trades": 5}, {"expectancy_r": -0.1}, {"wf_efficiency": 0.2},
    {"traffic_light": "red"}, {"data_source": "synthetic"},
])
def test_gates_reject_bad_rows(bad):
    assert not passes_gates(_row(**bad), QualityGates())


# ---- Korrelation & Auswahl ----------------------------------------------
def test_correlation_and_diversified_selection():
    idx = pd.date_range("2024-01-01", periods=200, freq="1h", tz="UTC")
    rng = np.random.default_rng(0)
    base = pd.Series(rng.normal(0, 1, 200), index=idx)
    returns = {
        "A": base,                                   # Referenz
        "B": base * 0.98 + rng.normal(0, 0.05, 200), # fast identisch zu A -> raus
        "C": pd.Series(rng.normal(0, 1, 200), index=idx),  # unabhaengig -> rein
    }
    corr = correlation_matrix(returns)
    assert corr.loc["A", "B"] > 0.8
    selected = select_diversified(["A", "B", "C"], corr, max_positions=6, max_correlation=0.6)
    assert "A" in selected and "C" in selected and "B" not in selected


def test_inverse_vol_weights_sum_to_one_and_favor_calm():
    idx = pd.date_range("2024-01-01", periods=200, freq="1h", tz="UTC")
    rng = np.random.default_rng(1)
    returns = {"calm": pd.Series(rng.normal(0, 0.5, 200), index=idx),
               "wild": pd.Series(rng.normal(0, 2.0, 200), index=idx)}
    w = inverse_vol_weights(returns, ["calm", "wild"])
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["calm"] > w["wild"]  # ruhigere Kombination bekommt mehr Gewicht


def test_diversification_reduces_volatility():
    """Zwei unkorrelierte Streams zusammen schwanken weniger als der volatilere allein."""
    idx = pd.date_range("2024-01-01", periods=500, freq="1h", tz="UTC")
    rng = np.random.default_rng(2)
    returns = {"A": pd.Series(rng.normal(0.0002, 0.01, 500), index=idx),
               "B": pd.Series(rng.normal(0.0002, 0.01, 500), index=idx)}
    weights = {"A": 0.5, "B": 0.5}
    eq = combine_equity(returns, weights, 10_000.0)
    port_vol = eq.pct_change().std()
    assert port_vol < returns["A"].std()  # Buendelung glaettet


# ---- Gesamtaufbau --------------------------------------------------------
def _make_inputs(n_combos=4, edge=0.1):
    idx = pd.date_range("2024-01-01", periods=400, freq="1h", tz="UTC")
    rng = np.random.default_rng(3)
    rows, returns, r_mult = {}, {}, {}
    for i in range(n_combos):
        lbl = f"strat{i} | X | 1h"
        rows[lbl] = _row(score=80 - i, strategy=f"strat{i}")
        returns[lbl] = pd.Series(rng.normal(0.0001, 0.01, 400), index=idx)
        r_mult[lbl] = rng.normal(edge, 1.0, 60)
    return rows, returns, r_mult


def test_build_validated_portfolio():
    rows, returns, r_mult = _make_inputs()
    pf = build_portfolio(rows, returns, r_mult, gates=QualityGates(), max_positions=3)
    assert pf.validated
    assert 1 <= pf.n_members <= 3
    assert abs(sum(m.weight for m in pf.members) - 1.0) < 1e-9
    assert len(pf.equity_curve) > 0
    assert "Diversifikation" in pf.diversification_note


def test_build_flags_illustrative_when_nothing_qualifies():
    """Alle Kombis unter der Huerde -> Portfolio wird als ILLUSTRATIV markiert."""
    rows, returns, r_mult = _make_inputs()
    for row in rows.values():
        row["expectancy_r"] = -0.2   # kein Vorteil -> faellt durch die Huerde
    pf = build_portfolio(rows, returns, r_mult, gates=QualityGates())
    assert not pf.validated
    assert "ILLUSTRATIV" in pf.note
    assert pf.n_members >= 1  # trotzdem zur Veranschaulichung befuellt
