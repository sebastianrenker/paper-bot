"""Tests fuer robusten Datenabruf: Retry mit Backoff und strikter Echtdaten-Modus."""
from __future__ import annotations

import pandas as pd
import pytest

from data.loader import DataLoader, MarketSpec, synthetic_ohlcv


def test_strict_mode_raises_when_no_real_data(tmp_path, monkeypatch):
    """allow_synthetic=False: kann keine Echtdaten geladen werden, wird NICHT
    synthetisch ersetzt, sondern ein Fehler geworfen (Kombination wird uebersprungen)."""
    dl = DataLoader(cache_dir=tmp_path, allow_synthetic=False, max_retries=2, retry_backoff=1.0)
    monkeypatch.setattr(dl, "_fetch_ccxt", lambda spec, bars: None)
    with pytest.raises(RuntimeError, match="ECHTEN Boersendaten"):
        dl.load(MarketSpec("crypto", "BTC/USDT", "1h"), bars=500)
    # Und es wurde NICHT als 'live' oder 'synthetic' markiert
    assert dl.source_of(MarketSpec("crypto", "BTC/USDT", "1h")) != "synthetic"


def test_retry_succeeds_after_transient_failures(tmp_path, monkeypatch):
    calls = {"n": 0}

    def flaky(spec, bars):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("rate limit")
        return synthetic_ohlcv(bars, spec)  # 3. Versuch liefert Daten

    dl = DataLoader(cache_dir=tmp_path, allow_synthetic=False, max_retries=4, retry_backoff=1.0)
    monkeypatch.setattr(dl, "_fetch_ccxt", flaky)
    df = dl.load(MarketSpec("crypto", "BTC/USDT", "1h"), bars=300)
    assert len(df) > 0
    assert calls["n"] == 3  # zwei Fehlschlaege, dann Erfolg
    assert dl.source_of(MarketSpec("crypto", "BTC/USDT", "1h")) == "live"


def test_lenient_mode_still_falls_back(tmp_path, monkeypatch):
    """Standardmodus (allow_synthetic=True) faellt weiterhin zurueck - fuer Offline/Tests."""
    dl = DataLoader(cache_dir=tmp_path, allow_synthetic=True, max_retries=1)
    monkeypatch.setattr(dl, "_fetch_ccxt", lambda spec, bars: None)
    df = dl.load(MarketSpec("crypto", "BTC/USDT", "1h"), bars=300)
    assert len(df) > 0
    assert dl.source_of(MarketSpec("crypto", "BTC/USDT", "1h")) == "synthetic"


def test_settings_require_real_wires_strict_loader(tmp_path):
    from config.settings import Settings

    s = Settings(raw={"data": {"require_real": True}})
    assert s.require_real is True
    assert s.data_loader().allow_synthetic is False
    s2 = Settings(raw={"data": {"require_real": False}})
    assert s2.data_loader().allow_synthetic is True
