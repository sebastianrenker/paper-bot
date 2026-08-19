"""Tests fuer die Live-Persistenz des Paper-Traders (Positionen, Heartbeat)."""
from __future__ import annotations

from core.store import Store
from execution.paper import PaperBroker
from execution.paper_loop import PaperLoopConfig, PaperTradingLoop
from risk.manager import Mode, RiskLimits, RiskManager


def test_positions_snapshot_roundtrip(tmp_path):
    store = Store(tmp_path / "t.db")
    store.save_positions_snapshot([
        {"symbol": "BTC/USDT", "qty": 0.5, "avg_price": 100.0, "stop_loss": 95.0,
         "take_profit": 110.0, "mark_price": 102.0, "unrealized": 1.0},
    ], source="paper")
    snap = store.positions_snapshot("paper")
    assert len(snap) == 1
    assert snap.iloc[0]["symbol"] == "BTC/USDT"
    assert snap.iloc[0]["unrealized"] == 1.0


def test_positions_snapshot_is_replaced_not_appended(tmp_path):
    store = Store(tmp_path / "t.db")
    store.save_positions_snapshot([{"symbol": "A", "qty": 1, "avg_price": 1.0}], "paper")
    store.save_positions_snapshot([{"symbol": "B", "qty": 2, "avg_price": 2.0}], "paper")
    snap = store.positions_snapshot("paper")
    assert len(snap) == 1 and snap.iloc[0]["symbol"] == "B"  # ersetzt, nicht angehaengt


def test_heartbeat_upsert(tmp_path):
    store = Store(tmp_path / "t.db")
    store.save_heartbeat("paper", equity=10_000, open_positions=1, daily_pnl_pct=0.0, tripped=False)
    store.save_heartbeat("paper", equity=9_900, open_positions=2, daily_pnl_pct=-0.01, tripped=False)
    hb = store.heartbeat("paper")
    assert hb["equity"] == 9_900 and hb["open_positions"] == 2  # eine Zeile, aktualisiert


def test_loop_persists_positions_and_heartbeat(tmp_path):
    """Nach einem Tick muessen Positionen + Heartbeat in der DB stehen, damit das
    Dashboard als separater Prozess live mitlesen kann."""
    from data.loader import DataLoader, synthetic_ohlcv

    store = Store(tmp_path / "t.db")
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

    hb = store.heartbeat("paper")
    assert hb and hb["equity"] is not None
    assert "open_positions" in hb
    # positions_snapshot spiegelt exakt die offenen Broker-Positionen
    assert len(store.positions_snapshot("paper")) == len(broker.get_positions())
