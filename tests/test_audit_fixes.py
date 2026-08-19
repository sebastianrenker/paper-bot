"""Regressionstests fuer die im Audit gefundenen Bugs.

Jeder Test schlaegt OHNE den zugehoerigen Fix fehl und besteht MIT ihm.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import cli
from config.settings import Settings
from execution.paper_loop import closed_bars


# ============================================================================
# Bug 1: config.yaml-Werte (stop_atr_mult/take_profit_r/atr_period) erreichen
#        die PaperLoopConfig nicht - der Bot handelte mit Default-Stops.
# ============================================================================
def test_loop_config_uses_config_values_not_defaults():
    settings = Settings(raw={
        "capital": {"initial": 10_000},
        "risk": {"risk_per_trade": 0.02},
        # bewusst von den PaperLoopConfig-Defaults (2.0/2.0/14) abweichende Werte:
        "backtest": {"stop_atr_mult": 3.5, "take_profit_r": 1.5, "atr_period": 21},
    })
    cfg = cli._make_loop_config(settings, poll_seconds=42)
    assert cfg.poll_seconds == 42
    assert cfg.stop_atr_mult == 3.5, "stop_atr_mult aus config.yaml wurde ignoriert"
    assert cfg.take_profit_r == 1.5, "take_profit_r aus config.yaml wurde ignoriert"
    assert cfg.atr_period == 21, "atr_period aus config.yaml wurde ignoriert"


def test_broker_uses_config_fees():
    settings = Settings(raw={
        "capital": {"initial": 10_000}, "risk": {"risk_per_trade": 0.01},
        "backtest": {"fee_rate": 0.0031, "slippage_rate": 0.0022},
    })
    broker = cli._make_broker(settings)
    assert broker.fee_rate == 0.0031
    assert broker.slippage_rate == 0.0022


# ============================================================================
# Bug 2: Look-Ahead - der noch laufende (letzte) Balken wurde fuer Signal/ATR
#        benutzt. closed_bars() muss ihn entfernen, solange er nicht abgeschlossen ist.
# ============================================================================
def _frame(n: int, last_open: datetime, tf_minutes: int) -> pd.DataFrame:
    idx = pd.date_range(end=last_open, periods=n, freq=f"{tf_minutes}min", tz="UTC")
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)


def test_closed_bars_drops_still_forming_last_bar():
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    # letzter Balken oeffnete vor 1h auf 4h-Timeframe -> laeuft noch (Ende in 3h)
    df = _frame(200, last_open=now - timedelta(hours=1), tf_minutes=240)
    out = closed_bars(df, "4h", now=now)
    assert len(out) == len(df) - 1, "laufender Balken wurde NICHT entfernt (Look-Ahead)"
    assert out.index[-1] == df.index[-2]


def test_closed_bars_keeps_fully_closed_last_bar():
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    # letzter Balken oeffnete vor 5h auf 4h-Timeframe -> laengst abgeschlossen
    df = _frame(200, last_open=now - timedelta(hours=5), tf_minutes=240)
    out = closed_bars(df, "4h", now=now)
    assert len(out) == len(df), "abgeschlossener Balken faelschlich entfernt"


def test_closed_bars_unknown_timeframe_is_safe():
    now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    df = _frame(10, last_open=now, tf_minutes=60)
    assert len(closed_bars(df, "unbekannt", now=now)) == len(df)


def test_process_generates_signal_on_closed_bar_only(monkeypatch, tmp_path):
    """End-to-End: _process darf der Strategie NUR die abgeschlossenen Balken geben."""
    from core.store import Store
    from core.types import Direction, Signal
    from data.loader import MarketSpec, synthetic_ohlcv
    from execution.paper import PaperBroker
    from execution.paper_loop import PaperLoopConfig, PaperTradingLoop
    from risk.manager import Mode, RiskLimits, RiskManager
    from strategies import REGISTRY

    ohlcv = synthetic_ohlcv(300, MarketSpec("crypto", "BTC/USDT", "4h"), seed=1)
    # letzten Index kuenstlich auf "laeuft noch" setzen (Open = jetzt)
    idx = ohlcv.index.to_list()
    idx[-1] = pd.Timestamp(datetime.now(timezone.utc))
    ohlcv.index = pd.DatetimeIndex(idx)

    seen = {}

    def fake_signal(self, frame):
        seen["len"] = len(frame)
        return Signal(Direction.FLAT, 0.0, "test")

    monkeypatch.setattr(REGISTRY["ema_crossover"], "generate_signal", fake_signal, raising=True)

    loop = PaperTradingLoop(PaperBroker(), RiskManager(RiskLimits(), 10_000.0),
                            Store(tmp_path / "t.db"), [], PaperLoopConfig())
    loop._process("ema_crossover", MarketSpec("crypto", "BTC/USDT", "4h"), ohlcv)
    assert seen["len"] == len(ohlcv) - 1, "Strategie sah den noch laufenden Balken (Look-Ahead)"


# ============================================================================
# Bug 3: zwei konkurrierende serve-Prozesse auf derselben DB.
# ============================================================================
def test_serve_refuses_second_instance_when_bot_alive(tmp_path, capsys):
    from core.store import Store

    db = tmp_path / "t.db"
    st = Store(db)
    st.set_control("desired_state", "running")
    st.save_heartbeat("paper", equity=10_000, open_positions=0, daily_pnl_pct=0.0, tripped=False)

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "mode: paper\ncapital: {initial: 10000}\nrisk: {risk_per_trade: 0.01}\n"
        "universe: {crypto: {symbols: ['BTC/USDT'], timeframes: ['4h']}}\n"
        f"database: {{path: '{db.name}'}}\n", encoding="utf-8")

    # settings.db_path haengt an ROOT, daher Store-Pfad direkt patchen:
    import config.settings as cs
    monkey_path = db
    orig = cs.Settings.db_path
    try:
        cs.Settings.db_path = property(lambda self: monkey_path)
        rc = cli.cmd_serve(type("A", (), {"config": str(cfg), "interval": 30,
                                          "reeval_hours": 999, "force": False})())
    finally:
        cs.Settings.db_path = orig
    out = capsys.readouterr().out
    assert rc == 1
    assert "laeuft bereits ein Bot" in out


# ============================================================================
# Bug 4: Hebel-Deckel reduzierte qty, aber die Einstiegsgebuehr wurde weiter
#        auf der ungedeckelten (viel groesseren) Position berechnet.
# ============================================================================
def test_leverage_cap_fee_matches_capped_quantity():
    import numpy as np
    import pandas as pd
    from backtest.engine import BacktestConfig, run_backtest
    from strategies.base import Strategy

    class AlwaysLong(Strategy):
        name = "always_long"
        category = "trend"

        @staticmethod
        def default_params() -> dict:
            return {}

        def compute(self, ohlcv):
            out = self.empty_frame(ohlcv.index)
            out["direction"] = 1
            out["confidence"] = 1.0
            return out

    # Fast flache Kurse -> winziger ATR -> winziger Stop-Abstand -> Hebel-Deckel greift.
    n = 60
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    base = 100.0 + np.sin(np.arange(n)) * 0.01   # Schwankung ~0.01 um 100
    df = pd.DataFrame({"open": base, "high": base + 0.005, "low": base - 0.005,
                       "close": base, "volume": 1000.0}, index=idx)

    fee = 0.0006
    res = run_backtest(AlwaysLong(), df, symbol="X", timeframe="1h",
                       config=BacktestConfig(fee_rate=fee, slippage_rate=0.0, take_profit_r=0.0))
    closed = [t for t in res.trades if not t.is_open]
    assert closed, "es sollten Trades entstehen"
    # Invariante: Gesamtgebuehr == (Einstiegs- + Ausstiegs-Notional) * fee_rate,
    # jeweils mit der TATSAECHLICH gehandelten (gedeckelten) Menge.
    for t in closed:
        expected = abs(t.qty) * (t.entry_price + t.exit_price) * fee
        assert t.fees == pytest.approx(expected, rel=1e-9), (
            f"Gebuehr {t.fees} passt nicht zur gedeckelten Menge (erwartet {expected})")


# ============================================================================
# Bug 5 (Prio 1a): Stop-Loss-Loop bei ATR nahe Null - ein zu enger Stop muss
#                  hart abgelehnt werden statt platziert.
# ============================================================================
def test_min_stop_distance_rejects_near_zero_stop():
    from risk.manager import RiskLimits, RiskManager

    risk = RiskManager(RiskLimits(), 10_000.0)
    # Stop 0.05 % vom Preis entfernt (< 0.25 % Mindestabstand) -> ABGELEHNT
    d = risk.check_order(symbol="X", qty=1, entry_price=100.0, stop_loss=99.95, open_positions=0)
    assert not d.allowed and "Stop-Abstand" in d.reason
    # Stop 1 % entfernt -> erlaubt
    d2 = risk.check_order(symbol="X", qty=1, entry_price=100.0, stop_loss=99.0, open_positions=0)
    assert d2.allowed


def _always_long(monkeypatch):
    from core.types import Direction, Signal
    from strategies import REGISTRY
    monkeypatch.setattr(REGISTRY["ema_crossover"], "generate_signal",
                        lambda self, frame: Signal(Direction.LONG, 0.9, "always"), raising=True)


def _frame_calm_plus_forming(forming_low: float, forming_high: float) -> "pd.DataFrame":
    """60 ruhige, ABGESCHLOSSENE 4h-Balken (Spanne 0.5, ATR ~0.5) + 1 noch laufender
    Balken (Open = jetzt) mit vorgegebener Spanne."""
    import numpy as np
    n = 140  # >= 100, damit auch tick() den Frame verarbeitet (nicht als "zu kurz" verwirft)
    now = pd.Timestamp(datetime.now(timezone.utc))
    idx = pd.date_range(end=now, periods=n, freq="240min", tz="UTC")
    o = np.full(n, 100.0); h = np.full(n, 100.25); l = np.full(n, 99.75); c = np.full(n, 100.0)
    h[-1], l[-1], c[-1], o[-1] = forming_high, forming_low, 100.0, 100.0  # laufender Balken
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1000.0}, index=idx)


def test_bar_debounce_blocks_reentry_after_stop_same_bar(monkeypatch, tmp_path):
    """Reproduziert das Log-Muster: Position oeffnet, wird auf DEMSELBEN Balken durch
    Stop-Loss geschlossen - eine zweite Verarbeitung desselben Balkens darf NICHT neu
    eroeffnen. Ohne den Debounce-Fix wuerde die zweite Order platziert."""
    from core.store import Store
    from data.loader import MarketSpec
    from execution.paper import PaperBroker
    from execution.paper_loop import PaperLoopConfig, PaperTradingLoop
    from risk.manager import RiskLimits, RiskManager

    _always_long(monkeypatch)
    ohlcv = _frame_calm_plus_forming(forming_low=98.0, forming_high=101.0)  # Low 98 trifft Stop 99
    broker = PaperBroker(starting_balance=10_000.0)
    loop = PaperTradingLoop(broker, RiskManager(RiskLimits(), 10_000.0),
                            Store(tmp_path / "t.db"),
                            [("ema_crossover", "crypto", "BTC/USDT", "4h")], PaperLoopConfig())
    spec = MarketSpec("crypto", "BTC/USDT", "4h")

    loop._process("ema_crossover", spec, ohlcv)   # 1. Verarbeitung: oeffnet LONG
    assert len(broker.get_positions()) == 1, "erste Order sollte eroeffnen"
    loop._process("ema_crossover", spec, ohlcv)   # 2. Verarbeitung, SELBER Balken

    # Mit Fix: Stop schliesst die Position, Debounce verhindert Neu-Eroeffnung -> 0 offen.
    # Ohne Fix wuerde hier sofort wieder eroeffnet (== 1).
    assert len(broker.get_positions()) == 0, "Debounce muss Neu-Eroeffnung auf demselben Balken verhindern"


# ============================================================================
# Bug 6 (Prio 2): Circuit-Breaker-Trip darf den Loop NICHT beenden, sondern nur
#                 den Handel pausieren, bis reset_breaker kommt.
# ============================================================================
def test_trip_holds_loop_and_resumes_after_reset(monkeypatch, tmp_path):
    from core.store import Store
    from execution.paper import PaperBroker
    from execution.paper_loop import PaperLoopConfig, PaperTradingLoop
    from risk.manager import RiskLimits, RiskManager

    _always_long(monkeypatch)
    ohlcv = _frame_calm_plus_forming(forming_low=99.5, forming_high=100.5)  # Low 99.5 > Stop 99 -> haelt

    class FixedLoader:
        def load(self, spec, bars=1500, refresh=False):
            return ohlcv

    broker = PaperBroker(starting_balance=10_000.0)
    risk = RiskManager(RiskLimits(), 10_000.0)
    loop = PaperTradingLoop(broker, risk, Store(tmp_path / "t.db"),
                            [("ema_crossover", "crypto", "BTC/USDT", "4h")],
                            PaperLoopConfig(), FixedLoader())
    loop._running = True

    risk.trip("Test-Trip")            # Circuit Breaker manuell ausloesen
    loop.tick()
    assert loop._running, "Loop darf nach Trip NICHT beenden"
    assert len(broker.get_positions()) == 0, "im Trip-Zustand keine neuen Orders"

    risk.reset(confirm=True)          # bewusste Freigabe (wie reset_breaker)
    loop.tick()
    assert len(broker.get_positions()) == 1, "nach Reset muss wieder gehandelt werden"
