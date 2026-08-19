# Audit- & Recherche-Bericht

**Datum:** 2026-07-27 · **Stand Tests:** 170 grün (inkl. 8 neue Regressionstests)

> **Wichtig:** Dieser Bericht ist keine Anlageberatung und enthält keine
> Gewinnversprechen. Jede genannte Kennzahl steht mit Zeitraum, Markt und Quelle.
> Wo die Evidenz schwach oder gemischt ist, wird das ausdrücklich gesagt.
> Der Live-Handel bleibt unverändert deaktiviert — daran wurde nichts gelockert.

---

## Teil 1 — Gefundene und behobene Bugs

Jeder Bug wurde im Code gefunden, mit einem konkreten Szenario nachvollzogen, behoben
und mit einem Regressionstest abgesichert, der **ohne** den Fix fehlschlägt. Alle Fixes
sind in `tests/test_audit_fixes.py` abgedeckt; die gesamte Suite bleibt grün.

| # | Datei | Was war kaputt | Reales Szenario | Fix | Test |
|---|---|---|---|---|---|
| 1 | `cli.py` (`_make_loop_config`) | `PaperLoopConfig(poll_seconds=…)` übernahm **nur** das Intervall; `stop_atr_mult`, `take_profit_r`, `atr_period` blieben auf Klassen-Defaults. Config-Werte „verpufften". | Nutzer ändert `backtest.stop_atr_mult` in `config.yaml` → Backtest nutzt neuen Wert, der **Paper-/Live-Bot handelt aber weiter mit Stop×2.0**. Der Bot weicht damit von der validierten Strategie ab. | Factory `_make_loop_config`, die Stops/TP/ATR aus `backtest_config()` zieht; in `cmd_paper` **und** `cmd_serve` verdrahtet. | `test_loop_config_uses_config_values_not_defaults` |
| 2 | `execution/paper_loop.py` (`closed_bars`, `_process`) | **Look-Ahead:** ccxt/yfinance liefern live den **noch laufenden** letzten Balken mit. Er wurde für Signal, ATR und Stop-Größe benutzt. | Auf 4h-Krypto flackert das Signal innerhalb der laufenden Kerze; der Bot handelt auf unvollständigen Daten, die **nicht** der backtest-validierten Logik (nur geschlossene Kerzen) entsprechen. | `closed_bars()` entfernt den letzten Balken, solange `jetzt < Balken-Open + Timeframe`; Signal/ATR laufen nur auf geschlossenen Kerzen, Einstieg zum aktuellen Marktpreis. | `test_closed_bars_*`, `test_process_generates_signal_on_closed_bar_only` |
| 3 | `cli.py` (`cmd_serve`) | **Keine Sperre gegen zwei Bot-Prozesse** auf derselben DB. | Zweiter Klick auf „BOT STARTEN" (bei kurz veraltetem Heartbeat während einer Anpassung) startet einen zweiten `serve`-Prozess; beide schreiben Heartbeat/Positionen/Equity in dieselbe DB → verfälschte Kapitalkurve. | Start-Guard: frisches Lebenszeichen + `desired_state=running` ⇒ zweiter Start bricht mit Code 1 ab (Override nur mit `--force`). | `test_serve_refuses_second_instance_when_bot_alive` |
| 4 | `backtest/engine.py` (`run_backtest`) | Beim **Hebel-Deckel** wurde `qty` reduziert, aber `notional` (Basis der Einstiegsgebühr) **nicht** neu berechnet. | Bei sehr engen Stops (niedriger ATR) greift der Deckel; die Einstiegsgebühr wird auf einer bis zu 10× größeren, nie eröffneten Position berechnet → überhöhte Gebühren, **verfälschter PnL und `r_multiple`** (die Kern-Kennzahl der ganzen Bewertung). | Nach dem Deckeln `notional = qty * entry` neu berechnen. | `test_leverage_cap_fee_matches_capped_quantity` |

**Bereits in einem früheren Durchgang behoben** (verifiziert, nicht erneut angefasst):
Positionen pro `(Strategie, Symbol)` statt nur pro Symbol; Broker-Gebühren/Slippage aus
Config; Persistenz des Circuit-Breaker-Trips über Neustart/Tageswechsel; atomare
`combo_params`-Zuweisung im Anpassungs-Thread; WAL + eigene DB-Verbindung pro Thread;
Paper-Trades strukturiert in die `trades`-Tabelle.

