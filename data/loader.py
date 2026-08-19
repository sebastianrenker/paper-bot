"""Marktdaten-Ingestion mit lokalem Parquet/CSV-Cache.

Quellen:
  crypto  -> ccxt (falls installiert)
  stocks  -> yfinance (falls installiert)
  forex   -> yfinance (Symbole wie 'EURUSD=X')

Ist keine Quelle verfuegbar oder schlaegt der Abruf fehl, wird ein deterministischer
synthetischer Datensatz erzeugt. Der ist NUR fuer Entwicklung/Tests gedacht und wird
ueberall als `synthetic` markiert - Backtest-Ergebnisse darauf sind bedeutungslos.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent / "cache"

TIMEFRAME_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440,
}


@dataclass(frozen=True)
class MarketSpec:
    market: str          # crypto | stocks | forex
    symbol: str          # z.B. "BTC/USDT", "AAPL", "EURUSD=X"
    timeframe: str

    @property
    def slug(self) -> str:
        safe = self.symbol.replace("/", "-").replace("=", "_").replace(":", "-")
        return f"{self.market}_{safe}_{self.timeframe}"


class DataLoader:
    # Reihenfolge der Krypto-Boersen. Binance zuerst (schnell, in DE erreichbar), dann
    # US-erreichbare Fallbacks. Bugfix: auf GitHub-Actions-Runnern (US-IP) sperrt Binance
    # mit HTTP 451 ("restricted location") - der strikte Echtdaten-Modus uebersprang dann
    # JEDES Symbol, der Cloud-Bot handelte nichts. Kraken/KuCoin/Coinbase liefern von dort
    # echte 4h-Kerzen. Die erste Boerse, die Daten liefert, gewinnt.
    DEFAULT_EXCHANGES = ["binance", "kraken", "kucoin", "coinbase"]

    def __init__(self, cache_dir: Path | str = CACHE_DIR, allow_synthetic: bool = True,
                 max_retries: int = 4, retry_backoff: float = 2.0,
                 exchange_ids: list[str] | None = None) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.allow_synthetic = allow_synthetic
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.last_source: dict[str, str] = {}
        self.exchange_ids = list(exchange_ids) if exchange_ids else list(self.DEFAULT_EXCHANGES)
        self.last_exchange: dict[str, str] = {}   # welche Boerse ein Symbol geliefert hat
        self._exchanges: dict[str, object] = {}    # ccxt-Instanzen wiederverwenden

    # ---- oeffentliche API -------------------------------------------------
    def load(self, spec: MarketSpec, bars: int = 1500, refresh: bool = False) -> pd.DataFrame:
        cache_file = self.cache_dir / f"{spec.slug}.csv"
        cache_exists = cache_file.exists()
        # Bugfix: der Cache wurde vorher NUR nach Zeilenzahl geprueft, nie nach Alter -
        # siehe _cache_is_stale() fuer die volle Begruendung. `stale` heisst hier nur
        # "wir versuchen zuerst einen frischen Abruf", NICHT "der Cache ist wertlos":
        # schlaegt der frische Abruf fehl, ist ein veralteter Cache weiter der bessere
        # Fallback als synthetische Daten (siehe unten).
        stale = cache_exists and self._cache_is_stale(spec, cache_file)

        if cache_exists and not refresh and not stale:
            df = pd.read_csv(cache_file, index_col=0, parse_dates=True)
            if len(df) >= bars * 0.9:
                self.last_source[spec.slug] = "cache"
                return self._finalize(df.tail(bars))

        fetch = self._fetch_ccxt if spec.market == "crypto" else self._fetch_yfinance
        df = self._fetch_with_retry(fetch, spec, bars)

        if df is not None and not df.empty:
            df = self._drop_unclosed_bar(df, spec.timeframe)

        if df is None or df.empty:
            if cache_exists:
                cached = pd.read_csv(cache_file, index_col=0, parse_dates=True)
                if len(cached) >= bars * 0.5:
                    log.warning("Frischer Abruf fuer %s fehlgeschlagen - nutze VERALTETEN "
                                "Cache als Fallback statt synthetischer Daten.", spec.slug)
                    self.last_source[spec.slug] = "stale_cache"
                    return self._finalize(cached.tail(bars))
            if not self.allow_synthetic:
                # Strikter Modus: lieber ganz ohne Daten als mit erfundenen.
                raise RuntimeError(
                    f"Keine ECHTEN Boersendaten fuer {spec.slug} verfuegbar "
                    f"(nach {self.max_retries} Versuchen). Kombination wird uebersprungen."
                )
            log.warning("Nutze SYNTHETISCHE Daten fuer %s - nicht fuer Entscheidungen verwenden!", spec.slug)
            df = synthetic_ohlcv(bars, spec)
            self.last_source[spec.slug] = "synthetic"
        else:
            self.last_source[spec.slug] = "live"
            df.to_csv(cache_file)

        return self._finalize(df)

    def _cache_is_stale(self, spec: MarketSpec, cache_file: Path) -> bool:
        """Bugfix: der Cache wurde vorher NUR nach Zeilenzahl geprueft, nie nach Alter.
        `evaluate_universe()`/`optimize_strategy()`/die periodische Selbst-Anpassung in
        `cli.py serve` riefen `.load()` ohne `refresh=True` auf - sobald einmal genug
        Zeilen im Cache lagen, wurden sie fuer immer wiederverwendet, auch bei der laut
        README 'alle paar Stunden' laufenden Reevaluation. Die 'Selbstanpassung' rechnete
        dadurch effektiv immer wieder auf demselben eingefrorenen historischen Fenster,
        statt sich wirklich an neue Marktdaten anzupassen. Jetzt gilt der Cache als
        veraltet, sobald mehr Zeit vergangen ist als zwei Bars des jeweiligen Timeframes
        entsprechen (mindestens 60 Minuten) - reicht ein Symbol dann trotzdem nicht
        rechtzeitig frische Daten, bleibt der bisherige Zeilenzahl-Fallback (siehe `load()`)
        als Netz gegen Netzwerkausfaelle bestehen: `_fetch_with_retry` faellt bei Fehlschlag
        auf synthetisch/Skip zurueck, nie automatisch auf beliebig alten Cache."""
        age_minutes = (time.time() - cache_file.stat().st_mtime) / 60.0
        bar_minutes = TIMEFRAME_MINUTES.get(spec.timeframe, 60)
        max_age_minutes = max(bar_minutes * 2, 60)
        return age_minutes > max_age_minutes

    def _drop_unclosed_bar(self, df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
        """Bugfix: ccxt/yfinance liefern bei Intraday-Timeframes ueblicherweise die GERADE
        LAUFENDE, noch nicht geschlossene Kerze als letzten Eintrag mit (ihr close/high/low
        aendert sich noch, bis die Kerze tatsaechlich schliesst). `_process()` im Paper-Loop
        hat diese letzte Zeile bisher ungeprueft als 'den aktuellen Bar' fuer Signal, Stop-
        Berechnung (ATR) und Fill-Preis benutzt - das ist inkonsistent mit dem Backtest (der
        AUSSCHLIESSLICH abgeschlossene historische Kerzen sieht) und kann dazu fuehren, dass
        ein Signal Sekunden spaeter, wenn die Kerze tatsaechlich schliesst, schon nicht mehr
        gilt (Whipsaws/verfruehte Ein-/Ausstiege). Wir droppen die letzte Kerze, wenn ihr
        Ende (Startzeit + Timeframe-Dauer) noch in der Zukunft liegt."""
        if df.empty:
            return df
        minutes = TIMEFRAME_MINUTES.get(timeframe, 60)
        last_open = df.index[-1]
        if last_open.tzinfo is None:
            last_open = last_open.tz_localize("UTC")
        bar_close = last_open + timedelta(minutes=minutes)
        if bar_close > datetime.now(timezone.utc):
            return df.iloc[:-1]
        return df

    def _fetch_with_retry(self, fetch, spec: MarketSpec, bars: int):
        """Wiederholt den Abruf mit exponentiellem Backoff. Faengt v.a. Rate-Limits
        und transiente Netzfehler ab, damit bei vielen Kombinationen nicht einzelne
        Abrufe unnoetig scheitern und synthetisch ersetzt werden."""
        import time

        delay = 1.0
        for attempt in range(1, self.max_retries + 1):
            try:
                df = fetch(spec, bars)
                if df is not None and not df.empty:
                    return df
                raise RuntimeError("leere Antwort")
            except Exception as exc:  # noqa: BLE001
                if attempt >= self.max_retries:
                    log.warning("Datenabruf fuer %s nach %d Versuchen fehlgeschlagen: %s",
                                spec.slug, self.max_retries, exc)
                    return None
                log.info("Abruf %s Versuch %d/%d fehlgeschlagen (%s) - warte %.1fs",
                         spec.slug, attempt, self.max_retries, exc, delay)
                time.sleep(delay)
                delay *= self.retry_backoff
        return None

    def source_of(self, spec: MarketSpec) -> str:
        return self.last_source.get(spec.slug, "unknown")

    # ---- Quellen ----------------------------------------------------------
    def _get_exchange(self, ex_id: str):
        import ccxt  # type: ignore
        if ex_id not in self._exchanges:
            self._exchanges[ex_id] = getattr(ccxt, ex_id)({"enableRateLimit": True})
        return self._exchanges[ex_id]

    @staticmethod
    def _candidate_symbols(symbol: str) -> list[str]:
        """Nicht jede Boerse fuehrt USDT-Paare; probiere zusaetzlich die USD-Variante
        (z. B. ETH/USDT -> ETH/USD auf Kraken/Coinbase)."""
        cands = [symbol]
        if symbol.endswith("/USDT"):
            cands.append(symbol[:-5] + "/USD")
        elif symbol.endswith("/USD"):
            cands.append(symbol + "T")
        return cands

    def _fetch_ccxt(self, spec: MarketSpec, bars: int) -> pd.DataFrame | None:
        try:
            import ccxt  # type: ignore
        except ImportError:
            return None
        last_err = None
        for ex_id in self.exchange_ids:
            try:
                ex = self._get_exchange(ex_id)
            except Exception as exc:  # noqa: BLE001 - Boerse nicht verfuegbar -> naechste
                last_err = exc
                continue
            # Boerse ueberspringen, wenn sie den Timeframe nicht kann (z. B. Coinbase kein 4h).
            tfs = getattr(ex, "timeframes", None)
            if tfs and spec.timeframe not in tfs:
                continue
            for sym in self._candidate_symbols(spec.symbol):
                try:
                    raw = ex.fetch_ohlcv(sym, timeframe=spec.timeframe, limit=min(bars, 1000))
                except ccxt.BadSymbol:
                    continue                      # Symbolformat passt hier nicht -> naechstes Symbol
                except Exception as exc:  # noqa: BLE001 - Geoblock/Netz -> naechste Boerse
                    last_err = exc
                    break
                if raw:
                    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
                    df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
                    self.last_exchange[spec.slug] = ex_id
                    return df
        if last_err is not None:
            raise last_err   # damit _fetch_with_retry es sieht/loggt
        return None

    def _fetch_yfinance(self, spec: MarketSpec, bars: int) -> pd.DataFrame | None:
        try:
            import yfinance as yf  # type: ignore
        except ImportError:
            return None
        interval = {"1d": "1d", "1h": "1h", "30m": "30m", "15m": "15m", "5m": "5m"}.get(spec.timeframe)
        if interval is None:
            return None
        minutes = TIMEFRAME_MINUTES[spec.timeframe]
        days = max(int(bars * minutes / 1440 * 1.6), 5)
        # yfinance limitiert Intraday-Historie hart
        days = min(days, 730 if minutes >= 60 else 59)
        df = yf.download(spec.symbol, period=f"{days}d", interval=interval,
                         progress=False, auto_adjust=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
        df.index = pd.to_datetime(df.index, utc=True)
        return df.tail(bars)

    @staticmethod
    def _finalize(df: pd.DataFrame) -> pd.DataFrame:
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return df.dropna()


def synthetic_ohlcv(bars: int, spec: MarketSpec | None = None, seed: int | None = None) -> pd.DataFrame:
    """Deterministischer GBM-Kursverlauf mit Trend- und Range-Phasen.

    Nur fuer Tests und Offline-Entwicklung. Enthaelt bewusst wechselnde Regime,
    damit Trend- und Mean-Reversion-Strategien beide etwas zu tun bekommen.
    """
    tf = spec.timeframe if spec else "1h"
    minutes = TIMEFRAME_MINUTES.get(tf, 60)
    if seed is None:
        seed = abs(hash(spec.slug)) % (2**32) if spec else 42
    rng = np.random.default_rng(seed)

    # Regimewechsel alle ~200 Bars: Drift wechselt Vorzeichen/Staerke
    n_regimes = max(bars // 200, 1)
    drifts = rng.normal(0, 0.0006, n_regimes).repeat(bars // n_regimes + 1)[:bars]
    vol = 0.004 * (1 + 0.5 * np.sin(np.linspace(0, 8 * np.pi, bars)))
    returns = drifts + rng.normal(0, 1, bars) * vol
    close = 100 * np.exp(np.cumsum(returns))

    spread = close * vol
    high = close + np.abs(rng.normal(0, 1, bars)) * spread
    low = close - np.abs(rng.normal(0, 1, bars)) * spread
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])
    volume = rng.lognormal(10, 0.4, bars)

    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    index = pd.date_range(end=end, periods=bars, freq=f"{minutes}min", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=index,
    )
