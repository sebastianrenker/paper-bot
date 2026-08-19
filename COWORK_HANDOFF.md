# Handoff-Bericht für Claude Cowork — Paper-Trading-Bot

**Erstellt:** 2026-08-06 14:23 UTC · **Von:** Claude Code (lokale Sitzung auf dem Rechner des Nutzers)
**Für:** Claude in einer separaten Cowork-Sitzung, die diesen Rechner NICHT live sieht,
aber Dateien aus dem Ordner `trading-dashboard` über die Geräte-Brücke lesen kann.

> **Zweck dieses Dokuments:** Dich (Cowork-Claude) so genau informieren, dass du den
> Zustand des Bots allein aus den Dateien im Projektordner nachvollziehen und dem Nutzer
> ehrlich berichten kannst — ohne Zugriff auf die laufende Konsole.
>
> **Nicht verhandelbar:** Es ist reines **Paper-Trading** (simuliertes Geld). Live-Handel
> ist im Code gesperrt (`execution/live.py` wirft `LiveTradingNotEnabled`). `mode: paper`
> bleibt. Ändere NICHTS in `risk/`, `execution/`, `strategies/` oder an den Risikolimits.
> Keine Prognosen, keine Gewinnversprechen — nur Fakten aus den Dateien.

---

## 1. Womit du arbeitest (was du lesen kannst)

| Datei | Inhalt | So nutzt du sie |
|---|---|---|
| **`BOT_STATUS_REPORT.md`** | Menschenlesbarer Statusbericht, **stündlich** automatisch neu geschrieben | **Primärquelle.** Zuerst hierher schauen. |
| `trading.db` | SQLite-Datenbank mit allem Zustand (Heartbeat, Positionen, Trades, Audit, Steuerung) | Für Details, die im Report nicht stehen — Beispiel-Queries unten. |
| `bot.log` | Rohlog des Bot-Prozesses (Fetches, Portfolio-Aufbau, Anpassungen, Fehler) | Um zu prüfen, ob echte Fetches laufen und ob Fehler auftauchen. |
| `bot.pid` | Enthält die Prozess-ID des Bots (aktuell `11640`) | Referenz; du kannst sie nicht selbst prüfen (kein Live-Zugriff). |
| `bot_status_report.py` | Das Skript, das den Report erzeugt (reine Lesefunktion) | Nur zur Info; der Scheduled Task ruft es auf. |
| `config/config.yaml` | Konfiguration: `capital.initial: 500.0`, `mode: paper`, `data.require_real: true` | Startkapital & Modus. **Nicht ändern.** |

**Wichtig zur Aktualität:** Wenn `BOT_STATUS_REPORT.md` in deiner Cowork-Sicht „alt"
aussieht, liegt das an der Synchronisation der Geräte-Brücke, nicht zwingend am Bot.
Das Feld **„Letztes Lebenszeichen"** im Report ist der zuverlässige Frische-Indikator:
ist es älter als ~2 Minuten, lief der Bot zum Zeitpunkt der Report-Erzeugung nicht.

---

## 2. Snapshot bei Übergabe (Stand 2026-08-06 14:23:41 UTC)

Diese Werte ändern sich laufend — sie sind nur die Momentaufnahme bei Erstellung dieses Dokuments:

- **Status:** LÄUFT (Heartbeat 27 s alt), `desired_state = running`
- **Kapital:** Start **500,00 €** → aktuell **499,88 €** (**−0,12 € / −0,024 %**) — reine Einstiegskosten (Gebühren/Slippage)
- **Offene Positionen (2):**
  - `LINK/USDT` — SHORT, Strategie `ema_crossover`, Einstieg 8,1399, Kurs 8,144, unreal. −0,027 €
  - `ADA/USDT` — LONG, Strategie `ema_crossover`, Einstieg 0,1924, Kurs 0,1923, unreal. −0,025 €
- **Abgeschlossene Paper-Trades:** 0 (auf 4h-Timeframes normal; kann Stunden dauern)
- **Circuit Breaker:** nie ausgelöst · **`tripped = 0`**
- **Letzte Selbst-Anpassung:** 2026-08-06 16:15:51 (Ortszeit, s. Zeitzonen-Hinweis) · **2** validierte Kombinationen aktiv
- **Datenintegrität:** **0 synthetische** Auswertungen (korrekt bei `require_real: true`)

**Zeitzonen-Hinweis (wichtig, sonst Fehlinterpretation):** Heartbeat- und
Equity-Zeitstempel in `trading.db` sind **UTC (ISO-8601)**. Das Feld `last_adaptation`
in der `control`-Tabelle ist dagegen **lokale Wanduhrzeit** des Rechners (hier UTC+2).
Nicht die beiden direkt vergleichen.

---

## 3. Der Bot-Prozess

- **Kommando:** `python cli.py serve --interval 60 --reeval-hours 6`
- **PID:** 11640 (auch in `bot.pid`)
- **Start:** als eigenständiger, abgekoppelter Windows-Prozess (`DETACHED_PROCESS`) —
  überlebt das Schließen der Claude-Code-Sitzung, **aber NICHT** einen Reboot/Shutdown
  oder Abmelden des Nutzers. Es gibt **keinen Auto-Neustart**.
- **Was er tut:** alle 60 s ein „Tick" (lädt echte Kurse der aktiv gehandelten
  Kombinationen, prüft Signale/Stops, platziert/schließt Paper-Orders, schreibt
  Heartbeat + Positionen in die DB). Alle 6 h eine Selbst-Anpassung (komplette
  Neu-Auswertung + Optimierung + Portfolio-Neubau), die die aktiv gehandelte Menge
  auf die validierten Kombinationen aktualisiert.
