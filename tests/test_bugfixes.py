"""Regressionstests fuer die im Code-Review gefundenen und behobenen Fehler.

Jeder Test verweist im Docstring auf den Fund, den er absichert, damit die
Verbindung zwischen Bug-Report und Test nachvollziehbar bleibt.
"""
from __future__ import annotations

import datetime
import os
import time

import pandas as pd
import pytest

import cli
from config.settings import Settings
from core.store import Store
from data.loader import DataLoader, MarketSpec, synthetic_ohlcv
from execution.paper import PaperBroker
from execution.paper_loop import PaperLoopConfig, PaperTradingLoop
from risk.manager import Mode, RiskLimits, RiskManager, RiskState


# ---- Fund #1 (kritisch): Strategien schlossen sich gegenseitig die Positionen ----
def test_paper_broker_positions_isolated_per_strategy():
    """Zwei Strategien auf demselben Symbol duerfen sich nicht gegenseitig die
    Position schliessen - jede Strategie hat ihre eigene Position."""
    b = PaperBroker(fee_rate=0.0, slippage_rate=0.0)
    b.update_price("ETH/USDT", 2000.0)
    b.place_order("ETH/USDT", "buy", 1.0, sl=1900.0, strategy="donchian_breakout")

    # Eine andere Strategie ohne eigene Position "schliesst" auf demselben Symbol -
    # das darf ein No-Op sein, NICHT die Position von donchian_breakout anfassen.
    b.close_position("ETH/USDT", strategy="connors_rsi2")

    positions = b.get_positions()
    assert len(positions) == 1
    assert positions[0].strategy == "donchian_breakout"
    assert positions[0].qty == pytest.approx(1.0)

    # connors_rsi2 eroeffnet unabhaengig eine eigene (Short-)Position auf demselben Symbol.
    b.place_order("ETH/USDT", "sell", 2.0, sl=2100.0, strategy="connors_rsi2")
    by_strategy = {p.strategy: p for p in b.get_positions()}
    assert set(by_strategy) == {"donchian_breakout", "connors_rsi2"}
    assert by_strategy["donchian_breakout"].qty == pytest.approx(1.0)
    assert by_strategy["connors_rsi2"].qty == pytest.approx(-2.0)

    # Schliesst donchian_breakout jetzt SEINE Position, bleibt connors_rsi2 unberuehrt.
    b.close_position("ETH/USDT", strategy="donchian_breakout")
    remaining = b.get_positions()
    assert len(remaining) == 1 and remaining[0].strategy == "connors_rsi2"


class _FixedDirection:
    """Test-Doppel: Strategie mit fest verdrahteter Signalrichtung, um den Loop mit
    mehreren "Strategien" auf demselben Symbol deterministisch zu pruefen."""

    def __init__(self, direction: int, name: str):
        self.name = name
        self._direction = direction

    @staticmethod
    def default_params() -> dict:
        return {}

    def generate_signal(self, ohlcv):
        from core.types import Direction, Signal

        d = {1: Direction.LONG, -1: Direction.SHORT, 0: Direction.FLAT}[self._direction]
        return Signal(direction=d, confidence=1.0, reason="test", timestamp=ohlcv.index[-1].to_pydatetime())


def test_paper_loop_multiple_strategies_same_symbol_do_not_collide(tmp_path, monkeypatch):
    """End-to-End-Regressionstest fuer den Hauptfund: laeuft eine long-gehende und eine
    flach bleibende Strategie im selben Tick auf demselben Symbol, darf die zweite
    NICHT die frisch eroeffnete Position der ersten schliessen (bestaetigtes
    Live-Verhalten: donchian_breakout eroeffnet, connors_rsi2 schliesst Sekunden
    spaeter via 'signal_exit' - wiederholt, nur Gebuehren-/Slippage-Verlust)."""
    import strategies as strategies_module

    long_strategy = _FixedDirection(1, "long_strat")
    flat_strategy = _FixedDirection(0, "flat_strat")
    monkeypatch.setitem(strategies_module.REGISTRY, "long_strat", lambda **_: long_strategy)
    monkeypatch.setitem(strategies_module.REGISTRY, "flat_strat", lambda **_: flat_strategy)

    store = Store(tmp_path / "t.db")
    broker = PaperBroker(starting_balance=10_000.0, fee_rate=0.0, slippage_rate=0.0)
    risk = RiskManager(RiskLimits(), 10_000.0, mode=Mode.PAPER)

    class FixedLoader(DataLoader):
        def load(self, spec, bars=1500, refresh=False):
            return synthetic_ohlcv(600, spec, seed=5)

    loop = PaperTradingLoop(
        broker, risk, store,
        [("long_strat", "crypto", "ETH/USDT", "1h"), ("flat_strat", "crypto", "ETH/USDT", "1h")],
        PaperLoopConfig(), FixedLoader(tmp_path / "cache"),
    )
    loop.tick()

    positions = broker.get_positions()
    assert len(positions) == 1, "long_strat's Position darf nicht von flat_strat geschlossen werden"
    assert positions[0].strategy == "long_strat"

    events = store.audit()["event"].tolist()
    assert "position_closed" not in events, "es gab nichts zu schliessen - flat_strat hatte nie eine eigene Position"


