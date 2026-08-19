"""Technische Indikatoren - bewusst ohne externe TA-Bibliothek (nur pandas/numpy),
damit die Strategien deterministisch und ohne Binary-Dependencies testbar bleiben."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder-Smoothing
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Directional Index - Mass fuer Trendstaerke (nicht Trendrichtung)."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(df).ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0.0)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line


def bollinger(series: pd.Series, period: int = 20, std_mult: float = 2.0):
    mid = series.rolling(period).mean()
    std = series.rolling(period).std(ddof=0)
    return mid - std_mult * std, mid, mid + std_mult * std


def bandwidth(series: pd.Series, period: int = 20, std_mult: float = 2.0) -> pd.Series:
    lower, mid, upper = bollinger(series, period, std_mult)
    return (upper - lower) / mid.replace(0.0, np.nan)


def vwap(df: pd.DataFrame, session: str = "D") -> pd.Series:
    """Session-VWAP - setzt pro Handelstag zurueck (Intraday-Konzept)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    grouper = session_periods(df.index, session)
    return pv.groupby(grouper).cumsum() / df["volume"].groupby(grouper).cumsum().replace(0.0, np.nan)


def session_periods(index: pd.DatetimeIndex, session: str = "D") -> pd.PeriodIndex:
    """Session-Zugehoerigkeit je Bar. Zeitzonen werden vorher nach UTC normalisiert,
    damit `to_period` nicht warnt und Sessions reproduzierbar bleiben."""
    if index.tz is not None:
        index = index.tz_convert("UTC").tz_localize(None)
    return index.to_period(session)


def donchian(df: pd.DataFrame, period: int = 20):
    return df["low"].rolling(period).min(), df["high"].rolling(period).max()


def swing_points(df: pd.DataFrame, left: int = 3, right: int = 3):
    """Regelbasierte Swing-Highs/Lows (fraktal). Rechte Seite wird geshiftet,
    damit kein Look-ahead entsteht."""
    highs = df["high"]
    lows = df["low"]
    is_high = highs == highs.rolling(left + right + 1, center=True).max()
    is_low = lows == lows.rolling(left + right + 1, center=True).min()
    # erst `right` Bars spaeter bestaetigt und damit handelbar
    return is_low.shift(right).fillna(False), is_high.shift(right).fillna(False)


def realized_volatility(series: pd.Series, period: int = 20) -> pd.Series:
    return series.pct_change().rolling(period).std(ddof=0).fillna(0.0)


# --- weitere Indikatoren (Recherche-gestuetzt) ---------------------------

def keltner(df: pd.DataFrame, ema_period: int = 20, atr_period: int = 20, mult: float = 2.0):
    """Keltner-Kanal: EMA-Mitte +/- ATR-Vielfaches. Basis fuer Pullback- und
    Breakout-Systeme (Quelle: opofinance/liberatedstocktrader)."""
    mid = ema(df["close"], ema_period)
    band = atr(df, atr_period) * mult
    return mid - band, mid, mid + band


def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3, smooth: int = 3):
    """Stochastik-Oszillator (%K/%D), 0..100."""
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    raw_k = 100 * (df["close"] - low_min) / (high_max - low_min).replace(0.0, np.nan)
    k = raw_k.rolling(smooth).mean()
    d = k.rolling(d_period).mean()
    return k.fillna(50.0), d.fillna(50.0)


def dmi(df: pd.DataFrame, period: int = 14):
    """Directional Movement: +DI, -DI, ADX. Trendrichtung + Trendstaerke."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(df).ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / tr
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)) * 100
    adx_line = dx.ewm(alpha=1 / period, adjust=False).mean().fillna(0.0)
    return plus_di.fillna(0.0), minus_di.fillna(0.0), adx_line


def supertrend(df: pd.DataFrame, period: int = 10, mult: float = 3.0):
    """Supertrend: ATR-basiertes Trendfolge-Band. Gibt (trend, direction) zurueck,
    direction = +1 (long) / -1 (short). Stateful, aber ohne Look-ahead
    (jede Zeile nutzt nur Werte bis einschliesslich dieser Zeile)."""
    hl2 = (df["high"] + df["low"]) / 2
    atr_v = atr(df, period)
    upper = hl2 + mult * atr_v
    lower = hl2 - mult * atr_v
    close = df["close"].to_numpy()
    up, lo = upper.to_numpy(), lower.to_numpy()

    final_up = np.full(len(df), np.nan)
    final_lo = np.full(len(df), np.nan)
    direction = np.ones(len(df), dtype=int)
    for i in range(len(df)):
        if i == 0 or np.isnan(up[i]):
            final_up[i], final_lo[i] = up[i], lo[i]
            continue
        final_up[i] = up[i] if (up[i] < final_up[i - 1] or close[i - 1] > final_up[i - 1]) else final_up[i - 1]
        final_lo[i] = lo[i] if (lo[i] > final_lo[i - 1] or close[i - 1] < final_lo[i - 1]) else final_lo[i - 1]
        if close[i] > final_up[i - 1]:
            direction[i] = 1
        elif close[i] < final_lo[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
    trend = np.where(direction > 0, final_lo, final_up)
    return pd.Series(trend, index=df.index), pd.Series(direction, index=df.index)


def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Williams %R, oszilliert zwischen 0 (Hoch) und -100 (Tief)."""
    hh = df["high"].rolling(period).max()
    ll = df["low"].rolling(period).min()
    wr = -100 * (hh - df["close"]) / (hh - ll).replace(0.0, np.nan)
    return wr.fillna(-50.0)


def cci(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Commodity Channel Index. > +100 ueberkauft, < -100 ueberverkauft."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    sma_t = typical.rolling(period).mean()
    mad = (typical - sma_t).abs().rolling(period).mean()
    return ((typical - sma_t) / (0.015 * mad.replace(0.0, np.nan))).fillna(0.0)


def roc(series: pd.Series, period: int = 20) -> pd.Series:
    """Rate of Change in Prozent (Basis fuer Time-Series-Momentum)."""
    return (series / series.shift(period) - 1.0).fillna(0.0) * 100


def ichimoku(df: pd.DataFrame, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52):
    """Ichimoku-Komponenten. Senkou-Spannen werden bewusst NICHT in die Zukunft
    projiziert (kein `shift(-kijun)`), damit die Werte fuer den aktuellen Bar
    look-ahead-frei nutzbar bleiben."""
    conv = (df["high"].rolling(tenkan).max() + df["low"].rolling(tenkan).min()) / 2
    base = (df["high"].rolling(kijun).max() + df["low"].rolling(kijun).min()) / 2
    span_a = (conv + base) / 2
    span_b = (df["high"].rolling(senkou_b).max() + df["low"].rolling(senkou_b).min()) / 2
    return conv, base, span_a, span_b
