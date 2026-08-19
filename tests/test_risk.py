"""Tests fuer Risikomanagement, Circuit Breaker, Kill-Switch und das Live-Gate.

Diese Tests sind die Absicherung der Akzeptanzkriterien: der Circuit Breaker muss
NACHWEISLICH auf Paper zurueckschalten, und Live darf ohne Bestaetigungsflow nicht
erreichbar sein.
"""
from __future__ import annotations

import pytest

from execution import gate
from execution.gate import CONFIRM_PHRASE, LiveGateError, unlock_live
from execution.live import CcxtLiveBroker, LiveTradingNotEnabled
from risk.manager import Mode, RiskLimits, RiskManager


@pytest.fixture(autouse=True)
def locked_gate():
    gate.lock()
    yield
    gate.lock()


@pytest.fixture
def risk() -> RiskManager:
    return RiskManager(RiskLimits(risk_per_trade=0.01, max_open_positions=2,
                                  max_daily_loss=0.03, max_total_drawdown=0.15),
                       initial_equity=10_000.0, mode=Mode.PAPER)


# ---- Limits --------------------------------------------------------------
def test_invalid_limits_rejected():
    with pytest.raises(ValueError):
        RiskLimits(risk_per_trade=0.2)
    with pytest.raises(ValueError):
        RiskLimits(max_open_positions=0)


def test_position_size_follows_risk_per_trade(risk):
    # 1 % von 10.000 = 100 Risiko; Stopabstand 5 -> 20 Einheiten
    assert risk.position_size(entry_price=100.0, stop_loss=95.0) == pytest.approx(20.0)


def test_position_size_capped_by_notional_limit(risk):
    # winziger Stop -> riesige Menge, muss auf 25 % Notional gedeckelt werden
    qty = risk.position_size(entry_price=100.0, stop_loss=99.99)
    assert qty * 100.0 <= 10_000.0 * risk.limits.max_position_notional_pct + 1e-6


def test_order_without_stop_loss_denied(risk):
    d = risk.check_order(symbol="BTC", qty=1, entry_price=100, stop_loss=None, open_positions=0)
    assert not d.allowed and "Stop-Loss" in d.reason


def test_max_open_positions_enforced(risk):
    d = risk.check_order(symbol="BTC", qty=1, entry_price=100, stop_loss=95, open_positions=2)
    assert not d.allowed and "offener Positionen" in d.reason


def test_oversized_order_is_reduced_not_rejected(risk):
    d = risk.check_order(symbol="BTC", qty=10_000, entry_price=100, stop_loss=95, open_positions=0)
    assert d.allowed
    assert d.adjusted_qty == pytest.approx(20.0)


# ---- Circuit Breaker (Akzeptanzkriterium) --------------------------------
def test_circuit_breaker_switches_live_back_to_paper():
    changes = []
    risk = RiskManager(RiskLimits(max_daily_loss=0.03), 10_000.0, mode=Mode.LIVE,
                       on_mode_change=lambda mode, reason: changes.append((mode, reason)))
    assert risk.mode == Mode.LIVE

    risk.update_equity(9_800.0)      # -2 %: noch innerhalb des Limits
    assert risk.mode == Mode.LIVE and not risk.state.tripped

    risk.update_equity(9_650.0)      # -3.5 %: Limit gerissen
    assert risk.state.tripped
    assert risk.mode == Mode.PAPER, "Circuit Breaker muss auf Paper zurueckschalten"
    assert changes and changes[0][0] == Mode.PAPER


def test_circuit_breaker_blocks_further_orders(risk):
    risk.update_equity(9_600.0)
    assert risk.state.tripped
    d = risk.check_order(symbol="BTC", qty=1, entry_price=100, stop_loss=95, open_positions=0)
    assert not d.allowed and "Circuit Breaker" in d.reason


def test_total_drawdown_triggers_breaker():
    risk = RiskManager(RiskLimits(max_daily_loss=0.5, max_total_drawdown=0.15), 10_000.0)
    risk.update_equity(12_000.0)
    risk.update_equity(10_000.0)     # -16.7 % vom Peak
    assert risk.state.tripped and "Gesamtdrawdown" in risk.state.trip_reason


