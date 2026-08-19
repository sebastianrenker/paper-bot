# Trading-Strategie-Dashboard

Lokale Anwendung, die mehrere etablierte Handelsstrategien parallel auf mehreren
Märkten (Krypto, US-Aktien, Forex) auswertet, ihre Robustheit statistisch prüft und
in drei getrennten Modi läuft: **Backtest → Paper → Live**.

---

## ⚠️ RISIKOHINWEIS — BITTE ZUERST LESEN

> **Dies ist ein Analysewerkzeug, KEINE Finanzberatung.**
>
> - **Backtest-Ergebnisse sind keine Zusage künftiger Gewinne.** Jede historische
>   Auswertung beschreibt die Vergangenheit; Märkte ändern ihr Verhalten.
> - **Handel kann zum Totalverlust des eingesetzten Kapitals führen** — bei
>   gehebelten Produkten auch zu Verlusten über das eingesetzte Kapital hinaus.
> - **Die Nutzung erfolgt vollständig auf eigenes Risiko.** Weder der Code noch die
>   angezeigten Kennzahlen stellen eine Anlageempfehlung dar.
> - Die Autoren übernehmen keine Haftung für Verluste aus der Nutzung dieser Software.
>
> Wenn eine Zahl in diesem Dashboard „85 % Wahrscheinlichkeit profitabel" sagt,
> heißt das: *unter der Annahme, dass die Zukunft der gemessenen Vergangenheit
> statistisch gleicht.* Diese Annahme ist regelmäßig falsch.

**Warum dieses Tool keine YouTube-Zahlen übernimmt:** Behauptungen wie „91 %
Trefferquote" sind fast immer overfittet oder durch Survivorship-Bias verzerrt.
Deshalb rechnet dieses Projekt jede Kennzahl selbst nach — mit Walk-Forward-Validierung
(Out-of-Sample) und Monte-Carlo-Konfidenzintervallen statt einer einzelnen Marketing-Zahl.
Eine hohe Win-Rate allein ist bedeutungslos: 90 % Gewinner und ein katastrophaler
Verlierer ergeben eine Verlustserie.

---

## Schnellstart (Windows, ein Klick)

Doppelklick auf **`START.bat`**. Beim ersten Start legt es automatisch die
virtuelle Umgebung an, installiert alle Abhängigkeiten und zeigt danach ein Menü:

```
[1] Auswertung aktualisieren   [2] Dashboard öffnen   [3] Dauerbetrieb
[4] Paper-Trading (ein Durchlauf)   [5] Tests   [6] Beenden
```

Kein Tippen nötig — alles läuft über das Menü.

## Schnellstart (manuell / Linux / macOS)

```bash
cd trading-dashboard

python -m venv .venv
.venv\Scripts\activate            # Windows;  Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

python cli.py evaluate            # Backtest + Walk-Forward + Monte-Carlo -> SQLite
python cli.py dashboard           # Streamlit-Dashboard auf http://localhost:8501
python cli.py paper --interval 300   # Paper-Trading-Loop (kein echtes Geld)
python cli.py serve --interval 300 --reeval-hours 6   # Dauerbetrieb: Paper + Auto-Auswertung

python -m pytest -q               # Testsuite (82 Tests)
```

**Dauerbetrieb (`serve`)** ist der „einschalten und laufen lassen"-Modus: der
Paper-Loop handelt simuliert weiter, während im Hintergrund alle paar Stunden die
komplette Auswertung **und das Portfolio** neu gerechnet werden — so ist alles
**immer auf dem aktuellsten Stand**. Das Dashboard in einem zweiten Fenster/Tab
(`dashboard`) zeigt die Ergebnisse dann stets aktuell. `serve` bleibt immer im
Paper-Modus.

