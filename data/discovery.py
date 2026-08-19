"""Automatische Symbol-Entdeckung.

Bisher musste jedes handelbare Symbol von Hand in `config.yaml` unter `universe`
eingetragen werden. Diese Datei findet Kandidaten stattdessen automatisch - aber
bewusst NICHT nach "was pumpt gerade" / Social-Media-Hype (das waere reine
Kurzfrist-Spekulation, siehe die Diskussion dazu), sondern nach einem objektiven,
nachvollziehbaren Kriterium: 24h-Handelsvolumen (Liquiditaet). Ein Memecoin taucht
hier genauso automatisch auf wie ein Large-Cap-Coin - einfach weil er nach diesem
Kriterium liquide genug ist, nicht weil er als "Memecoin" markiert waere.

WICHTIG: entdeckte Symbole durchlaufen exakt dieselbe Qualitaetspruefung
(Walk-Forward-Validierung, Score-Gates in stats/portfolio.py) wie manuell
eingetragene - die Entdeckung erweitert nur den Kandidatenkreis, sie schaltet
nichts am Risikomanagement vorbei.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

DEFAULT_QUOTE = "USDT"
# Bewusst konservative Mindestschwelle: ein duenn gehandeltes Symbol wuerde die
# Gebuehren-/Slippage-Annahmen des Backtests (kalibriert auf liquide Hauptpaare)
# ohnehin unrealistisch machen, und ein brandneuer Coin ohne genug Historie faellt
# spaeter sowieso durch `evaluation.bars` (Standard 1500 Balken). Hier filtern wir
# das schon vor der teuren Auswertung raus.
DEFAULT_MIN_24H_VOLUME_USD = 5_000_000.0
DEFAULT_TOP_N = 30


def discover_crypto_symbols(
    exchange=None,
    quote: str = DEFAULT_QUOTE,
    top_n: int = DEFAULT_TOP_N,
    min_24h_volume_usd: float = DEFAULT_MIN_24H_VOLUME_USD,
) -> list[str]:
    """Liefert die `top_n` liquidesten Spot-Symbole gegen `quote` (z. B. USDT) auf
    Binance, sortiert nach 24h-Handelsvolumen in USD, mit Mindestvolumen-Filter.

    `exchange` ist injizierbar (fuer Tests / andere ccxt-Boersen) - Standard ist eine
    neue `ccxt.binance()`-Instanz, passend zum Rest von data/loader.py.
    """
    if exchange is None:
        import ccxt

        exchange = ccxt.binance({"enableRateLimit": True})

    tickers = exchange.fetch_tickers()
    candidates: list[tuple[str, float]] = []
    for symbol, t in tickers.items():
        if not symbol.endswith(f"/{quote}"):
            continue
        vol_quote = t.get("quoteVolume")
        if vol_quote is None:
            base_vol, last = t.get("baseVolume"), t.get("last")
            vol_quote = base_vol * last if base_vol is not None and last else None
        if vol_quote is None or vol_quote < min_24h_volume_usd:
            continue
        candidates.append((symbol, float(vol_quote)))

    candidates.sort(key=lambda pair: pair[1], reverse=True)
    symbols = [s for s, _ in candidates[:top_n]]
    log.info(
        "Symbol-Entdeckung (crypto): %d von %d Ticker(n) oberhalb %.0f USD 24h-Volumen, "
        "Top %d uebernommen.", len(candidates), len(tickers), min_24h_volume_usd, top_n,
    )
    return symbols


# Kein verlaesslicher, freier "nach Volumen sortiert"-Endpunkt fuer Aktien verfuegbar
# (yfinance bietet keine stabile offizielle Screener-API) - deshalb bewusst eine
# kuratierte Liste liquider Large-Caps statt einer erfundenen "Live-Rangliste", die
# unbemerkt kaputtgehen wuerde. Explizit ALS SOLCHE gekennzeichnet, keine Taeuschung
# ueber "automatisch entdeckt wie bei Krypto".
CURATED_LIQUID_STOCKS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM",
    "V", "UNH", "XOM", "WMT", "MA", "PG", "HD", "COST", "AVGO", "NFLX",
    "AMD", "CRM", "ADBE", "PEP", "KO", "BAC", "DIS", "MRK", "CSCO",
]


def discover_stock_symbols(top_n: int = DEFAULT_TOP_N) -> list[str]:
    """Liefert die ersten `top_n` Symbole einer kuratierten Liste liquider
    Large-Cap-Aktien - siehe CURATED_LIQUID_STOCKS-Kommentar fuer den Grund, warum das
    keine echte Live-Entdeckung wie bei Krypto ist."""
    return list(CURATED_LIQUID_STOCKS[:top_n])