def test_kill_switch(risk):
    risk.kill_switch("Test")
    assert risk.state.tripped
    assert not risk.check_order(symbol="X", qty=1, entry_price=10, stop_loss=9, open_positions=0).allowed


def test_reset_requires_explicit_confirmation(risk):
    risk.kill_switch("Test")
    with pytest.raises(PermissionError):
        risk.reset()
    risk.reset(confirm=True)
    assert not risk.state.tripped


def test_audit_log_records_decisions(risk):
    risk.check_order(symbol="BTC", qty=1, entry_price=100, stop_loss=95, open_positions=0)
    risk.check_order(symbol="BTC", qty=1, entry_price=100, stop_loss=None, open_positions=0)
    events = [e["event"] for e in risk.audit]
    assert "order_allowed" in events and "order_denied" in events


# ---- Live-Gate -----------------------------------------------------------
def test_gate_locked_by_default():
    assert not gate.is_unlocked()
    with pytest.raises(LiveGateError):
        gate.assert_live_unlocked("irgendein-token")


def test_live_broker_cannot_be_created_while_locked():
    with pytest.raises(LiveGateError):
        CcxtLiveBroker("crypto", {}, gate_token=None)


def test_unlock_requires_config_mode_live():
    with pytest.raises(LiveGateError, match="config.yaml"):
        unlock_live("crypto", config_mode="paper", confirm_phrase=CONFIRM_PHRASE,
                    expected_code="ABC", entered_code="ABC", risk_checks_passed=True)


def test_unlock_requires_exact_phrase(monkeypatch):
    monkeypatch.setenv("EXCHANGE_API_KEY", "k")
    monkeypatch.setenv("EXCHANGE_API_SECRET", "s")
    with pytest.raises(LiveGateError, match="Bestaetigungssatz"):
        unlock_live("crypto", config_mode="live", confirm_phrase="ja ok",
                    expected_code="ABC", entered_code="ABC", risk_checks_passed=True)


def test_unlock_requires_correct_code(monkeypatch):
    monkeypatch.setenv("EXCHANGE_API_KEY", "k")
    monkeypatch.setenv("EXCHANGE_API_SECRET", "s")
    with pytest.raises(LiveGateError, match="Bestaetigungscode"):
        unlock_live("crypto", config_mode="live", confirm_phrase=CONFIRM_PHRASE,
                    expected_code="ABC", entered_code="XYZ", risk_checks_passed=True)


def test_unlock_requires_credentials(monkeypatch):
    monkeypatch.delenv("EXCHANGE_API_KEY", raising=False)
    monkeypatch.delenv("EXCHANGE_API_SECRET", raising=False)
    with pytest.raises(LiveGateError, match="API-Keys"):
        unlock_live("crypto", config_mode="live", confirm_phrase=CONFIRM_PHRASE,
                    expected_code="ABC", entered_code="ABC", risk_checks_passed=True)


def test_full_unlock_then_live_adapter_still_not_implemented(monkeypatch):
    monkeypatch.setenv("EXCHANGE_API_KEY", "k")
    monkeypatch.setenv("EXCHANGE_API_SECRET", "s")
    token = unlock_live("crypto", config_mode="live", confirm_phrase=CONFIRM_PHRASE,
                        expected_code="ABC", entered_code="ABC", risk_checks_passed=True)
    assert gate.is_unlocked()
    # Selbst nach Freischaltung existiert kein Live-Order-Pfad (Plan-Schritt 7.7)
    with pytest.raises(LiveTradingNotEnabled):
        CcxtLiveBroker("crypto", {"key": "k"}, gate_token=token)


def test_effective_mode_falls_back_to_paper_without_gate(tmp_path):
    from config.settings import Settings

    settings = Settings(raw={"mode": "live"})
    assert settings.configured_mode == Mode.LIVE
    assert settings.effective_mode == Mode.PAPER, "Config allein darf Live nie aktivieren"
