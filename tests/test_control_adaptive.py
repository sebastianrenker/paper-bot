"""Tests fuer Dashboard-Steuerung und Selbst-Anpassung des Bots."""
from __future__ import annotations

import pytest

from core.store import Store
from execution.paper import PaperBroker
from execution.paper_loop import PaperLoopConfig, PaperTradingLoop
from risk.manager import Mode, RiskLimits, RiskManager


# ---- Steuerkanal Dashboard <-> Bot ---------------------------------------
def test_control_set_get(tmp_path):
    store = Store(tmp_path / "t.db")
    assert store.get_control("desired_state", "stopped") == "stopped"  # Default
    store.set_control("desired_state", "running")
    assert store.get_control("desired_state") == "running"


def test_pop_command_is_one_shot(tmp_path):
    store = Store(tmp_path / "t.db")
    store.set_control("command", "adapt")
    assert store.pop_command() == "adapt"
    assert store.pop_command() == ""  # nach dem Lesen geloescht


# ---- Adaptive Parameter im Loop ------------------------------------------
def test_loop_prefers_validated_combo_params(tmp_path):
    store = Store(tmp_path / "t.db")
    broker = PaperBroker()
    risk = RiskManager(RiskLimits(), 10_000.0, mode=Mode.PAPER)
    loop = PaperTradingLoop(
        broker, risk, store, [],
        strategy_params={"ema_crossover": {"fast": 9}},
        combo_params={("ema_crossover", "BTC/USDT", "4h"): {"fast": 12, "slow": 50}},
    )
    # Pro-Kombination validierte Parameter haben Vorrang
    assert loop.params_for("ema_crossover", "BTC/USDT", "4h") == {"fast": 12, "slow": 50}
    # ohne Kombinations-Treffer: Rueckfall auf Strategie-Defaults
    assert loop.params_for("ema_crossover", "ETH/USDT", "1h") == {"fast": 9}
    # unbekannte Strategie: leeres Dict
    assert loop.params_for("unbekannt", "X", "1h") == {}


def test_loop_combo_params_are_live_mutable(tmp_path):
    """serve aktualisiert combo_params live -> der Bot passt sich ohne Neustart an."""
    loop = PaperTradingLoop(PaperBroker(), RiskManager(RiskLimits(), 10_000.0), Store(tmp_path / "t.db"), [])
    assert loop.params_for("supertrend", "SOL/USDT", "4h") == {}
    loop.combo_params[("supertrend", "SOL/USDT", "4h")] = {"period": 7, "mult": 2.0}
    assert loop.params_for("supertrend", "SOL/USDT", "4h") == {"period": 7, "mult": 2.0}


# ---- Gelernte Parameter laden --------------------------------------------
def test_load_learned_params_roundtrip(tmp_path):
    import yaml
    import cli
    from config.settings import Settings

    learned = {"ema_crossover": {"BTC/USDT": {"4h": {"fast": 12, "slow": 50}}}}
    (tmp_path / "learned_params.yaml").write_text(yaml.safe_dump(learned), encoding="utf-8")
    s = Settings(raw={}, path=tmp_path / "config.yaml")
    out = cli._load_learned_params(s)
    assert out == {("ema_crossover", "BTC/USDT", "4h"): {"fast": 12, "slow": 50}}


def test_load_learned_params_missing_file(tmp_path):
    import cli
    from config.settings import Settings

    s = Settings(raw={}, path=tmp_path / "config.yaml")
    assert cli._load_learned_params(s) == {}
