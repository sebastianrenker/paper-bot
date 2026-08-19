# Master-Prompt: Memecoin-Paper-Trading-Bot (in neue Session einfügen)

> Kopiere alles unter der Linie in eine neue Claude-Code-Session (am besten in einem
> leeren Projektordner, z. B. `memecoin-bot`).

---

Baue ein **Memecoin-Paper-Trading-Analysewerkzeug** in Python (Ordner `memecoin-bot`).
Es ist ein Lern-/Analysewerkzeug mit **simuliertem Geld**, kein Live-Handel. Übernimm die
komplette Architektur, Qualitäts- und Ehrlichkeits-Prinzipien von unten — sie stammen aus
einem funktionierenden Vorgängerprojekt (Krypto-Majors) und haben sich bewährt.

## Nicht verhandelbar (Sicherheit & Ehrlichkeit)
- **Kein Live-Handel.** Ein Live-Broker-Adapter existiert nur als gesperrter Stub, der
  `LiveTradingNotEnabled` wirft. `mode: paper` ist Default. Kein automatischer Wechsel zu Live.
- **Keine Gewinnversprechen, keine Prognosen, keine Anlageberatung.** Jede angezeigte
  Kennzahl bekommt Kontext (Zeitraum, Datenquelle). Wo Evidenz schwach ist, sag es.
- **Memecoins sind extrem riskant:** die meisten gehen gegen null, viele sind Rugpulls,
  Liquidität ist oft dünn, Slippage hoch, historische Daten kurz/lückenhaft. Das Tool muss
  das **überall laut und ehrlich** kommunizieren (Dashboard-Banner, README, Reports).
  Backtests sind hier noch **weniger** vorhersagekräftig als bei Majors.
- **Risikolimits sind Pflicht** (Circuit Breaker etc., s. u.) und dürfen nie gelockert werden.
- **Jeder Bugfix bekommt einen Regressionstest**, der ohne den Fix fehlschlägt. Kein
  bestehender Test wird an einen Bug angepasst. Am Ende: `python -m pytest -q` komplett grün.
- **Nur echte Daten** (kein synthetischer Fallback im scharfen Modus): schlägt ein Abruf
  fehl, wird die Kombination übersprungen statt gefälscht (`data.require_real: true`).

## Memecoin-Spezifika (das ist der Unterschied zum Vorgänger)
1. **Datenquellen** (modular, austauschbar, mit Cache + Retry/Backoff):
   - **CEX-gelistete Memecoins** (DOGE, SHIB, PEPE, WIF, BONK, FLOKI …) via `ccxt`
     (Binance/Bybit) — echte OHLCV, am zuverlässigsten. **Damit anfangen.**
   - **On-chain/DEX-Memecoins** (Solana/pump.fun/Raydium, Ethereum): optionaler Adapter
     für DexScreener / GeckoTerminal / Birdeye (öffentliche APIs). Ehrlich dokumentieren:
     oft nur kurze Historie, unzuverlässige/illiquide Kerzen → viele Kombinationen fallen
     durch die Datenintegritäts-Prüfung. Das ist Absicht, kein Fehler.
   - Universe in `config.yaml` konfigurierbar; optional Auto-Discovery der Top-Memecoins
     nach Volumen/Liquidität (mit Mindest-Liquiditäts- und Mindest-Alter-Filter).
2. **Angepasste Annahmen:** deutlich höhere **Gebühren + Slippage** (DEX-Realität), engere
   Positionsgrößen, ATR-basierte Stops (Volatilität ist extrem). Pessimistische
   Kosten-Variante (×2) als Standard-Robustheitscheck im Evaluate anbieten.
3. **Schutzfilter vor Handel:** Mindest-Liquidität, Mindest-Handelsalter, Volumen-Filter,
   optional simple Rugpull-Heuristiken (z. B. Preis-Kollaps-Erkennung). Als Risikofilter,
   nicht als Gewinngarantie framen.

## Architektur & Module (übernehmen)
```
memecoin-bot/
├── strategies/      # eine Datei je Strategie, gemeinsames Interface + Registry (Plug-in)
├── data/            # Datenquellen (ccxt + DEX), Cache mit Retry/Backoff, strikter Echtdaten-Modus
├── backtest/        # engine, walkforward, montecarlo, stress, optimize, evaluate
├── stats/           # metrics, regime, score, portfolio
├── execution/       # base, paper (Broker), live (gesperrter Stub), gate, paper_loop
├── risk/            # manager (Circuit Breaker/Kill-Switch/Position-Sizing), vault
├── core/            # types, indicators (selbst implementiert, kein pandas-ta), store (SQLite)
├── config/          # config.yaml + settings.py (.env für evtl. Keys)
├── dashboard/       # Streamlit-App (Live-Paper-Trader, Ranking, Portfolio, Charts, Audit)
└── tests/           # Regressionstest je Bug + Feature
```

