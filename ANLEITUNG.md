# Anleitung (für Einsteiger, ohne Technik-Wissen)

Diese Seite erklärt in einfachen Schritten, wie du das Programm benutzt — und was es
**kann** und was es **nicht** kann. Bitte einmal ganz lesen.

---

## In 3 Schritten starten

1. **Doppelklick auf `START.bat`.** Beim ersten Mal richtet sich alles von selbst ein
   (1–2 Minuten). Danach erscheint ein Menü.
2. Wähle **[0] Selbsttest.** Er prüft in Klartext, ob alles bereit ist. Steht überall
   `[OK]`, bist du startklar.
3. Wähle **[4] LIVE PAPER-TRADER.** Es öffnet sich der Browser. Im Tab
   **„Live Paper-Trader"** siehst du in Echtzeit, was das Programm tut.

Fertig. Mehr brauchst du nicht.

---

## Das Wichtigste zuerst: dein Geld ist sicher

- Das Programm handelt **mit Spielgeld** (10.000 „Papier-Dollar"). **Es kann kein
  echtes Geld ausgeben.** Der Handel mit echtem Geld ist im Programm **gesperrt** und
  in dieser Version gar nicht eingebaut.
- Du kannst also **nichts falsch machen und nichts verlieren.** Probiere alles aus.
- Der **Selbsttest [0]** bestätigt dir das jedes Mal: „Live-Handel ist GESPERRT".

---

## Ehrliche Wahrheit über „Profit" — bitte lesen

Dieses Programm ist ein **Lern- und Analysewerkzeug**, kein Gelddruck-Automat.

- **Kein Programm der Welt kann Gewinn im Trading garantieren.** Wer das verspricht,
  lügt. Kurse der Zukunft sind nicht vorhersehbar.
- Was dieses Programm ehrlich tut: es prüft Strategien mit strengen Tests und zeigt
  dir **transparent**, welche in der Vergangenheit funktioniert haben — und warnt,
  wenn eine Strategie nur zufällig gut aussah.
- Es zeigt dir auch die **Verlust-Trades**. Das ist Absicht: du sollst *vorher* sehen,
  wie riskant echtes Trading wäre — mit Spielgeld, ohne echtes Risiko.
- „Idiotensicher" bedeutet hier: **du kannst kein echtes Geld verlieren**, das
  Programm stürzt nicht ab, und jede Anzeige ist ehrlich beschriftet.

Wenn du irgendwann echtes Geld einsetzen willst, ist das eine große Entscheidung, die
**nur du** treffen kannst — mit Geld, dessen Verlust dir nicht wehtun würde, und erst
nachdem der Papier-Betrieb über Wochen gut lief.

---

## Was die Menüpunkte bedeuten

| Menü | Was es tut |
|---|---|
| **[0] Selbsttest** | Prüft, ob alles bereit ist. Hier immer anfangen. |
| **[1] Auswertung** | Rechnet alle Strategien auf **echten Börsendaten** durch. |
| **[2] Dashboard** | Öffnet die Übersicht im Browser. |
| **[3] Dauerbetrieb** | Lässt das Programm im Hintergrund weiterlaufen und alles aktuell halten. |
| **[4] Live Paper-Trader** | Das Wichtigste: du siehst live, was der Bot macht. |
| **[5] Selbst-Optimierung** | Sucht bessere Einstellungen — übernimmt aber nur, was echte Prüfungen besteht. |
| **[6] Paper-Trading** | Ein einzelner Durchlauf zum Ausprobieren. |
| **[7] Tests** | Prüft, dass das Programm technisch korrekt arbeitet. |

---

## Wenn etwas nicht klappt

- **Fehlermeldung im Menü?** Wähle **[0] Selbsttest** — er sagt in Klartext, was fehlt
  (meist: „Internet prüfen" oder „einmal neu starten").
- **Browser zeigt nichts?** Kurz warten und die Seite neu laden. Das Dashboard läuft
  unter `http://localhost:8501`.
- **„Keine Daten"?** Kurz die Internetverbindung prüfen und **[1] Auswertung** erneut
  starten — die Börsendaten werden dann frisch geladen.

Es kann nichts kaputtgehen. Im schlimmsten Fall schließt du alle Fenster und startest
`START.bat` neu.

---

## Bot automatisch bei Anmeldung starten (optional, Windows)

Standardmäßig läuft der Bot nur, solange du ihn manuell gestartet hast, und stoppt bei
Reboot/Abmelden. Wer ihn dauerhaft laufen lassen will, kann ihn per
**Windows-Aufgabenplanung** bei jeder Anmeldung automatisch starten lassen — mit
`run_bot_supervised.bat`, das den Bot nach einem echten Absturz mit 30 s Cooldown neu
startet (kein zusätzlicher Dienst/keine neue Abhängigkeit nötig).

Einmalig in einer normalen Konsole ausführen (Pfad ggf. anpassen):

```bat
schtasks /create /tn "TradingBotSupervised" ^
  /tr "\"%CD%\run_bot_supervised.bat\"" /sc onlogon /f
```

- Prüfen:  `schtasks /query /tn "TradingBotSupervised"`
- Entfernen:  `schtasks /delete /tn "TradingBotSupervised" /f`

Hinweise:
- Läuft nur, solange **dein Benutzer angemeldet** ist (kein Hintergrunddienst).
- Der **Doppelstart-Schutz** in `serve` verhindert, dass zwei Bots parallel dieselbe
  Datenbank verfälschen — ein zweiter Start bricht mit einem Hinweis ab.
- Mit dem Circuit-Breaker-Fix pausiert der Bot bei Tagesverlust-Limit nur den Handel;
  er beendet sich nicht mehr. Zum Fortsetzen im Dashboard **reset_breaker** senden.
- Es bleibt **Paper-Trading** — kein echtes Geld.