# ---- Fund #2/#3: Circuit Breaker setzte sich automatisch zurueck ----------------
def test_circuit_breaker_survives_day_rollover():
    """Ein normaler Tageswechsel darf eine ausgeloeste Sperre NICHT aufheben - nur
    reset(confirm=True) darf das."""
    risk = RiskManager(RiskLimits(max_daily_loss=0.03), 10_000.0, mode=Mode.PAPER)
    risk.state = RiskState(day=datetime.date(2026, 8, 4), day_start_equity=10_000.0, peak_equity=10_000.0)

    risk.update_equity(9_600.0, now=datetime.datetime(2026, 8, 4, 23, 0, tzinfo=datetime.timezone.utc))
    assert risk.state.tripped

    risk.update_equity(9_600.0, now=datetime.datetime(2026, 8, 5, 0, 5, tzinfo=datetime.timezone.utc))
    assert risk.state.tripped, "Circuit Breaker darf sich nicht automatisch am naechsten Tag loesen"

    d = risk.check_order(symbol="BTC/USDT", qty=1, entry_price=100, stop_loss=95, open_positions=0)
    assert not d.allowed, "Am naechsten Tag ohne manuellen Reset duerfen weiterhin keine Orders raus"


def test_circuit_breaker_trip_persists_across_process_restart(tmp_path):
    """Ein neu erzeugter RiskManager auf derselben DB (= Prozess-Neustart, z. B. ueber
    den 'BOT STARTEN'-Knopf im Dashboard) muss eine am selben Tag ausgeloeste Sperre
    uebernehmen, statt frisch mit tripped=False zu starten."""
    store = Store(tmp_path / "t.db")
    settings = Settings(raw={"capital": {"initial": 10_000}, "risk": {"max_daily_loss": 0.03}})

    risk1 = cli._make_risk_manager(settings, store, mode=Mode.PAPER)
    risk1.update_equity(9_600.0)  # -4 % -> Sperre
    assert risk1.state.tripped

    # "Neustart": frischer RiskManager auf derselben Store-Instanz/DB.
    risk2 = cli._make_risk_manager(settings, store, mode=Mode.PAPER)
    assert risk2.state.tripped, "Sperre muss nach Neustart am selben Tag erhalten bleiben"

    risk2.reset(confirm=True)
    risk3 = cli._make_risk_manager(settings, store, mode=Mode.PAPER)
    assert not risk3.state.tripped, "Nach bewusstem reset(confirm=True) darf ein Neustart nicht wieder sperren"


# ---- Fund: Gebuehren/Slippage aus config.yaml wirkten nicht auf den Paper-Broker ----
def test_paper_broker_uses_configured_fees_and_slippage():
    settings = Settings(raw={"capital": {"initial": 10_000},
                             "backtest": {"fee_rate": 0.01, "slippage_rate": 0.02}})
    broker = cli._make_broker(settings)
    assert broker.fee_rate == pytest.approx(0.01)
    assert broker.slippage_rate == pytest.approx(0.02)


# ---- Fund: confidence floss nirgends in die Positionsgroesse ein ----------------
def test_confidence_scales_position_size():
    cfg = PaperLoopConfig(min_confidence_scale=0.4)
    assert cfg.size_multiplier(0.0) == pytest.approx(0.4)
    assert cfg.size_multiplier(1.0) == pytest.approx(1.0)
    assert cfg.size_multiplier(0.5) == pytest.approx(0.7)
    assert cfg.size_multiplier(0.2) < cfg.size_multiplier(0.9)