## Backtest & Validierung (genau so, inkl. der Bugfixes)
- **Ereignisbasierte Engine**, konservativ: Signal von Bar t wird zum **Open von Bar t+1**
  ausgeführt (kein Look-ahead). Stop VOR Take-Profit im selben Bar (pessimistisch).
  Gebühren + Slippage je Seite. Positionsgröße = risk_per_trade × Equity / Stop-Abstand,
  Hebel hart gedeckelt — **und die Einstiegsgebühr auf der GEDECKELTEN Menge berechnen**
  (`notional` nach dem Deckeln neu berechnen).
- **Nur abgeschlossene Kerzen** live/paper: eine `closed_bars()`-Funktion entfernt den noch
  laufenden letzten Balken (solange `jetzt < Open + Timeframe`); Signal/ATR nur darauf,
  Einstieg zum aktuellen Marktpreis.
- **Walk-Forward** (Parameter nur auf Train wählen, nur Out-of-Sample werten) + **Monte-Carlo**
  (Bootstrap, Konfidenzintervall + Ruin-Wahrscheinlichkeit) + **Regime-Erkennung** (ADX/Vola).
- **„Funktioniert gerade"-Score** (transparent, keine Black Box): Edge (OOS-Erwartung) +
  Robustheit (MC) + Regime-Passung + Recency, multipliziert mit einem Konfidenzfaktor aus
  der Trade-Zahl. Ampel grün/gelb/rot, mit Warnungen (zu wenige Trades, Overfitting-Verdacht).
- **Portfolio-Modul**: bündelt validierte, wenig korrelierte Kombinationen (Qualitätshürden,
  Korrelations-Auswahl, Inverse-Vol-Gewichtung). Ehrlich: Diversifikation senkt Drawdown,
  macht aus Verlierern keine Gewinner.
- **Selbst-Optimierung mit Overfitting-Wächter**: übernimmt Parameter NUR bei positivem
  OOS-Erwartungswert + WF-Effizienz ≥ 0.5 + genug Trades, sonst Ablehnung (Defaults).
- **Millionen-Trade-Stresstest** (Risiko-Quantifizierung, KEIN Gewinn-Beweis).

## Strategien (Startset, je als austauschbares Modul, parametrisierbar)
Trend/Momentum: ema_crossover, supertrend, donchian_breakout, dmi_trend, macd_momentum,
roc_momentum. Breakout: bollinger_breakout, keltner_pullback, opening_range_breakout.
Mean-Reversion: rsi_mean_reversion, connors_rsi2, stochastic_reversion, williams_r_reversion,
cci_reversion. Struktur: support_resistance. Indikatoren selbst in `core/indicators.py`
implementieren (pandas/numpy, keine TA-Bibliothek). **Memecoin-Hinweis:** Mean-Reversion ist
bei Memecoins gefährlicher (Trends können brutal weiterlaufen oder kollabieren) — im Score
niedrig gewichten/erwarten.

## Risikomanagement (Pflicht, inkl. der gelernten Fixes)
- Max. Risiko/Trade (Default 1 %), max. offene Positionen, **Tagesverlust-Circuit-Breaker**
  (Default 3 %) und Gesamtdrawdown-Kill-Switch. Jede Order braucht Stop-Loss.
- **Mindest-Stop-Abstand** (z. B. 0.25 % des Preises): zu enge Stops werden **abgelehnt**
  (nicht aufgeweitet) — verhindert den Stop-Loss-Endlos-Loop bei ATR≈0. Bei Memecoins ggf.
  größer wählen (Slippage/Spread breiter).
- **Balken-Debounce**: nach Stop/TP auf einem Balken darf dieselbe (Strategie, Symbol) auf
  DEMSELBEN Balken nicht neu eröffnen (erst auf einem neuen, später geschlossenen Balken).
- Positionen pro **(Strategie, Symbol)** tracken (nicht nur pro Symbol).
- **Circuit-Breaker-Trip beendet NICHT den Prozess**: der Loop geht in sicheren Wartezustand
  (tickt weiter, keine neuen Orders), bis `reset_breaker`. Trip-Status wird persistiert
  (überlebt Neustart/Tageswechsel; auto-Reset erst am neuen UTC-Tag, wenn nicht heute getript).
- Positionsgröße ggf. mit Signal-Konfidenz skalieren.

