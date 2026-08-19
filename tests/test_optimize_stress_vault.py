"""Tests fuer Selbst-Optimierung (mit Overfitting-Waechter), Millionen-Stresstest
und den verschluesselten Schluessel-Tresor."""
from __future__ import annotations

import numpy as np
import pytest

from backtest.optimize import optimize_strategy
from backtest.stress import stress_test
from strategies.ema_crossover import EmaCrossover


# ---- Stresstest ----------------------------------------------------------
def test_stress_scales_to_many_paths():
    rng = np.random.default_rng(0)
    r = rng.normal(0.05, 1.0, 120)
    res = stress_test(r, total_paths=200_000, trades_per_path=100, block=50_000)
    assert res.total_paths == 200_000
    assert res.simulated_trades == 200_000 * 100  # 20 Mio. simulierte Trades
    assert 0.0 <= res.prob_profitable <= 1.0
    assert res.return_p05 <= res.median_return <= res.return_p95
    assert 0.0 <= res.median_max_drawdown <= res.drawdown_p95 <= res.worst_drawdown <= 1.0


def test_stress_losing_distribution_flags_ruin():
    r = np.array([-1.0] * 60 + [0.4] * 40)
    res = stress_test(r, total_paths=100_000, trades_per_path=200, block=25_000, risk_per_trade=0.02)
    assert res.prob_profitable < 0.2
    assert res.prob_ruin > 0.0


def test_stress_empty():
    res = stress_test([], total_paths=1000)
    assert res.total_paths == 0 and not res.reliable


# ---- Selbst-Optimierung mit Waechter -------------------------------------
def test_optimizer_rejects_when_no_real_edge(ohlcv):
    """Auf synthetischem Rauschen darf KEIN Parametersatz uebernommen werden -
    der Overfitting-Waechter muss ablehnen und die Defaults behalten."""
    res = optimize_strategy(
        EmaCrossover, ohlcv, grid={"fast": [8, 9, 12], "slow": [21, 26]},
        base_params={"trend_filter": 0}, train_bars=400, test_bars=100,
        min_oos_trades=1000,  # kuenstlich hoch -> Ablehnung erzwungen
    )
    assert not res.accepted
    assert res.chosen_params == res.default_params
    assert "ABGELEHNT" in res.verdict


def test_optimizer_reports_ranking(ohlcv):
    res = optimize_strategy(
        EmaCrossover, ohlcv, grid={"fast": [8, 12]},
        base_params={"trend_filter": 0}, train_bars=400, test_bars=100,
    )
    assert res.candidates_tested == 2
    assert len(res.ranking) >= 1
    assert "selection_score" in res.ranking[0]


# ---- Verschluesselter Vault ----------------------------------------------
pytest.importorskip("cryptography")


def test_vault_roundtrip(tmp_path):
    from risk.vault import SecretsVault

    vault = SecretsVault(tmp_path / "s.vault")
    secrets = {"EXCHANGE_API_KEY": "abc123", "EXCHANGE_API_SECRET": "s3cr3t"}
    vault.save(secrets, "master-passwort")
    assert vault.exists()
    assert vault.load("master-passwort") == secrets


def test_vault_wrong_password_fails(tmp_path):
    from risk.vault import SecretsVault, VaultError

    vault = SecretsVault(tmp_path / "s.vault")
    vault.save({"K": "v"}, "richtiges-passwort")
    with pytest.raises(VaultError, match="Falsches Master-Passwort"):
        vault.load("falsches-passwort")


def test_vault_rejects_weak_password(tmp_path):
    from risk.vault import SecretsVault, VaultError

    with pytest.raises(VaultError, match="mindestens 8"):
        SecretsVault(tmp_path / "s.vault").save({"K": "v"}, "kurz")


def test_vault_ciphertext_contains_no_plaintext(tmp_path):
    from risk.vault import SecretsVault

    vault = SecretsVault(tmp_path / "s.vault")
    vault.save({"EXCHANGE_API_SECRET": "SUPERSECRET_TOKEN_XYZ"}, "master-passwort")
    raw = (tmp_path / "s.vault").read_bytes()
    assert b"SUPERSECRET_TOKEN_XYZ" not in raw  # Klartext darf nicht auf Platte liegen


def test_vault_load_into_env_no_overwrite(tmp_path, monkeypatch):
    from risk.vault import SecretsVault

    monkeypatch.setenv("EXCHANGE_API_KEY", "vorhanden")
    vault = SecretsVault(tmp_path / "s.vault")
    vault.save({"EXCHANGE_API_KEY": "neu", "EXCHANGE_API_SECRET": "neu2"}, "master-passwort")
    names = vault.load_into_env("master-passwort", overwrite=False)
    import os
    assert os.environ["EXCHANGE_API_KEY"] == "vorhanden"  # nicht ueberschrieben
    assert "EXCHANGE_API_SECRET" in names