# ---- Fund: Datencache altert nie -------------------------------------------------
def test_cache_considered_stale_after_max_age(tmp_path):
    dl = DataLoader(cache_dir=tmp_path, allow_synthetic=True, max_retries=1)
    spec = MarketSpec("crypto", "BTC/USDT", "1h")
    cache_file = tmp_path / f"{spec.slug}.csv"
    synthetic_ohlcv(300, spec).to_csv(cache_file)
    assert not dl._cache_is_stale(spec, cache_file), "frisch geschriebener Cache ist nicht veraltet"

    three_hours_ago = time.time() - 3 * 3600  # > 2 Bars * 60min fuer 1h-Timeframe
    os.utime(cache_file, (three_hours_ago, three_hours_ago))
    assert dl._cache_is_stale(spec, cache_file), "Cache aelter als 2 Bars muss als veraltet gelten"


def test_stale_cache_still_used_as_fallback_when_fresh_fetch_fails(tmp_path, monkeypatch):
    """Staleness darf keine Zuverlässigkeits-Regression sein: schlaegt der frische
    Abruf fehl, ist ein alter Cache weiterhin besser als synthetische Daten."""
    dl = DataLoader(cache_dir=tmp_path, allow_synthetic=True, max_retries=1)
    spec = MarketSpec("crypto", "BTC/USDT", "1h")
    cache_file = tmp_path / f"{spec.slug}.csv"
    synthetic_ohlcv(300, spec, seed=1).to_csv(cache_file)
    old = time.time() - 5 * 3600
    os.utime(cache_file, (old, old))

    monkeypatch.setattr(dl, "_fetch_ccxt", lambda spec, bars: None)
    df = dl.load(spec, bars=300)
    assert len(df) > 0
    assert dl.source_of(spec) == "stale_cache"


# ---- Fund: die gerade noch laufende (unfertige) Kerze wurde wie eine geschlossene
#            behandelt -------------------------------------------------------------
def test_drop_unclosed_bar_removes_still_forming_last_candle(tmp_path):
    """ccxt/yfinance liefern bei Intraday-Timeframes ueblicherweise die gerade laufende,
    noch nicht geschlossene Kerze als letzten Eintrag - Backtests sehen dagegen NUR
    abgeschlossene Kerzen. Die letzte Zeile muss entfernt werden, wenn ihr Ende
    (Startzeit + Timeframe-Dauer) noch in der Zukunft liegt."""
    dl = DataLoader(cache_dir=tmp_path, allow_synthetic=True, max_retries=1)
    now = pd.Timestamp.utcnow().floor("h")
    idx = pd.date_range(end=now, periods=5, freq="1h", tz="UTC")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}, index=idx)

    out = dl._drop_unclosed_bar(df, "1h")
    assert len(out) == 4, "die letzte (noch laufende) 1h-Kerze muss entfernt werden"
    assert out.index[-1] == idx[-2]

    closed_idx = pd.date_range(end=now - pd.Timedelta(hours=2), periods=5, freq="1h", tz="UTC")
    closed_df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0},
                             index=closed_idx)
    out2 = dl._drop_unclosed_bar(closed_df, "1h")
    assert len(out2) == 5, "laengst geschlossene Kerzen duerfen nicht entfernt werden"


def test_load_drops_unclosed_bar_before_using_or_caching(tmp_path, monkeypatch):
    """End-to-End: `.load()` darf die noch laufende Kerze weder an den Aufrufer
    weitergeben noch in den Cache schreiben."""
    dl = DataLoader(cache_dir=tmp_path, allow_synthetic=True, max_retries=1)
    spec = MarketSpec("crypto", "BTC/USDT", "1h")
    now = pd.Timestamp.utcnow().floor("h")
    idx = pd.date_range(end=now, periods=300, freq="1h", tz="UTC")
    fake = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
                         "volume": 10.0}, index=idx)

    monkeypatch.setattr(dl, "_fetch_ccxt", lambda spec, bars: fake)
    out = dl.load(spec, bars=300)
    assert out.index[-1] == idx[-2], "die zurueckgegebenen Daten duerfen nicht auf der laufenden Kerze enden"

    cached = pd.read_csv(dl.cache_dir / f"{spec.slug}.csv", index_col=0, parse_dates=True)
    assert cached.index[-1] == idx[-2], "der Cache darf die laufende Kerze nicht persistieren"


# ---- Fund: echte Paper-Trades landeten nie in der `trades`-Tabelle --------------
def test_closed_paper_trade_is_persisted_to_trades_table(tmp_path):
    store = Store(tmp_path / "t.db")
    broker = PaperBroker(starting_balance=10_000.0, fee_rate=0.0, slippage_rate=0.0)
    broker.update_price("BTC/USDT", 100.0)
    broker.place_order("BTC/USDT", "buy", 10, sl=95.0, strategy="ema_crossover")
    broker.update_price("BTC/USDT", 110.0)
    broker.close_position("BTC/USDT", strategy="ema_crossover", exit_reason="signal_exit")
    assert len(broker.closed_trades) == 1

    loop = PaperTradingLoop(broker, RiskManager(RiskLimits(), 10_000.0), store, [])
    loop._flush_closed_trades({("ema_crossover", "BTC/USDT"): ("crypto", "1h")})

    trades = store.trades(source="paper")
    assert len(trades) == 1
    assert trades.iloc[0]["pnl"] == pytest.approx(100.0)
    assert trades.iloc[0]["strategy"] == "ema_crossover"
    assert broker.closed_trades == []  # nach dem Flush geleert


