# Gratis in der Cloud laufen lassen (ohne PC anzulassen)

Ziel: Der Paper-Bot läuft **kostenlos rund um die Uhr** auf GitHubs Servern, und du
siehst den Stand über einen **öffentlichen Link** (Streamlit Cloud) — dein eigener
Rechner darf aus sein.

> Alles bleibt **Paper-Trading** (simuliertes Geld). Es werden **keine API-Keys** und
> kein echtes Konto gebraucht. Der Live-Handel ist im Code gesperrt.

**Ehrlich vorab — die Grenzen dieses Gratis-Wegs:**
- Der Bot handelt **alle ~30 Minuten** einen Tick (nicht sekündlich). GitHubs Zeitplan
  ist „best effort" — Läufe können sich verschieben oder mal ausfallen. Für 4h-Strategien
  ist das unkritisch.
- Ein **öffentliches** GitHub-Repo hat unbegrenzte Gratis-Minuten (empfohlen). Deine
  Paper-Ergebnisse sind dann öffentlich sichtbar — da kein echtes Geld/kein Key im Spiel
  ist, ist das unbedenklich.
- Der Bot handelt genau die Kombinationen in **`active_combos.yaml`** (gerade 2 Stück).
  Die schwere Auswertung/Anpassung machst du gelegentlich lokal und committest die Datei.

---

## Was ich schon vorbereitet habe (liegt im Ordner)
- `.github/workflows/paper-bot.yml` — der Zeitplan-Job (alle 30 min ein Tick + Commit).
- `requirements-ci.txt` — schlanke Abhängigkeiten für den schnellen CI-Lauf.
- `cli.py ci-tick` — führt genau einen Paper-Tick aus, speichert den Zustand in
  `cloud/paper.db` und schreibt `BOT_STATUS_REPORT.md`.
- `cli.py export-active` — schreibt die validierten Kombinationen nach `active_combos.yaml`.
- `cloud/paper.db` — schlanke Start-DB (500 € Startkapital), lokal getestet.
- Dashboard-Nur-Lese-Modus für die Cloud (Umgebungsvariable `CLOUD_READONLY=1`).

---

## Schritt 1 — GitHub-Repo anlegen (einmalig)
1. Kostenlosen Account auf https://github.com anlegen (falls noch keiner).
2. Neues **öffentliches** Repository erstellen (z. B. `paper-bot`).
3. Den kompletten Ordner `trading-dashboard` hochladen/pushen. Bequem ohne Kommandozeile:
   **GitHub Desktop** (https://desktop.github.com) → „Add Local Repository" → diesen
   Ordner wählen → „Publish repository" (Häkchen „public").

## Schritt 2 — Bot-Job aktivieren
1. Im Repo → Reiter **Actions**. Bei öffentlichen Repos ist der Zeitplan sofort aktiv.
2. Links **`paper-bot`** wählen → rechts **Run workflow** (löst sofort einen ersten Tick
   aus, statt bis zur nächsten halben Stunde zu warten).
3. Nach ~1 Minute erscheint ein neuer Commit „paper-bot: Tick …" — dann läuft es.

## Schritt 3 — Öffentliches Dashboard (Streamlit Cloud)
1. Auf https://share.streamlit.io mit deinem GitHub-Account anmelden (gratis).
2. **Create app** → dein Repo `paper-bot`, Branch `main`, **Main file**:
   `dashboard/app.py`.
3. **Advanced settings** → **Environment variables** hinzufügen:
   - `CLOUD_READONLY` = `1`
   - `TRADING_DB` = `cloud/paper.db`
4. **Deploy**. Nach ein paar Minuten bekommst du eine öffentliche URL — dort siehst du
   Status, Kapitalkurve, offene Positionen, Trades und den Handels-Chart.

Fertig. Ab jetzt tickt der Bot alle ~30 min auf GitHub, und die Streamlit-Seite zeigt den
aktuellen Stand — dein PC darf aus sein.

---

## Was du ändern möchtest, wenn nötig
- **Andere/weitere Kombinationen handeln:** lokal `python cli.py evaluate` (Daten frisch),
  optional `python cli.py optimize`, dann `python cli.py export-active` → `active_combos.yaml`
  committen/pushen. Der Cloud-Bot übernimmt sie automatisch.
- **Sauberer Neustart (Kapital zurück auf 500 €):** lokal `cloud/paper.db` löschen, einen
  `ci-tick` mit `TRADING_DB=cloud/paper.db` laufen lassen, committen.
- **Tick-Intervall:** in `.github/workflows/paper-bot.yml` die `cron`-Zeile anpassen
  (kleiner als 5 min lässt GitHub nicht zu).

## Verhalten bei Verlusttag
Reißt der Tagesverlust die 3 %-Grenze, **pausiert** der Bot den Handel für den Rest des
UTC-Tages (Sicherheitsmechanismus) und nimmt am nächsten Tag automatisch wieder auf.

---
*Analysewerkzeug, keine Finanzberatung. Paper-Trading mit simuliertem Geld.*