**Portfolio (`portfolio`)** bündelt die validierten, wenig korrelierten Kombinationen
zu einem gemeinsamen Portfolio und verteilt das Risiko (Inverse-Volatilitäts-Gewichtung).
Das ist der einzige seriöse Weg, das *Gesamt*-Ergebnis zu verbessern: Diversifikation
senkt Drawdown und Schwankung. Sie macht aus Verlierern aber **keine** Gewinner — die
Portfolio-Erwartung ist der gewichtete Durchschnitt der Einzel-Erwartungen. Bestehen zu
wenige Kombinationen die Qualitätshürden, wird das offen gesagt.

Ohne installiertes `ccxt`/`yfinance` läuft alles auf **synthetischen** Daten. Die sind
für Entwicklung und Tests gedacht und werden in CLI und Dashboard überall als solche
markiert — Ergebnisse darauf haben **keinerlei** Aussagekraft für echte Märkte.

### Nur echte Börsendaten (strikter Modus)

In `config.yaml` ist `data.require_real: true` gesetzt. Damit gilt:

- Jeder Abruf wird bei Drosselung/Netzfehler **mehrfach mit Backoff wiederholt**
  (Rate-Limiting bei vielen Symbolen abgefangen); die ccxt-Verbindung wird über alle
  Abrufe wiederverwendet.
- Lässt sich für eine Kombination **keine** echte Datenquelle laden, wird sie
  **übersprungen** — niemals synthetisch ersetzt. So enthält die Auswertung
  garantiert **0 % synthetische** Daten.
- Erfolgreiche Abrufe werden als CSV gecacht und beim nächsten Lauf wiederverwendet.

Auf `false` gesetzt, greift wieder der synthetische Fallback (für Offline-Arbeit).

---

## Modi

| Modus | Was passiert | Aktivierung |
|---|---|---|
| **backtest** | Nur historische Auswertung, keine Orders | `mode: backtest` |
| **paper** | Strategien laufen auf Live-Daten gegen ein simuliertes Konto | **Default** |
| **live** | Echte Orders an einen echten Broker | dreifache Freigabe, siehe unten |

Der Default ist **paper**. Ein automatischer Wechsel nach live existiert im Code nicht —
auch nicht, wenn eine Strategie außergewöhnlich gut performt.

### Der Weg nach Live (drei Bedingungen, alle gleichzeitig)

1. **Config-Flag:** `mode: live` in `config/config.yaml`
2. **Manuelle Bestätigung:** `python cli.py enable-live --market crypto`
   fragt interaktiv nach dem wörtlichen Satz
   *„Ich verstehe, dass echtes Geld eingesetzt wird"* plus einem zufällig erzeugten
   Bestätigungscode zum Abtippen.
3. **Echte API-Keys** des jeweiligen Marktes in `.env`

Fehlt eine davon, bleibt der effektive Modus **paper** — siehe
`Settings.effective_mode` in [config/settings.py](config/settings.py) und das Gate in
[execution/gate.py](execution/gate.py). Das Gate ist prozesslokal: **nach jedem
Neustart ist wieder Paper aktiv.**

> **Stand heute ist der Live-Order-Pfad bewusst nicht implementiert.**
> [execution/live.py](execution/live.py) definiert die Adapter für ccxt / Alpaca / OANDA,
> wirft aber `LiveTradingNotEnabled`. Das ist Absicht (Umsetzungsplan Schritt 7.7) und
> kein vergessener Code. Erst freischalten, wenn der Paper-Betrieb über einen längeren
> Zeitraum nachweislich stabil lief.

---

## Architektur

```
trading-dashboard/
├── strategies/     8 Strategien, je eine Datei, gemeinsames Interface + Registry
├── data/           Marktdaten-Ingestion (ccxt / yfinance) mit CSV-Cache
├── backtest/       Engine, Walk-Forward, Monte-Carlo, Orchestrierung
├── stats/          Kennzahlen, Regime-Erkennung, "Funktioniert gerade"-Score
├── execution/      Broker-Adapter (paper aktiv, live gesperrt) + Live-Gate + Paper-Loop
├── risk/           Risikolimits, Circuit Breaker, Kill-Switch, Audit
├── dashboard/      Streamlit-UI
├── config/         config.yaml + Settings-Loader (.env für Secrets)
├── core/           Typen, Indikatoren, SQLite-Store
└── tests/          82 Tests
```

