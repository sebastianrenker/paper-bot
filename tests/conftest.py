import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.loader import MarketSpec, synthetic_ohlcv  # noqa: E402


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    return synthetic_ohlcv(1200, MarketSpec("crypto", "BTC/USDT", "1h"), seed=123)


@pytest.fixture
def trending_up() -> pd.DataFrame:
    """Klar steigender Markt ohne Rauschen - Trendstrategien MUESSEN hier long gehen."""
    n = 300
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = pd.Series([100 + i * 0.5 for i in range(n)], index=idx)
    return pd.DataFrame({
        "open": close.shift(1).fillna(100.0), "high": close + 0.2,
        "low": close - 0.2, "close": close, "volume": 1000.0,
    }, index=idx)