- **Er handelt bewusst nur die validierten Portfolio-Kombinationen**, nicht das ganze
  Universum — deshalb wenige, gezielte Positionen.

---

## 4. Datenbank direkt lesen (falls du Details brauchst)

`trading.db` ist SQLite (WAL-Modus). Relevante Tabellen und Beispiel-Abfragen:

```sql
-- Läuft der Bot noch? (frisches ts = ja)
SELECT ts, equity, open_positions, daily_pnl_pct, tripped, note
FROM heartbeat WHERE source='paper';

-- Offene Positionen
SELECT symbol, strategy, qty, avg_price, mark_price, stop_loss, take_profit, unrealized
FROM positions_snapshot WHERE source='paper';

-- Kapitalverlauf (UTC-Zeitstempel)
SELECT ts, equity FROM equity_points WHERE source='paper' ORDER BY ts;

-- Abgeschlossene Paper-Trades, je Strategie
SELECT strategy, COUNT(*) n, SUM(pnl) pnl, AVG(pnl) avg_pnl
FROM trades WHERE source='paper' GROUP BY strategy;

-- Risiko-Ereignisse (Circuit Breaker / Kill-Switch / Zwangsschließung)
SELECT ts, event, details FROM audit_log
WHERE event IN ('circuit_breaker','kill_switch','forced_flat') ORDER BY id DESC;

-- Steuerung / Anpassung
SELECT key, value, ts FROM control
WHERE key IN ('desired_state','last_adaptation','adapted_combos');

-- Datenintegrität: MUSS 0 sein bei require_real:true
SELECT data_source, COUNT(*) FROM evaluations GROUP BY data_source;
```

Hinweis: `trades` enthält auch `source='backtest'` (143.525 Zeilen aus der
historischen Auswertung) — für den Live-Bot **immer auf `source='paper'` filtern**.

---

## 5. Der stündliche Report (Scheduled Task)

- **Task-Name:** `TradingBotStatusReport`
- **Zeitplan:** stündlich · **Status:** Bereit (verifiziert: manueller Testlauf schrieb
  `BOT_STATUS_REPORT.md` korrekt neu)
- **Was er tut:** führt `bot_status_report.py` aus → überschreibt `BOT_STATUS_REPORT.md`
  mit dem aktuellen Stand aus `trading.db`.
- **Einschränkung:** läuft standardmäßig **nur, solange der Nutzer angemeldet ist**.
  Der Task hält den *Report* frisch, **nicht den Bot** — beides sind getrennte Dinge.

---

## 6. Wie du dem Nutzer ehrlich berichtest

1. Lies **`BOT_STATUS_REPORT.md`**.
2. Prüfe zuerst **„Letztes Lebenszeichen"**:
   - unter ~2 min → Bot läuft, Zahlen sind aktuell.
   - deutlich älter → **Bot läuft nicht mehr** (wahrscheinlich Rechner neu gestartet
     oder Nutzer abgemeldet). Sag das klar; die Zahlen sind dann ein Standbild.
3. Berichte **Fakten**: Kapital vs. 500 €, offene Positionen, abgeschlossene Trades,
   ob der Circuit Breaker auslöste, letzte Anpassung.
4. **Keine Prognose.** Ein paar Stunden Kapitalbewegung sind Mark-to-Market-Rauschen
   der offenen Positionen — **kein** Beleg für einen Vorteil. Belastbare Aussagen
   brauchen Wochen. Wenn der Nutzer „macht er Gewinn?" fragt: die reine Zahl nennen und
   diese Einordnung dazu.
5. Wenn `synthetic > 0` in der Datenintegrität steht: das ist ein **Fehler** (dürfte bei
   `require_real:true` nicht vorkommen) — dem Nutzer melden, nicht verschweigen.

---

## 7. Was du (Cowork) NICHT tun kannst / sollst

- Du hast **keinen Live-Zugriff** auf diesen Rechner — du kannst den Prozess nicht sehen,
  starten oder stoppen und keine Befehle darauf ausführen. Du arbeitest nur mit den
  Dateien, die die Geräte-Brücke synchronisiert.
- **Den Bot starten/stoppen** kann nur der Nutzer am Rechner: Dashboard-Knopf
  „BOT STARTEN" / „BOT STOPPEN", oder in der Konsole `python cli.py serve` bzw.
  `desired_state=stopped` setzen.
- **Nichts optimieren, keine Limits lockern**, um bessere Zahlen zu erzeugen — es geht um
  ehrliche Beobachtung.

---

## 8. Was der Nutzer selbst tun muss

- **Rechner an lassen und angemeldet bleiben.** Bei Shutdown/Reboot stoppt der Bot
  (Heartbeat wird „veraltet") und muss manuell neu gestartet werden. Der Report-Task
  läuft ebenfalls nur bei angemeldetem Benutzer.
- Bot bewusst stoppen: Dashboard → **BOT STOPPEN** (stellt Positionen glatt) — oder
  Prozess PID 11640 beenden.
- Live-Stand jederzeit: `BOT_STATUS_REPORT.md` (stündlich) oder Dashboard-Tab
  „Live Paper-Trader" (`python cli.py dashboard`, Auto-Refresh alle 5 s).

---

*Analysewerkzeug, keine Finanzberatung. Paper-Trading mit simuliertem Geld. Der
Live-Handel ist und bleibt im Code gesperrt.*
