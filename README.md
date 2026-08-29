# Trading-Strategie-Dashboard

![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Mode](https://img.shields.io/badge/mode-paper--only-orange)
![Live trading](https://img.shields.io/badge/live%20trading-locked-red)

> Wertet etablierte Handelsstrategien parallel über mehrere Märkte aus, prüft ihre Robustheit statistisch — Backtest → Paper → Live, ehrlich gerechnet.

## Überblick

Lokale Anwendung, die mehrere etablierte Handelsstrategien parallel auf mehreren
Märkten (Krypto, US-Aktien, Forex) auswertet, ihre Robustheit statistisch prüft
und in drei getrennten Modi läuft: **Backtest → Paper → Live**.

> ⚠️ **RISIKOHINWEIS — Dies ist ein Analysewerkzeug, KEINE Finanzberatung.**
> Backtest-Ergebnisse sind keine Zusage künftiger Gewinne; Handel kann zum
> **Totalverlust** führen (bei Hebel darüber hinaus). Nutzung vollständig auf
> eigenes Risiko, keine Haftung. „85 % Wahrscheinlichkeit profitabel" heißt nur:
> *unter der Annahme, dass die Zukunft der gemessenen Vergangenheit gleicht* —
> diese Annahme ist regelmäßig falsch.

**Warum keine YouTube-Zahlen:** Behauptungen wie „91 % Trefferquote" sind fast
immer overfittet oder durch Survivorship-Bias verzerrt. Deshalb rechnet dieses
Projekt jede Kennzahl selbst nach — mit Walk-Forward-Validierung (Out-of-Sample)
und Monte-Carlo-Konfidenzintervallen statt einer einzelnen Marketing-Zahl.

## Features

**Drei Modi** (Default: **paper**; ein automatischer Wechsel nach live existiert im Code nicht):

| Modus | Was passiert | Aktivierung |
|---|---|---|
| **backtest** | Nur historische Auswertung, keine Orders | `mode: backtest` |
| **paper** | Strategien laufen auf Live-Daten gegen ein simuliertes Konto | **Default** |
| **live** | Echte Orders an einen echten Broker | dreifache Freigabe |

**Der Weg nach Live** braucht **drei** Bedingungen gleichzeitig: `mode: live` in der
Config, interaktive Bestätigung (`cli.py enable-live` fragt einen wörtlichen Satz +
Zufallscode ab) und echte API-Keys in `.env`. Fehlt eine, bleibt der Modus **paper**;
das Gate ist prozesslokal (**nach Neustart wieder Paper**).

> **Stand heute ist der Live-Order-Pfad bewusst nicht implementiert** —
> [execution/live.py](execution/live.py) wirft `LiveTradingNotEnabled` (Absicht, kein
> vergessener Code).

**Strategien (Startset):** `ema_crossover, rsi_mean_reversion, bollinger_breakout,
macd_momentum, vwap_reversion, opening_range_breakout, donchian_breakout,
support_resistance, supertrend, keltner_pullback, stochastic_reversion, dmi_trend,
ichimoku_trend, connors_rsi2, williams_r_reversion, cci_reversion, roc_momentum` —
Indikatoren from scratch (pandas/numpy), Kategorien Trend / Mean-Reversion / Breakout /
Momentum / Struktur.

**Der „Funktioniert gerade"-Score (keine Black Box):**

```
Score = ( 0.35·Edge + 0.30·Robustheit + 0.20·Regime + 0.15·Recency ) × Konfidenzfaktor × 100
```

Edge = OOS-Erwartungswert je Trade (Walk-Forward); Robustheit = Monte-Carlo
P(profitabel), gedämpft durch Worst-Case-Drawdown; Regime = passt ADX/Volatilität zur
Kategorie; Recency = letzte 3 OOS-Fenster; Konfidenzfaktor = `min(OOS-Trades/30, 1)`.
Explizite Warnungen bei <30 OOS-Trades, Walk-Forward-Effizienz <0,5, >5 % Ruin-Pfaden
oder synthetischer Datenquelle. Implementierung: [stats/score.py](stats/score.py).

**Risikomanagement (Pflichtmodul, kein Bypass — `RiskManager.check_order()`):**

| Limit | Default | Wirkung |
|---|---|---|
| Risiko pro Trade | 1 % | Ordergröße wird reduziert |
| Max. offene Positionen | 3 | Order abgelehnt |
| Max. Tagesverlust | 3 % | **Circuit Breaker** → Rückfall auf Paper, glattstellen, Loop stoppt |
| Max. Gesamtdrawdown | 15 % | Circuit Breaker |
| Stop-Loss | Pflicht | Order ohne SL abgelehnt |

**Dashboard:** Modus-Banner in Ampelfarbe, Ranking mit Monte-Carlo-Konfidenzintervall
statt Einzelprozent, Score-Zerlegung, Markt-Heatmap, Kill-Switch in der Sidebar,
Audit-Log-Tab. Ein Live-Paper-Trader-Tab zeigt Status/Positionen/Kapitalkurve alle 5 s.

## Architektur

```
trading-dashboard/
├── strategies/     Strategien, je eine Datei, gemeinsames Interface + Registry
├── data/           Marktdaten-Ingestion (ccxt / yfinance) mit CSV-Cache
├── backtest/       Engine, Walk-Forward, Monte-Carlo, Orchestrierung
├── stats/          Kennzahlen, Regime-Erkennung, "Funktioniert gerade"-Score
├── execution/      Broker-Adapter (paper aktiv, live gesperrt) + Live-Gate + Paper-Loop
├── risk/           Risikolimits, Circuit Breaker, Kill-Switch, Audit
├── dashboard/      Streamlit-UI
├── config/         config.yaml + Settings-Loader (.env für Secrets)
├── core/           Typen, Indikatoren, SQLite-Store
└── tests/          Testsuite
```

**Konservative Backtest-Annahmen:** Signal von Bar *t* wird zum **Open von t+1**
ausgeführt (kein Look-ahead); bei Stop+Ziel im selben Bar **gewinnt der Stop**
(pessimistisch); Gebühren + Slippage auf jede Seite; Hebel hart gedeckelt. Neue
Strategie = eine Datei nach dem `Strategy`-Interface + Eintrag in die `REGISTRY`;
`test_no_lookahead` prüft die Look-ahead-Freiheit automatisch.

**Bewusste Abweichungen:** keine TA-/Backtest-Bibliothek (eigene, deterministische
Indikatoren/Engine für versionsstabile R-Multiple-Buchführung); Live-Trading als Stub;
synthetischer Datenfallback (überall als solcher markiert, ohne Aussagekraft). Mit
`data.require_real: true` (Default) werden Kombinationen ohne echte Datenquelle
**übersprungen — nie synthetisch ersetzt** (0 % synthetische Daten in der Auswertung).

## Quickstart

**Windows (ein Klick):** Doppelklick auf **`START.bat`** — legt beim ersten Start die
venv an, installiert Abhängigkeiten und zeigt ein Menü (Auswertung / Dashboard /
Dauerbetrieb / Paper-Trading / Tests).

**Manuell / Linux / macOS:**

```bash
cd trading-dashboard
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python cli.py evaluate                               # Backtest + Walk-Forward + Monte-Carlo
python cli.py dashboard                              # Streamlit auf http://localhost:8501
python cli.py paper --interval 300                   # Paper-Loop (kein echtes Geld)
python cli.py serve --interval 300 --reeval-hours 6  # Dauerbetrieb: Paper + Auto-Auswertung
```

Echte Börsendaten via `pip install ccxt yfinance`. `.env` (aus `.env.example`) enthält
**nur** API-Keys, steht in `.gitignore` und darf nie committet werden.

## Tests

```bash
python -m pytest -q
```

Abgedeckt u. a.: Look-ahead-Freiheit jeder Strategie, Ausführung zum Folge-Open,
Stop-Loss begrenzt Verlust auf ≈ 1R, Monte-Carlo-Intervallgrenzen, Overfitting-Warnung,
**Circuit Breaker schaltet nachweislich von Live auf Paper zurück**, und das Live-Gate
(gesperrt per Default, jede der drei Bedingungen einzeln geprüft).

## Lizenz

MIT — siehe [LICENSE](LICENSE). © 2026 Sebastian Renker.