**Kein bestehender Test wurde an einen Bug angepasst.** Es wurde kein Test gefunden, der
fehlerhaftes Verhalten fälschlich als korrekt festschrieb.

---

## Teil 2 — Evidenz zu den eingesetzten Strategiefamilien

Im Projekt (`strategies/`) aktiv: **Trendfolge/Momentum** (`ema_crossover`, `supertrend`,
`donchian_breakout`, `dmi_trend`, `ichimoku_trend`, `macd_momentum`, `roc_momentum`),
**Breakout** (`bollinger_breakout`, `opening_range_breakout`, `keltner_pullback`),
**kurzfristige Mean-Reversion** (`rsi_mean_reversion`, `connors_rsi2`,
`stochastic_reversion`, `williams_r_reversion`, `cci_reversion`) und **Struktur**
(`support_resistance`).

### 1. Time-Series-Momentum / Trendfolge — **starke, peer-reviewte Evidenz**
- **Quelle (stark):** Moskowitz, Ooi & Pedersen, *Time Series Momentum*, **Journal of
  Financial Economics 2012** (peer-reviewt). 58 liquide Futures, 1985–2009.
- **Kennzahlen:** Composite-Sharpe **≈ 1,28** (Buy-and-Hold ≈ 0,38) über den Zeitraum;
  12-Monats-Lookback / 1-Monat-Halten; positiv über Aktienindizes, Währungen, Rohstoffe,
  Anleihen; stark in Krisen (2008 positiv). Rückkehr-Effekt jenseits ~12 Monaten.
