"""Unit-Tests fuer die Strategie-Signale."""
from __future__ import annotations

import pandas as pd
import pytest

from core.types import Direction
from strategies import REGISTRY, build


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_compute_shape_and_ranges(name, ohlcv):
    frame = build(name).compute(ohlcv)
    assert list(frame.index) == list(ohlcv.index)
    assert set(frame["direction"].unique()) <= {-1, 0, 1}
    assert frame["confidence"].between(0.0, 1.0).all()
    assert (frame.loc[frame["direction"] == 0, "confidence"] == 0.0).all()


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_generate_signal_returns_valid_signal(name, ohlcv):
    sig = build(name).generate_signal(ohlcv)
    assert isinstance(sig.direction, Direction)
    assert 0.0 <= sig.confidence <= 1.0
    assert sig.timestamp == ohlcv.index[-1].to_pydatetime()


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_no_lookahead(name, ohlcv):
    """Das Signal fuer Bar t darf sich nicht aendern, wenn spaetere Bars hinzukommen."""
    strategy = build(name)
    cut = 900
    full = strategy.compute(ohlcv)
    partial = strategy.compute(ohlcv.iloc[:cut])
    # letzte Bars des Teilfensters vergleichen (davor: identischer Warmup)
    a = full["direction"].iloc[cut - 50:cut].to_numpy()
    b = partial["direction"].iloc[-50:].to_numpy()
    assert (a == b).all(), f"{name}: Signal aendert sich rueckwirkend -> Look-ahead-Bias"


@pytest.mark.parametrize("name", ["ema_crossover", "donchian_breakout"])
def test_trend_strategies_go_long_in_uptrend(name, trending_up):
    frame = build(name).compute(trending_up)
    assert frame["direction"].iloc[-1] == 1


def test_ema_crossover_flips_direction():
    n = 400
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    # erst steigend, dann fallend
    values = [100 + i * 0.5 for i in range(200)] + [200 - i * 0.5 for i in range(200)]
    close = pd.Series(values, index=idx)
    df = pd.DataFrame({"open": close, "high": close + 0.1, "low": close - 0.1,
                       "close": close, "volume": 1000.0}, index=idx)
    frame = build("ema_crossover", {"trend_filter": 0}).compute(df)
    assert frame["direction"].iloc[190] == 1
    assert frame["direction"].iloc[-1] == -1


def test_rsi_signals_only_at_extremes(ohlcv):
    from core.indicators import rsi

    frame = build("rsi_mean_reversion", {"require_divergence": False}).compute(ohlcv)
    r = rsi(ohlcv["close"], 14)
    longs = frame["direction"] == 1
    assert (r[longs] < 30).all()
    shorts = frame["direction"] == -1
    assert (r[shorts] > 70).all()


def test_unknown_param_rejected():
    with pytest.raises(ValueError):
        build("ema_crossover", {"nicht_existent": 5})


def test_missing_columns_rejected():
    df = pd.DataFrame({"close": [1, 2, 3]},
                      index=pd.date_range("2024-01-01", periods=3, freq="1h", tz="UTC"))
    with pytest.raises(ValueError, match="fehlen Spalten"):
        build("ema_crossover").generate_signal(df)


def test_vwap_restricted_to_intraday():
    strategy = build("vwap_reversion")
    assert not strategy.supports("crypto", "1d")
    assert strategy.supports("crypto", "1h")
