"""Konfigurations-Laden. Secrets kommen ausschliesslich aus .env, nie aus der YAML."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from backtest.engine import BacktestConfig
from risk.manager import Mode, RiskLimits

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config" / "config.yaml"


@dataclass
class Settings:
    raw: dict[str, Any] = field(default_factory=dict)
    path: Path = DEFAULT_CONFIG_PATH
    # Cache fuer die automatische Symbol-Entdeckung: {market: (timestamp, [symbole])}.
    # Kein dataclass-Vergleichs-/Repr-Feld - reiner interner Laufzeit-Zustand.
    _discovery_cache: dict[str, tuple] = field(default_factory=dict, repr=False, compare=False)

    # ---- abgeleitete Sichten ---------------------------------------------
    @property
    def configured_mode(self) -> Mode:
        return Mode(self.raw.get("mode", "paper"))

    @property
    def effective_mode(self) -> Mode:
        """Der tatsaechlich aktive Modus. LIVE nur, wenn das Gate freigeschaltet ist -
        die Konfiguration allein genuegt nie."""
        from execution.gate import is_unlocked

        mode = self.configured_mode
        if mode == Mode.LIVE and not is_unlocked():
            return Mode.PAPER
        return mode

    @property
    def initial_capital(self) -> float:
        return float(self.raw.get("capital", {}).get("initial", 10_000.0))

    @property
    def require_real(self) -> bool:
        return bool(self.raw.get("data", {}).get("require_real", False))

    def validate(self) -> list[str]:
        """Prueft die Konfiguration auf typische Fehler und gibt verstaendliche
        Problemmeldungen zurueck (leere Liste = alles in Ordnung)."""
        problems: list[str] = []
        if self.raw.get("mode") not in ("backtest", "paper", "live"):
            problems.append(f"mode '{self.raw.get('mode')}' ist ungueltig (erlaubt: backtest, paper, live).")
        if self.initial_capital <= 0:
            problems.append("capital.initial muss groesser als 0 sein.")
        try:
            self.risk_limits()
        except Exception as exc:  # noqa: BLE001
            problems.append(f"Risikolimits ungueltig: {exc}")
        if not self.universe():
            problems.append("universe ist leer - keine Maerkte/Symbole zum Auswerten.")
        return problems

    def data_loader(self):
        """DataLoader passend zur Konfiguration - im strikten Modus ohne synthetischen Fallback.
        `data.exchanges` erlaubt es, die Krypto-Boersen-Reihenfolge zu setzen (Fallback,
        falls eine Boerse - z. B. Binance von US-Servern - geoblockt ist)."""
        from data.loader import DataLoader
        exchanges = self.raw.get("data", {}).get("exchanges")
        return DataLoader(allow_synthetic=not self.require_real, exchange_ids=exchanges)

    def risk_limits(self) -> RiskLimits:
        return RiskLimits(**self.raw.get("risk", {}))

    def backtest_config(self) -> BacktestConfig:
        bt = dict(self.raw.get("backtest", {}))
        return BacktestConfig(initial_capital=self.initial_capital,
                              risk_per_trade=self.risk_limits().risk_per_trade, **bt)

    def universe(self) -> list[tuple[str, str, str]]:
        """(market, symbol, timeframe)-Tripel.

        Wenn ein Markt in config.yaml `auto_discover: true` gesetzt hat, werden die
        manuell eingetragenen `symbols` um automatisch entdeckte Symbole ergaenzt
        (siehe data/discovery.py) - nach Liquiditaet, nicht nach Hype. Ohne dieses
        Flag verhaelt sich `universe()` exakt wie vorher (rein aus der Config)."""
        out = []
        for market, spec in (self.raw.get("universe") or {}).items():
            for symbol in self._resolve_symbols(market, spec):
                for tf in spec.get("timeframes", []):
                    out.append((market, symbol, tf))
        return out

    def _resolve_symbols(self, market: str, spec: dict) -> list[str]:
        symbols = list(spec.get("symbols", []))
        if not spec.get("auto_discover"):
            return symbols

        refresh_s = float(spec.get("auto_discover_refresh_minutes", 60)) * 60
        cached = self._discovery_cache.get(market)
        if cached and (time.time() - cached[0]) < refresh_s:
            discovered = cached[1]
        else:
            try:
                discovered = self._run_discovery(market, spec)
                self._discovery_cache[market] = (time.time(), discovered)
            except Exception as exc:  # noqa: BLE001 - Entdeckung darf die Auswertung nie blockieren
                log.warning("Symbol-Entdeckung fuer '%s' fehlgeschlagen (%s) - nutze nur die "
                           "manuell eingetragenen Symbole.", market, exc)
                discovered = cached[1] if cached else []
        # Reihenfolge erhalten, keine Duplikate (dict.fromkeys statt set() - deterministisch).
        return list(dict.fromkeys(symbols + discovered))

    @staticmethod
    def _run_discovery(market: str, spec: dict) -> list[str]:
        top_n = int(spec.get("auto_discover_top_n", 30))
        if market == "crypto":
            from data.discovery import discover_crypto_symbols
            return discover_crypto_symbols(
                quote=spec.get("auto_discover_quote", "USDT"), top_n=top_n,
                min_24h_volume_usd=float(spec.get("auto_discover_min_volume_usd", 5_000_000.0)),
            )
        if market == "stocks":
            from data.discovery import discover_stock_symbols
            return discover_stock_symbols(top_n=top_n)
        return []

    def strategy_names(self) -> list[str]:
        from strategies import REGISTRY

        names = self.raw.get("strategies") or []
        return list(names) if names else list(REGISTRY)

    def params_for(self, strategy: str) -> dict:
        return dict((self.raw.get("strategy_params") or {}).get(strategy, {}))

    def grid_for(self, strategy: str) -> dict:
        return dict((self.raw.get("optimization_grid") or {}).get(strategy, {}))

    @property
    def evaluation(self) -> dict:
        return self.raw.get("evaluation", {})

    @property
    def db_path(self) -> Path:
        # Umgebungsvariable TRADING_DB hat Vorrang - so koennen CI (GitHub Actions) und
        # das Cloud-Dashboard (Streamlit) auf eine schlanke, separate Paper-DB zeigen,
        # ohne config.yaml zu aendern. Relativer Pfad zaehlt ab dem Projekt-Root.
        env = os.environ.get("TRADING_DB")
        if env:
            p = Path(env)
            return p if p.is_absolute() else ROOT / p
        default = ROOT / self.raw.get("database", {}).get("path", "trading.db")
        # Cloud-Komfort: liegt keine lokale trading.db vor, aber die vom CI-Bot
        # committete schlanke cloud/paper.db, dann diese verwenden (Streamlit-Cloud).
        if not default.exists() and (ROOT / "cloud" / "paper.db").exists():
            return ROOT / "cloud" / "paper.db"
        return default


def load_settings(path: Path | str = DEFAULT_CONFIG_PATH) -> Settings:
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    _load_dotenv(ROOT / ".env")
    return Settings(raw=raw, path=path)


def _load_dotenv(env_path: Path) -> None:
    """Minimaler .env-Loader, damit python-dotenv keine harte Abhaengigkeit ist.
    Bestehende Umgebungsvariablen werden nicht ueberschrieben."""
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