- **Für dieses Projekt:** stützt die Trend-Familie **im Prinzip** — ABER: getestet auf
  **Futures mit Monats-Horizont**, nicht auf 1h/4h-Krypto/Aktien wie hier. Die Evidenz
  überträgt sich **nicht 1:1** auf kurze Timeframes und einzelne Coins.
  Betroffen wäre `roc_momentum` (ist bereits eine TSMOM-Variante) sowie generell die
  Auswahl längerer Timeframes in `config.yaml`.
  Quellen: [SSRN 2089463](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2089463) ·
  [NYU Stern PDF](https://w4.stern.nyu.edu/facdir/lpederse/papers/TimeSeriesMomentum.pdf) ·
  [Alpha Architect](https://alphaarchitect.com/time-series-momentum-theory-and-evidence/)

### 2. Kurzfristige Mean-Reversion (RSI-2 / %R / CCI) — **gemischte, überwiegend nicht-akademische Evidenz**
- **Quelle (schwach–mittel):** überwiegend Praktiker-/Blog-Backtests
  ([QuantifiedStrategies](https://www.quantifiedstrategies.com/rsi-2-strategy/),
  [Williams %R](https://www.quantifiedstrategies.com/williams-r-trading-strategy/)), teils
  hohe Trefferquoten (70–80 %) auf **US-Aktienindizes im Tageschart**.
- **Vorsicht:** hohe Trefferquote ≠ Profitabilität (das Projekt zeigt das selbst); diese
  Quellen sind **nicht peer-reviewt** und selten kosten-/slippage-realistisch.
- **Für dieses Projekt:** `connors_rsi2` ist bewusst auf Tageschart+SMA200-Filter gebaut
  (nah am Original). Auf Krypto-Intraday ist die Evidenz **dünn**. Kein Handlungsbedarf,
  aber Erwartungen niedrig halten.

### 3. Volatility-Targeting / Positionsgrößen — **gemischte, teils peer-reviewte Evidenz**
- **Quelle (mittel–stark):** Harvey et al., *The Impact of Volatility Targeting*
  (Financial Analysts Journal / Man Group). Weitere: Bongaerts et al., *Conditional
  Volatility Targeting*, **FAJ 2020** (peer-reviewt).
- **Kennzahlen/Aussage:** Vol-Targeting **reduziert Max-Drawdowns** und Tail-Risk
  zuverlässig; den **Sharpe** verbessert es v. a. bei Risiko-Assets und dem
  Momentum-Faktor, **nicht** durchgängig (bei Value/Size/Rohstoffen kaum). Die
  konventionelle Variante kann das Vol-Ziel überschießen.
- **Für dieses Projekt:** Das Risikomodul skaliert bereits per **ATR** (eine Form von
  Vol-Sizing) und neuerdings per Konfidenz. Ein explizites **Portfolio-Vol-Target**
  (Gesamtrisiko auf Ziel-Vol steuern) wäre ein sinnvoller, aber optionaler Zusatz —
  betroffen: `risk/manager.py` (`position_size`) und `stats/portfolio.py` (Gewichtung).
  → **Empfehlung, kein Auto-Fix** (Trade-off Rendite/Drawdown).

### 4. Overfitting-Kontrolle — **starke, methodische Evidenz; teilweise umsetzbar**
- **Quelle (stark):** Bailey & López de Prado, *The Deflated Sharpe Ratio* (2014) und
  *The Probability of Backtest Overfitting*. Kernaussagen: schon **~3 Backtest-Varianten**
  erzeugen scheinbar signifikante Zufalls-Gewinner; die **Deflated Sharpe Ratio** und
  **Minimum Backtest Length** korrigieren für die Zahl der Versuche.
- **Für dieses Projekt:** Es gibt bereits einen Walk-Forward-**Overfitting-Wächter**
  (Effizienz-Schwelle) und eine Trade-Zahl-Konfidenz — gut. **Nicht** berücksichtigt wird
  aktuell die **Anzahl der getesteten Grid-Kombinationen** (`optimization_grid`): je meh
  Varianten `run_optimization` prüft, desto größer die Selektions-Verzerrung.
  → **Empfehlung:** einen Trial-Count-Abschlag (Richtung Deflated Sharpe) in
  `stats/score.py` / `backtest/optimize.py` ergänzen. Klarer Nutzen, aber Design-Entscheid.
  Quellen: [SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) ·
  [Probability of Backtest Overfitting (PDF)](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)

### 5. Realistische Kosten/Slippage — **Praktiker-Konsens (nicht peer-reviewt, aber breit)**
- **Aussage:** „Verdopple deine angenommenen Kosten; überlebt die Strategie das noch,
  ist der Vorteil eher echt." Häufigste Verlustursachen bei Bots: (a) zu viele Varianten
  getestet, (b) Backtest füllt zum Mittelkurs, live zahlt man den Spread, (c) neues
  Marktregime.
- **Für dieses Projekt:** Engine modelliert Gebühren **und** Slippage je Seite (gut,
  Bug #4 machte das jetzt konsistent). **Empfehlung:** eine „pessimistische Kosten"-Variante
  in `backtest/evaluate.py` (Kosten ×2) als zusätzlichen Robustheits-Check anbieten.

### 6. Fehlende Strategiefamilie: **Funding-Rate-Carry / Basis-Trade (marktneutral)**
- **Quelle (gemischt):** akademisch aufkommend (z. B. AEA-2026-Programmpapier zu
  Perpetual-Basis; MDPI-Artikel zur Funding-Struktur) + Praktiker. Perpetuals sind **~93 %**
  des Krypto-Futures-Volumens.
- **Kennzahlen/Caveats:** Delta-neutral (Spot long + Perp short) verdient den Funding-Satz.
  Aber: die **Renditen sind stark komprimiert** — BTC-Front-Basis fiel von ~25 % (Feb 2024)
  auf **4,46 %** (Dez 2025), 93 % der Tage unter der 5 %-Breakeven-Schwelle; von Top-Arb-
  Gelegenheiten sind nach Kosten nur **~40 %** positiv. Kein „Gratis-Geld".
- **Für dieses Projekt:** Das ist die einzige klar **marktneutrale** Familie, die komplett
  fehlt. Sie bräuchte: Funding-Daten (ccxt `fetchFundingRate`), einen neuen Adapter für
  gepaarte Spot/Perp-Positionen und eine eigene Strategie-Klasse. **Neue Abhängigkeit +
  echte Design-Entscheidung → nur als Empfehlung, nicht eigenmächtig umgesetzt.**
  Quellen: [ScienceDirect: Funding-Rate-Arbitrage CEX/DEX](https://www.sciencedirect.com/science/article/pii/S2096720925000818) ·
  [MDPI: Funding-Rate-Struktur](https://www.mdpi.com/2227-7390/14/2/346) ·
  [AEA 2026 Programm](https://www.aeaweb.org/conference/2026/program/paper/ByyFEfr4)

---

## Empfehlungen zur Entscheidung (nicht eigenmächtig umgesetzt)

Diese Punkte sind **Design-Entscheidungen mit Trade-off** — bitte auswählen, was umgesetzt
werden soll:

1. **Cache-Veraltung** (`data/loader.py`): Bei `refresh=False` (Auswertung) wird ein einmal
   geschriebener Cache **nie** als veraltet erkannt. Für den Live-Bot unkritisch (er nutzt
   `refresh=True`), aber die periodische Anpassung könnte auf alten Daten laufen.
   *Trade-off:* automatisches Neuladen kostet Offline-Tauglichkeit. → als Option vorschlagen.
2. **Deflated-Sharpe-/Trial-Count-Abschlag** im Score (siehe 2.4).
3. **Portfolio-Volatility-Targeting** (siehe 2.3).
4. **Pessimistische Kosten-Variante** im Evaluate (siehe 2.5).
5. **Neue marktneutrale Familie: Funding-Rate-Carry** (siehe 2.6).

Sag mir, welche Punkte ich umsetzen soll — 1 und 4 sind risikoarm; 2 und 3 sind
substanzielle, aber gut belegte Verbesserungen; 5 ist das größte Projekt (neue Daten/Adapter).

---

## Audit-Durchgang 3 (2026-08-07) — Stop-Loss-Loop, Circuit-Breaker-Exit, Betrieb

Anlass: Realer Lauf am 2026-08-06 verlor −15,05 € (−3,01 %) in 3 h 14 min und wurde vom
Tagesverlust-Circuit-Breaker gestoppt; danach lief der Bot nie wieder an. `trades`
(source='paper') zeigte 84/87 `roc_momentum`-Trades mit identischem entry/exit im
Minutentakt, alle `exit_reason='stop_loss'`.

**Hinweis vorab:** Der Arbeitsstand war beim Start dieses Durchgangs teilweise
zurückgesetzt — die Fixes aus Durchgang 2 (Look-Ahead `closed_bars`, serve-Doppelstart-
Guard, `PaperLoopConfig`-Verdrahtung) fehlten im Code, während ihre Tests noch da waren
→ Suite rot. Diese drei wurden **wiederhergestellt** (siehe Tabelle), damit die Suite
wieder grün ist.

| # | Datei | Was war kaputt | Reales Szenario | Fix | Test |
|---|---|---|---|---|---|
| P1a | `risk/manager.py` | Bei ATR≈0 lag der Stop praktisch auf dem Einstieg; keine Mindest-Distanz. | Ruhiger Markt → Stop ~0 → eigene Kerzenspanne stoppt sofort aus, Strategie eröffnet neu → Dauerschleife bis Circuit Breaker. −9,57 € in ~70 min. | `RiskLimits.min_stop_pct` (0,25 %); `check_order` **lehnt** Orders mit zu engem Stop ab (statt aufzuweiten). | `test_min_stop_distance_rejects_near_zero_stop` |
| P1b | `execution/paper_loop.py` | Keine Balken-Sperre: nach Stop/TP auf einem Balken konnte dieselbe Kombination auf **demselben** Balken sofort neu eröffnen. | Minutentakt-Neueröffnung auf derselben (ruhigen) Kerze → reiner Gebühren-/Slippage-Verlust. | Debounce `self._blocked_bar[(strategy,symbol)]=bar_ts` bei Stop/TP; Neu-Eröffnung erst auf einem neuen, später geschlossenen Balken. | `test_bar_debounce_blocks_reentry_after_stop_same_bar` |
| P2 | `execution/paper_loop.py` (`_flatten_all`) | Circuit-Breaker-Trip rief `self.stop()` → beendete den **gesamten** `serve`-Prozess (äußere `while loop._running`). | Am 2026-08-06 beendete der Trip den Bot komplett; er lief ohne manuellen Neustart nie wieder. | Trip stellt nur glatt + geht in **Safe-Hold** (Loop tickt weiter, `tripped` blockiert neue Orders via `check_order`), bis `reset_breaker`. | `test_trip_holds_loop_and_resumes_after_reset`; angepasst: `test_circuit_breaker_flattens_positions_in_loop` (erwartete fälschlich `loop_stopped`) |
| R2 | `execution/paper_loop.py` | Look-Ahead-Fix `closed_bars` war entfernt → Signal/ATR auf noch laufender Kerze. | Signal flackert intraday, weicht vom Backtest ab. | `closed_bars` wiederhergestellt; `_process` nutzt abgeschlossene Kerzen. | `test_closed_bars_*`, `test_process_generates_signal_on_closed_bar_only` |
| R3 | `cli.py` (`cmd_serve`) | Doppelstart-Guard entfernt → zweiter `serve` möglich. | Zwei Prozesse verfälschen dieselbe DB. | Guard wiederhergestellt (frisches Heartbeat+running ⇒ Abbruch, `--force`). | `test_serve_refuses_second_instance_when_bot_alive` |
| R1 | `cli.py` (`_make_loop_config`) | Loop-Config-Verdrahtung entfernt → Paper-Loop nutzte Default-Stops statt config.yaml. | Bot handelt mit anderen Stops als validiert. | `_make_loop_config` wiederhergestellt, in `cmd_paper`/`cmd_serve` verdrahtet. | `test_loop_config_uses_config_values_not_defaults` |

**Begründung „ablehnen statt Stop aufweiten" (P1a):** Ein Aufweiten würde das
Risiko-/Positionsgrößen-Profil der Strategie still verändern und die Paper-Ergebnisse
vom validierten Backtest entkoppeln. Ein ATR≈0-Moment ist zudem ein degenerierter,
illiquider Augenblick ohne handelbares Volatilitätssignal — **nicht** zu handeln ist die
ehrliche, sichere Wahl. Zusammen mit dem Debounce ist die Schleife damit doppelt gesperrt.

**Prio 3 (Betrieb):** `run_bot_supervised.bat` (Auto-Neustart nach echtem Absturz, 30 s
Cooldown, keine neue Abhängigkeit) + Aufgabenplanungs-Anleitung in `ANLEITUNG.md`. Mit
dem P2-Fix beendet sich `serve` bei Trip nicht mehr; der Supervisor greift nur bei echten
Abstürzen, der Doppelstart-Guard verhindert Parallel-Bots.

**Prio 4 (Empfehlungen, NICHT umgesetzt — Entscheidung offen):**
- *Risikoarm, würde ich nach deinem OK direkt umsetzen:* pessimistische Kosten-Variante
  (Gebühr/Slippage ×2) als zusätzlicher Robustheits-Check in `backtest/evaluate.py`.
- *Nur Vorschlag (Trade-off):* Deflated-Sharpe-/Trial-Count-Abschlag im Score
  (`stats/score.py`), Portfolio-Volatility-Targeting (`risk`/`stats/portfolio.py`),
  Funding-Rate-Carry als neue marktneutrale Familie (neue Datenquelle/Adapter).

### Ehrliche Einschätzung: neuer mehrwöchiger Paper-Lauf jetzt sinnvoll?
Die Fixes beseitigen einen **konkreten Kapitalvernichter** (die Stop-Loss-Schleife, allein
−9,57 €) und den Grund, warum der Bot nach einem Trip tot blieb. Ein neuer, sauberer
Paper-Lauf über mehrere Wochen ist damit **technisch sinnvoll** — mit zwei ehrlichen
Vorbehalten: (1) `roc_momentum` war der Hauptverursacher; ob es nach den Fixes noch
schädlich churnt, sollte im Blick bleiben (ggf. per config aus dem Universe nehmen —
Nutzerentscheidung, kein Bug). (2) Ein positives Ergebnis über Wochen bleibt eine
Stichprobe, kein Beleg für einen dauerhaften Vorteil. Vor dem Start empfiehlt sich ein
sauberer DB-Reset des Paper-Zustands (Start bei 500 €).