# ---- Fund: totes `price`-Argument in close_position ------------------------------
def test_close_position_uses_explicit_price_argument():
    b = PaperBroker(fee_rate=0.0, slippage_rate=0.0)
    b.update_price("X", 100.0)
    b.place_order("X", "buy", 10, sl=95.0, strategy="s")
    # Bewusst EIN anderer Preis als der zuletzt gesetzte Marktpreis - muss tatsaechlich
    # als Fill-Preis verwendet werden (vorher: totes Argument, Fill kam immer aus dem
    # zuletzt via update_price() gesetzten Preis).
    b.close_position("X", price=123.0, strategy="s")
    assert b.cash == pytest.approx(10_000.0 + (123.0 - 100.0) * 10)


# ---- Fund: Neustart des Bot-Prozesses verwarf stillschweigend Kapitalstand/Positionen ---
def test_restore_broker_state_reconstructs_cash_and_open_positions(tmp_path):
    """Fuer echten Dauerbetrieb (Server mit Auto-Neustart) MUSS ein neu gestarteter
    Prozess dort weitermachen, wo der vorherige aufgehoert hat - nicht wieder bei
    capital.initial mit leerem Bestand beginnen. Simuliert: ein vorheriger Lauf hat
    Heartbeat + Positions-Snapshot hinterlassen (Kapital 480, eine offene Position mit
    +5 unrealisiertem Gewinn -> Cash muss 475 sein, NICHT wieder die 500 aus der Config)."""
    store = Store(tmp_path / "restore.db")
    store.save_heartbeat("paper", equity=480.0, open_positions=1, daily_pnl_pct=-0.04, tripped=0)
    entry_time = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    store.save_positions_snapshot([{
        "symbol": "BTC/USDT", "strategy": "donchian_breakout", "qty": 0.01,
        "avg_price": 40_000.0, "stop_loss": 38_000.0, "take_profit": 44_000.0,
        "mark_price": 40_500.0, "unrealized": 5.0, "entry_time": entry_time,
    }], source="paper")

    broker = PaperBroker(starting_balance=500.0, fee_rate=0.0, slippage_rate=0.0)
    restored = cli.restore_broker_state(broker, store)

    assert restored is True
    assert broker.cash == pytest.approx(480.0 - 5.0)  # equity minus unrealized = cash
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "BTC/USDT"
    assert positions[0].strategy == "donchian_breakout"
    assert positions[0].qty == pytest.approx(0.01)
    assert positions[0].avg_price == pytest.approx(40_000.0)
    assert positions[0].entry_time == entry_time
    # get_account_balance() muss wieder die urspruengliche Equity (480) ergeben,
    # nicht die 500 aus starting_balance.
    assert broker.get_account_balance() == pytest.approx(480.0)


def test_restore_broker_state_leaves_fresh_broker_untouched_when_no_prior_run(tmp_path):
    """Eine wirklich frische Installation (kein vorheriger Lauf in der DB) darf NICHT
    versuchen, etwas wiederherzustellen - der Broker bleibt bei capital.initial."""
    store = Store(tmp_path / "fresh.db")
    broker = PaperBroker(starting_balance=500.0, fee_rate=0.0, slippage_rate=0.0)

    restored = cli.restore_broker_state(broker, store)

    assert restored is False
    assert broker.cash == pytest.approx(500.0)
    assert broker.get_positions() == []


def test_make_broker_restores_state_when_store_passed(tmp_path):
    """End-to-End: _make_broker(settings, store) muss den wiederhergestellten Zustand
    liefern, nicht den frischen Default - genau das, was cmd_paper/cmd_serve jetzt tun."""
    store = Store(tmp_path / "e2e.db")
    store.save_heartbeat("paper", equity=333.0, open_positions=0, daily_pnl_pct=0.0, tripped=0)
    store.save_positions_snapshot([], source="paper")

    settings = Settings(raw={"capital": {"initial": 500.0}, "backtest": {}})
    broker = cli._make_broker(settings, store)

    assert broker.cash == pytest.approx(333.0)
