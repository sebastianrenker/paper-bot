"""Tests fuer die Idiotensicherheit: Konfig-Validierung, Selbsttest, Absturzschutz."""
from __future__ import annotations

import pandas as pd

import cli
from config.settings import Settings
from data.loader import synthetic_ohlcv, MarketSpec


def test_validate_accepts_good_config():
    s = Settings(raw={
        "mode": "paper", "capital": {"initial": 10_000},
        "risk": {"risk_per_trade": 0.01}, "data": {"require_real": True},
        "universe": {"crypto": {"symbols": ["BTC/USDT"], "timeframes": ["1h"]}},
    })
    assert s.validate() == []


def test_validate_flags_bad_mode_and_capital():
    s = Settings(raw={"mode": "hopp", "capital": {"initial": -5},
                      "universe": {"crypto": {"symbols": ["BTC/USDT"], "timeframes": ["1h"]}}})
    problems = s.validate()
    assert any("mode" in p for p in problems)
    assert any("capital" in p for p in problems)


def test_validate_flags_empty_universe():
    s = Settings(raw={"mode": "paper", "capital": {"initial": 10_000}, "universe": {}})
    problems = s.validate()
    assert problems and any("universe" in p for p in problems)


def test_validate_flags_absurd_risk():
    s = Settings(raw={"mode": "paper", "capital": {"initial": 10_000},
                      "risk": {"risk_per_trade": 0.9},  # 90% pro Trade -> unvertretbar
                      "universe": {"crypto": {"symbols": ["X"], "timeframes": ["1h"]}}})
    assert any("Risikolimits" in p for p in s.validate())


def test_doctor_runs_without_network(tmp_path, monkeypatch, capsys):
    """Selbsttest laeuft durch und meldet 'Alles bereit', Netzwerk gemockt."""
    from data.loader import DataLoader

    monkeypatch.setattr(DataLoader, "load",
                        lambda self, spec, bars=50, refresh=False: synthetic_ohlcv(bars, spec))

    class Args:
        config = None
    from config.settings import DEFAULT_CONFIG_PATH
    Args.config = DEFAULT_CONFIG_PATH
    rc = cli.cmd_doctor(Args())
    out = capsys.readouterr().out
    assert "SELBSTTEST" in out
    assert "Live-Handel ist GESPERRT" in out
    assert rc == 0


def test_main_never_raises_stacktrace(monkeypatch):
    """Idiotensicher: ein Fehler in einem Befehl fuehrt zu freundlicher Meldung + Code 1,
    nie zu einem rohen Stacktrace."""
    def boom(args):
        raise ValueError("kaputt")

    monkeypatch.setattr(cli, "cmd_doctor", boom)
    rc = cli.main(["doctor"])
    assert rc == 1  # sauber abgefangen, kein Crash