### Neue Strategie hinzufügen (Plug-in-Prinzip)

```python
# strategies/meine_strategie.py
from strategies.base import Strategy

class MeineStrategie(Strategy):
    name = "meine_strategie"
    category = "trend"          # trend | mean_reversion | breakout | momentum | structure

    @staticmethod
    def default_params() -> dict:
        return {"period": 20}   # zugleich die Whitelist erlaubter Parameter

    def compute(self, ohlcv):
        out = self.empty_frame(ohlcv.index)
        out["direction"] = ...   # -1 / 0 / 1
        out["confidence"] = ...  # 0.0 .. 1.0
        return out
```

Klasse in `strategies/__init__.py` in die `REGISTRY` eintragen — fertig. Backtest-,
Execution- und UI-Code bleiben unberührt.

**Look-ahead-Regel:** `compute()` darf pro Zeile nur Daten bis einschließlich dieser
Zeile verwenden. Der Test `test_no_lookahead` prüft das für jede registrierte Strategie
automatisch, indem er das Signal auf einem Teilfenster gegen das Vollfenster vergleicht.

---

## Strategien (Startset)

| # | Modul | Kategorie | Kurz |
|---|---|---|---|
| 1 | `ema_crossover` | Trend | EMA-Kreuzung + optionaler Langfrist-Trendfilter |
| 2 | `rsi_mean_reversion` | Mean-Reversion | RSI-Extrem + Divergenz-Check |
| 3 | `bollinger_breakout` | Breakout | Ausbruch aus Squeeze mit Volumenbestätigung |
| 4 | `macd_momentum` | Momentum | MACD-Kreuzung + Histogramm-Beschleunigung |
| 5 | `vwap_reversion` | Mean-Reversion | Rückkehr zum Session-VWAP (nur Intraday) |
| 6 | `opening_range_breakout` | Breakout | Ausbruch aus der Opening Range |
| 7 | `donchian_breakout` | Trend | N-Perioden-Hoch/Tief, Turtle-Style |
| 8 | `support_resistance` | Struktur | Rejection an bestätigten Swing-Zonen |
| 9 | `supertrend` | Trend | ATR-Supertrend-Band mit ADX-Filter |
| 10 | `keltner_pullback` | Trend | Pullback zum 20-EMA im ADX-Trend |
| 11 | `stochastic_reversion` | Mean-Reversion | Stochastik-Kreuzung aus dem Extrem |
| 12 | `dmi_trend` | Trend | +DI/-DI-Kreuzung mit ADX-Bestätigung |
| 13 | `ichimoku_trend` | Trend | Preis vs. Wolke + Tenkan/Kijun |
| 14 | `connors_rsi2` | Mean-Reversion | Larry Connors RSI(2) mit SMA200-Trendfilter |
| 15 | `williams_r_reversion` | Mean-Reversion | Williams %R im Extrem, mit Trendfilter |
| 16 | `cci_reversion` | Mean-Reversion | Rückkehr aus CCI-Extrem (< −100 / > +100) |
| 17 | `roc_momentum` | Momentum | Time-Series-Momentum (N-Perioden-Rendite) |

---

## Der „Funktioniert gerade"-Score — keine Black Box

```
Score = ( 0.35 · Edge  +  0.30 · Robustheit  +  0.20 · Regime  +  0.15 · Recency )
        × Konfidenzfaktor × 100
```

| Komponente | Woraus |
|---|---|
| **Edge** | Out-of-Sample-Erwartungswert je Trade (R) aus der Walk-Forward-Analyse |
| **Robustheit** | Monte-Carlo: P(profitabel), gedämpft durch den Worst-Case-Drawdown (95 %) |
| **Regime** | Passt das aktuelle Marktumfeld (ADX, Volatilitätsperzentil) zur Kategorie? |
| **Recency** | Letzte 3 OOS-Fenster gegenüber dem eigenen Durchschnitt |
| **Konfidenzfaktor** | `min(OOS-Trades / 30, 1.0)` — wenige Trades drücken den Score hart |

