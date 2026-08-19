"""Tests fuer die automatische Symbol-Entdeckung (data/discovery.py, Settings.universe()).

Deckt ab: Sortierung/Filterung nach Liquiditaet (nicht Hype), dass entdeckte Symbole
manuell eingetragene ERGAENZEN statt ersetzen, dass ein fehlgeschlagener Entdeckungs-
Versuch die Auswertung nicht crasht (nur die manuellen Symbole bleiben), und dass ohne
`auto_discover`-Flag das Verhalten exakt wie vorher (rein aus der Config) ist.
"""
from __future__ import annotations

import pytest

from config.settings import Settings
from data.discovery import CURATED_LIQUID_STOCKS, discover_crypto_symbols, discover_stock_symbols


class _FakeExchange:
    """Minimaler Stand-in fuer ccxt.binance() mit fest verdrahteten Tickern."""

    def __init__(self, tickers: dict) -> None:
        self._tickers = tickers

    def fetch_tickers(self) -> dict:
        return self._tickers


def _ticker(quote_volume=None, base_volume=None, last=None) -> dict:
    return {"quoteVolume": quote_volume, "baseVolume": base_volume, "last": last}


# ---- discover_crypto_symbols: reine Liquiditaets-Sortierung, kein Hype-Ranking -----
def test_discover_crypto_symbols_sorts_by_volume_and_filters_illiquid():
    ex = _FakeExchange({
        "BTC/USDT": _ticker(quote_volume=500_000_000.0),
        "DOGE/USDT": _ticker(quote_volume=200_000_000.0),   # ein Memecoin - taucht auf,
        "PEPE/USDT": _ticker(quote_volume=50_000_000.0),    # weil liquide genug, nicht
        "SCAMCOIN/USDT": _ticker(quote_volume=100.0),        # weil "Memecoin"/Hype
        "ETH/BTC": _ticker(quote_volume=999_999_999.0),      # falsches Quote-Asset -> raus
    })
    symbols = discover_crypto_symbols(exchange=ex, quote="USDT", top_n=10, min_24h_volume_usd=5_000_000.0)
    assert symbols == ["BTC/USDT", "DOGE/USDT", "PEPE/USDT"]  # absteigend nach Volumen
    assert "SCAMCOIN/USDT" not in symbols  # unter der Mindestschwelle
    assert "ETH/BTC" not in symbols        # falsches Quote-Asset


def test_discover_crypto_symbols_respects_top_n():
    ex = _FakeExchange({f"COIN{i}/USDT": _ticker(quote_volume=10_000_000.0 + i) for i in range(10)})
    symbols = discover_crypto_symbols(exchange=ex, top_n=3, min_24h_volume_usd=0.0)
    assert len(symbols) == 3


def test_discover_crypto_symbols_falls_back_to_base_volume_times_last():
    """Manche Ticker liefern kein `quoteVolume` direkt - dann aus baseVolume * last berechnen."""
    ex = _FakeExchange({"XRP/USDT": _ticker(base_volume=1_000_000.0, last=2.0)})
    symbols = discover_crypto_symbols(exchange=ex, min_24h_volume_usd=1_000_000.0)
    assert symbols == ["XRP/USDT"]  # 1_000_000 * 2.0 = 2_000_000 >= Schwelle


def test_discover_stock_symbols_is_a_labeled_curated_list_not_live_ranking():
    symbols = discover_stock_symbols(top_n=5)
    assert symbols == CURATED_LIQUID_STOCKS[:5]


# ---- Settings.universe(): Auto-Discovery ergaenzt, ersetzt nicht -------------------
def test_universe_without_auto_discover_flag_is_unchanged(monkeypatch):
    """Ohne das Flag darf NIE ein Netzwerkaufruf ausgeloest werden - bestehendes
    Verhalten muss unveraendert bleiben."""
    called = []
    monkeypatch.setattr(Settings, "_run_discovery", staticmethod(lambda market, spec: called.append(1) or []))
    settings = Settings(raw={"universe": {"crypto": {"symbols": ["BTC/USDT"], "timeframes": ["1h"]}}})
    assert settings.universe() == [("crypto", "BTC/USDT", "1h")]
    assert called == []


def test_universe_with_auto_discover_merges_manual_and_discovered(monkeypatch):
    monkeypatch.setattr(
        Settings, "_run_discovery",
        staticmethod(lambda market, spec: ["DOGE/USDT", "BTC/USDT"]),  # BTC ueberschneidet sich
    )
    settings = Settings(raw={"universe": {"crypto": {
        "symbols": ["BTC/USDT"], "timeframes": ["1h"], "auto_discover": True,
    }}})
    out = settings.universe()
    symbols = [s for (_m, s, _tf) in out]
    assert symbols == ["BTC/USDT", "DOGE/USDT"]  # keine Duplikate, Reihenfolge erhalten


def test_universe_auto_discover_failure_falls_back_to_manual_symbols_only(monkeypatch):
    """Ein Netzwerkfehler bei der Entdeckung darf die gesamte Auswertung NICHT crashen -
    es bleibt bei den manuell eingetragenen Symbolen."""
    def _boom(market, spec):
        raise RuntimeError("Boerse nicht erreichbar")

    monkeypatch.setattr(Settings, "_run_discovery", staticmethod(_boom))
    settings = Settings(raw={"universe": {"crypto": {
        "symbols": ["BTC/USDT"], "timeframes": ["1h"], "auto_discover": True,
    }}})
    out = settings.universe()
    assert out == [("crypto", "BTC/USDT", "1h")]


def test_universe_auto_discover_uses_cache_within_refresh_window(monkeypatch):
    calls = []
    monkeypatch.setattr(Settings, "_run_discovery", staticmethod(lambda market, spec: calls.append(1) or ["ETH/USDT"]))
    settings = Settings(raw={"universe": {"crypto": {
        "symbols": [], "timeframes": ["1h"], "auto_discover": True,
        "auto_discover_refresh_minutes": 60,
    }}})
    settings.universe()
    settings.universe()
    assert len(calls) == 1  # zweiter Aufruf kam aus dem Cache, kein erneuter Netzwerkaufruf
