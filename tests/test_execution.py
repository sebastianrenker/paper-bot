"""Integrationstests fuer den Paper-Order-Flow (gegen den simulierten Broker)."""
from __future__ import annotations

import pytest

from core.store import Store
from execution.paper import PaperBroker
from execution.paper_loop import PaperLoopConfig, PaperTradingLoop
from risk.manager import Mode, RiskLimits, RiskManager


@pytest.fixture
def broker() -> PaperBroker:
    b = PaperBroker(starting_balance=10_000.0, fee_rate=0.0, slippage_rate=0.0)
    b.update_price("BTC/USDT", 100.0)
    return b


def test_paper_broker_opens_and_values_position(broker):
    broker.place_order("BTC/USDT", "buy", 10, sl=95.0, tp=110.0)
    positions = broker.get_positions()
    assert len(positions) == 1 and positions[0].qty == 10
    broker.update_price("BTC/USDT", 110.0)
    assert broker.get_account_balance() == pytest.approx(10_100.0)


def test_paper_broker_realizes_pnl_on_close(broker):
    broker.place_order("BTC/USDT", "buy", 10, sl=95.0)
    broker.update_price("BTC/USDT", 110.0)
    broker.close_position("BTC/USDT")
    assert not broker.get_positions()
    assert broker.cash == pytest.approx(10_100.0)


def test_paper_broker_short_position(broker):
    broker.place_order("BTC/USDT", "sell", 10, sl=105.0)
    broker.update_price("BTC/USDT", 90.0)
    assert broker.get_account_balance() == pytest.approx(10_100.0)


def test_fees_are_charged():
    b = PaperBroker(starting_balance=10_000.0, fee_rate=0.001, slippage_rate=0.0)
    b.update_price("X", 100.0)
    b.place_order("X", "buy", 10, sl=95.0)
    assert b.cash == pytest.approx(10_000.0 - 1.0)


def test_stop_loss_is_triggered(broker):
    broker.place_order("BTC/USDT", "buy", 10, sl=95.0, tp=120.0)
    assert broker.check_stops("BTC/USDT", high=101.0, low=94.0) == "stop_loss"
    assert not broker.get_positions()
    assert broker.cash == pytest.approx(9_950.0)


def test_take_profit_is_triggered(broker):
    broker.place_order("BTC/USDT", "buy", 10, sl=95.0, tp=110.0)
    assert broker.check_stops("BTC/USDT", high=111.0, low=99.0) == "take_profit"
    assert broker.cash == pytest.approx(10_100.0)


def test_stop_wins_over_take_profit_in_same_bar(broker):
    """Pessimistische Annahme: liegen SL und TP im selben Bar, gilt der Stop."""
    broker.place_order("BTC/USDT", "buy", 10, sl=95.0, tp=110.0)
    assert broker.check_stops("BTC/USDT", high=111.0, low=94.0) == "stop_loss"


def test_order_without_price_raises():
    b = PaperBroker()
    with pytest.raises(RuntimeError, match="Kein Preis"):
        b.place_order("UNKNOWN", "buy", 1, sl=1.0)


def test_paper_loop_places_risk_checked_order(tmp_path, monkeypatch):
    """End-to-End: Loop laedt Daten, erzeugt Signal, prueft Risiko, platziert Paper-Order."""
    from data.loader import DataLoader, MarketSpec, synthetic_ohlcv

    store = Store(tmp_path / "test.db")
    broker = PaperBroker(starting_balance=10_000.0)
    risk = RiskManager(RiskLimits(), 10_000.0, mode=Mode.PAPER)

    class FixedLoader(DataLoader):
        def load(self, spec, bars=1500, refresh=False):
            return synthetic_ohlcv(600, spec, seed=5)

    loop = PaperTradingLoop(
        broker, risk, store, [("ema_crossover", "crypto", "BTC/USDT", "1h")],
        PaperLoopConfig(poll_seconds=1), FixedLoader(tmp_path / "cache"),
        {"ema_crossover": {"trend_filter": 0}},
    )
    loop.tick()

    assert store.equity_curve("paper").shape[0] == 1
    # Es wurde entweder eine Order platziert oder das Signal war FLAT - beides gueltig,
    # aber niemals eine Order ohne Stop-Loss.
    for order in broker.orders:
        assert order.stop_loss is not None


def test_paper_loop_refuses_live_broker_without_live_mode(tmp_path):
    store = Store(tmp_path / "t.db")
    risk = RiskManager(RiskLimits(), 10_000.0, mode=Mode.PAPER)

    class FakeLive(PaperBroker):
        mode = "live"

    with pytest.raises(RuntimeError, match="Live-Broker"):
        PaperTradingLoop(FakeLive(), risk, store, [])


def test_circuit_breaker_flattens_positions_in_loop(tmp_path):
    """Akzeptanzkriterium: reisst das Tagesverlustlimit, werden Positionen glattgestellt
    und der Loop geht in einen sicheren WARTEZUSTAND (er beendet sich NICHT).

    Hinweis: Dieser Test erwartete frueher `loop_stopped` beim Trip - das war das
    Prio-2-Bug-Verhalten (der Trip beendete den gesamten Dauerbetrieb-Prozess). Jetzt
    pausiert nur der Handel; siehe paper_loop._flatten_all."""
    from data.loader import DataLoader, synthetic_ohlcv

    store = Store(tmp_path / "t.db")
    broker = PaperBroker(starting_balance=10_000.0, fee_rate=0.0, slippage_rate=0.0)
    broker.update_price("BTC/USDT", 100.0)
    broker.place_order("BTC/USDT", "buy", 10, sl=95.0)
    risk = RiskManager(RiskLimits(max_daily_loss=0.01), 10_000.0, mode=Mode.PAPER)

    class FixedLoader(DataLoader):
        def load(self, spec, bars=1500, refresh=False):
            return synthetic_ohlcv(50, spec, seed=1)  # zu kurz -> kein neues Signal

    loop = PaperTradingLoop(broker, risk, store, [("ema_crossover", "crypto", "BTC/USDT", "1h")],
                            PaperLoopConfig(), FixedLoader(tmp_path / "cache"))
    loop._running = True          # Dauerbetrieb simulieren
    broker.cash -= 500.0          # simulierter Tagesverlust von 5 %
    loop.tick()

    assert risk.state.tripped
    assert not broker.get_positions(), "Positionen muessen glattgestellt werden"
    assert loop._running, "der Loop darf sich durch den Trip NICHT beenden (Prio-2-Fix)"
    events = store.audit()["event"].tolist()
    assert "forced_flat" in events and "breaker_hold" in events
    assert "loop_stopped" not in events, "der Trip darf den Prozess nicht mehr beenden"