## Persistenz & Betrieb (übernehmen)
- **SQLite** (`store.py`) mit WAL + busy_timeout; Tabellen: evaluations, trades, audit_log,
  equity_points, positions_snapshot, heartbeat, control.
- **Zustands-Wiederherstellung bei Neustart** (`restore_broker_state`): Kapital + offene
  Positionen aus der DB laden, nicht bei jedem Start auf Kapital.initial zurückfallen.
- **`serve`-Dauerbetrieb** (steuerbar über `control`-Tabelle: start/stop/adapt/reset_breaker),
  **Doppelstart-Schutz** (frisches Heartbeat + running ⇒ zweiter Start bricht ab, `--force`).
  Selbst-Anpassungs-Thread mit **eigener DB-Verbindung** (nicht dieselbe wie der Haupt-Loop).
- **Config-Werte müssen die ausführenden Klassen erreichen** (Factory-Funktionen, die
  Broker/LoopConfig aus config.yaml bauen — nicht mit Default-Werten konstruieren).

## Dashboard (Streamlit)
- Modus-Banner (großes „PAPER — kein echtes Geld"), Kill-Switch, **Memecoin-Risiko-Banner**.
- Tabs: **Live Paper-Trader** (Auto-Refresh via `st.fragment(run_every=…)`: Status/Heartbeat,
  Kapital, offene Positionen, Live-Kapitalkurve, Aktivitäts-Feed, **Kerzenchart mit
  Kauf/Verkauf-Markern** via Altair), Ranking, Detail je Strategie (Score-Zerlegung,
  R-Verteilung, Monte-Carlo-Fächer), Portfolio, Markt-Heatmap, Audit-Log.
- Steuerknöpfe (Start/Stopp/Anpassen/Not-Aus); im Cloud-Nur-Lese-Modus (`CLOUD_READONLY=1`)
  ausblenden.

## Idiotensicher & Doku
- `cli.py doctor` (Klartext-Selbsttest: Python/Pakete/Config/DB/Datenquelle/Geld-Sicherheit).
- `Settings.validate()` mit verständlichen Meldungen; `main()` fängt alle Exceptions ab
  (freundliche Meldung statt Stacktrace). Windows: `START.bat` (Ein-Klick-Menü).
- `README.md`, `ANLEITUNG.md` (Laien), `SECURITY.md`, und ein Audit-/Recherche-Bericht mit
  Quellen und Quellenqualität (peer-reviewt vs. Blog).

## Gratis-Cloud-Deploy (übernehmen)
- **`cli.py export-active`** → `active_combos.yaml` (validierte Kombinationen).
- **`cli.py ci-tick`** → EIN Paper-Tick auf `active_combos.yaml`, persistiert in schlanke
  `cloud/paper.db`, schreibt `BOT_STATUS_REPORT.md`, WAL-Checkpoint.
- **GitHub Actions** (`.github/workflows/…`, Cron alle ~30 min, `requirements-ci.txt` schlank)
  committet Zustand zurück; `TRADING_DB`-Env + Fallback auf `cloud/paper.db`.
- **Streamlit Community Cloud** hostet `dashboard/app.py` (Env `CLOUD_READONLY=1`,
  `TRADING_DB=cloud/paper.db`) als öffentlichen, nur-lesenden Blick.
- Anleitung als `DEPLOY_GRATIS.md`. Öffentliches Repo (unbegrenzte Gratis-Actions).

## Umsetzungsreihenfolge
1. Gerüst + Strategie-Interface + 3 Strategien + Backtest-Engine + Tests.
2. Datenquellen (ccxt-Memecoins zuerst, echte Daten, strikter Modus).
3. Walk-Forward + Monte-Carlo + Score + Risikomanagement (inkl. aller Fixes oben).
4. Restliche Strategien, Portfolio, Selbst-Optimierung.
5. Paper-Loop + Persistenz + `serve` + Dashboard.
6. DEX-Datenadapter (optional), Memecoin-Filter (Liquidität/Alter/Rug).
7. Doctor/Doku/Idiotensicher, dann Gratis-Cloud-Deploy.
8. Durchgehend: pro Bug ein Regressionstest, `pytest -q` grün halten.

## Ehrliche Schlusslage, die du mir am Ende gibst
Keine Prognose. Sag klar: läuft der Bot mit echten Daten, was zeigt der erste
Backtest/Report, und die nüchterne Einordnung, dass **Memecoins überwiegend Verlust/Null
bedeuten** und ein positives Kurzzeit-Ergebnis Rauschen ist, kein Beleg für einen Vorteil.
Baue keinen „garantiert profitabel"-Schalter — den gibt es nicht.