Die Zerlegung wird im Dashboard je Kombination als Balkendiagramm plus Formel-Text
angezeigt. Implementierung: [stats/score.py](stats/score.py).

**Explizite Warnungen** erscheinen bei: unter 30 Out-of-Sample-Trades, Walk-Forward-Effizienz
unter 0.5 (Overfitting-Verdacht), über 5 % Ruin-Pfaden in der Monte-Carlo-Simulation und
bei synthetischer Datenquelle.

### Was die Statistik-Schicht konkret tut

- **Rolling Backtest** über 90 / 180 / 365 Tage, getrennt je Markt und Timeframe
- **Walk-Forward:** Parameterwahl nur auf dem Trainingsfenster, berichtet wird
  ausschließlich Out-of-Sample
- **Kennzahlen:** Win-Rate, Profit-Faktor, Ø R-Multiple, Sharpe, Sortino, Max Drawdown,
  Trade-Anzahl — unter 30 Trades wird `statistically_significant = False` gesetzt
- **Monte-Carlo:** 1.000 Bootstrap-Pfade über die Trade-Verteilung → 90 %-Konfidenz­intervall
  für Return und Drawdown, plus Ruin-Wahrscheinlichkeit
- **Persistenz:** alle Läufe in SQLite (`trading.db`) für Historie

### Konservative Annahmen der Backtest-Engine

Damit die Zahlen nicht geschönt sind:

- Signal von Bar *t* wird zum **Open von Bar t+1** ausgeführt (kein Look-ahead)
- Liegen Stop-Loss und Take-Profit im selben Bar, **gewinnt der Stop** (die
  Intrabar-Reihenfolge ist unbekannt — pessimistische Annahme)
- **Gebühren und Slippage** auf jede Seite; Slippage verschlechtert den Fill immer
- Positionsgröße folgt strikt dem Risikomodell, Hebel ist hart gedeckelt

---

## Risikomanagement (Pflichtmodul)

Jede Order läuft durch `RiskManager.check_order()` — es gibt keinen Bypass-Pfad.

| Limit | Default | Wirkung bei Verletzung |
|---|---|---|
| Risiko pro Trade | 1 % | Ordergröße wird reduziert (nicht abgelehnt) |
| Max. offene Positionen | 3 | Order abgelehnt |
| Max. Tagesverlust | 3 % | **Circuit Breaker** → Modus fällt auf Paper zurück, Positionen glattgestellt, Loop gestoppt |
| Max. Gesamtdrawdown | 15 % | Circuit Breaker |
| Stop-Loss | Pflicht | Order ohne SL wird abgelehnt |

`RiskLimits` weist bereits im Konstruktor mehr als 5 % Risiko pro Trade zurück.
Der **Kill-Switch** ist im Dashboard-Sidebar als Button verfügbar und schaltet sofort
auf Paper. Entsperren geht nur manuell über `reset(confirm=True)` — und setzt den Modus
*nicht* auf Live zurück.

Alle Orders, Ablehnungen und Modus-Wechsel landen mit Zeitstempel, Strategie, Signal-Begründung
und Ergebnis im Audit-Log (`audit_log`-Tabelle, im Dashboard als eigener Tab).

---

## Live Paper-Trader (sehen, was der Bot tut)

Doppelklick auf **`PAPERTRADER.bat`** — startet den Paper-Loop **und** das Dashboard
zusammen. Im Tab **„Live Paper-Trader"** siehst du, alle 5 Sekunden aktualisiert:

- **Status** (LIVE / gestoppt), aktuelles Kapital, offene Positionen, Tagesverlust
- **Offene Positionen** mit Einstieg, aktuellem Kurs, Stop, Ziel und unrealisiertem Gewinn
- **Live-Kapitalkurve** (Paper)
- **Aktivitäts-Feed**: jede Order, Ablehnung, Schließung und der Circuit Breaker — in Echtzeit

Der Loop schreibt seinen Zustand in die SQLite-DB; das Dashboard (eigener Prozess)
liest ihn live. Alles im **Paper-Modus — kein echtes Geld**.

## Dashboard

`python cli.py dashboard`

- **Modus-Banner** in Ampelfarbe, ganz oben („PAPER MODE — kein echtes Geld")
- **Ranking** aller Strategie/Markt/Timeframe-Kombinationen mit Ampel, Monte-Carlo-Konfidenzintervall
  (`-12.3 % ... +48.1 %`) statt einer einzelnen Prozentzahl, und einer Spalte „Belastbar"
- **Detailansicht:** Score-Zerlegung, kumulierte R-Kurve, Drawdown-Chart, Trade-Historie,
  Paper-Equity im Vergleich
- **Markt-Heatmap:** bester Score je Strategie und Markt/Timeframe
- **Sidebar:** Kapital, Tagesverlust gegen Limit als Fortschrittsbalken, **Kill-Switch**
- **Audit-Log** als eigener Tab

---

## Konfiguration

- `config/config.yaml` — Modus, Kapital, Risikolimits, Universe, Strategie-Parameter,
  Optimierungs-Grid. Halte das Grid klein: große Grids überfitten.
- `.env` (aus `.env.example`) — **nur** API-Keys. Steht in `.gitignore` und darf
  niemals committet werden.

---

## Tests

```bash
python -m pytest -q      # 82 Tests
```

Abgedeckt sind unter anderem:

- Signalbereiche, Parameter-Whitelist und **Look-ahead-Freiheit jeder Strategie**
- Backtest-Engine: Ausführung zum Folge-Open, Stop-Loss begrenzt Verlust auf ≈ 1R,
  Kosten verschlechtern das Ergebnis
- Monte-Carlo: Intervallgrenzen, Zuverlässigkeitsflag bei kleiner Stichprobe
- Score: kleine Stichprobe drückt den Score, Overfitting-Warnung greift
- **Circuit Breaker schaltet nachweislich von Live auf Paper zurück**
  (`test_circuit_breaker_switches_live_back_to_paper`) und stellt im Loop Positionen glatt
- Live-Gate: gesperrt per Default, jede der drei Bedingungen einzeln geprüft,
  Live-Adapter bleibt auch nach Freischaltung nicht instanziierbar

---

## Abweichungen von der ursprünglichen Vorgabe

- **Keine TA-Bibliothek** (`pandas-ta` o. ä.): alle Indikatoren sind in
  [core/indicators.py](core/indicators.py) mit pandas/numpy implementiert. Grund:
  deterministisch, versionsstabil, keine Binary-Dependencies, vollständig testbar.
- **Keine Backtest-Bibliothek** (`backtrader`, `vectorbt`): eigene, schlanke Engine,
  weil die R-Multiple-Buchführung und die pessimistischen Fill-Annahmen der Kern der
  Statistik-Schicht sind und nicht in fremder Semantik versteckt werden sollten.
- **Live-Trading ist ein Stub** — bewusst, siehe oben.
- **`python-dotenv` ist keine harte Abhängigkeit**; ein minimaler `.env`-Loader steckt
  in `config/settings.py`.
- **Kein Scheduler-Daemon:** der Paper-Loop ist eine einfache Schleife mit `--interval`.
  APScheduler oder ein Systemd-Timer lässt sich darüber legen.
- **Synthetischer Datenfallback**, damit das Projekt ohne Netzwerk und ohne Keys
  vollständig lauffähig und testbar ist — überall deutlich als solcher markiert.

---

## Nächste sinnvolle Schritte

1. Echte Datenquellen anbinden (`pip install ccxt yfinance`) und die Auswertung auf
   realen Kursen wiederholen — die synthetischen Zahlen sind bedeutungslos.
2. Paper-Loop über mehrere Wochen laufen lassen und die Paper-Equity-Kurve gegen die
   Backtest-Kurve halten. Klaffen sie auseinander, ist der Backtest zu optimistisch.
3. Erst danach überhaupt über Schritt 7.7 (Live) nachdenken — und dann mit einem
   Betrag, dessen Totalverlust folgenlos wäre.
