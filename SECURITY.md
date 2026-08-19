# Sicherheit & Geldschutz

Dieses Dokument fasst zusammen, wie das Projekt dein Kapital und deine Zugangsdaten
schützt — und was **du** zusätzlich tun musst, weil kein Programm es erzwingen kann.
Grundlage sind gängige Sicherheitsempfehlungen für Trading-Bots (Quellen unten).

## Was der Code durchsetzt

| Schutz | Umsetzung |
|---|---|
| **Keys nie im Klartext auf Platte** | Verschlüsselter Vault (`risk/vault.py`): PBKDF2-HMAC-SHA256 mit 480 000 Iterationen + Fernet (AES-128-CBC + HMAC). Master-Passwort wird nie gespeichert. |
| **Keys nie im Git-Repo** | `.env` und `*.vault`/`secrets.vault` stehen in `.gitignore`. |
| **Keys nie im Log** | Es wird nirgends ein Key-Wert geloggt; das Audit-Log speichert nur Ereignisse, Beträge und Strategienamen. |
| **Kein automatischer Live-Handel** | Dreifaches Gate (`execution/gate.py`): Config-Flag + manuelle Bestätigung + Keys. Prozesslokal, nach Neustart wieder Paper. |
| **Harte Risikolimits** | `risk/manager.py`: Risiko/Trade gedeckelt (max. 5 %), Tagesverlust-Circuit-Breaker, Gesamtdrawdown-Kill-Switch, Pflicht-Stop-Loss. |
| **Vault verweigert unsicheren Fallback** | Ohne `cryptography` gibt es **keine** Klartextspeicherung, sondern einen Fehler. |

Vault anlegen:

```bash
python cli.py vault --market crypto
```

## Was DU tun musst (Code kann es nicht erzwingen)

Diese Punkte sind laut den Quellen die wichtigsten — und sie liegen außerhalb der
Software, bei deinem Exchange-Konto:

1. **API-Key OHNE Auszahlungsrecht erstellen.** Nur „read" + „trade" aktivieren,
   **niemals „withdraw"**. Selbst wenn der Key gestohlen wird, kann dann niemand
   Guthaben abziehen. Das ist der wichtigste Einzelschutz.
2. **IP-Whitelist der Exchange aktivieren.** Der Key funktioniert dann nur von deiner
   festen IP. Ein geleakter Key ist von woanders wertlos.
3. **Keys alle 30–90 Tage rotieren** — auch ohne Verdacht. Bei jedem Verdacht sofort.
4. **Zwei-Faktor-Authentifizierung (2FA)** auf dem Exchange-Konto.
5. **Master-Passwort des Vaults** stark wählen und getrennt aufbewahren (Passwort-Manager).
6. **Erst Paper, dann Cent-Beträge.** Live niemals mit einem Betrag, dessen
   Totalverlust dir wehtut.

## Was dieses Tool NICHT ist

- Keine Absicherung gegen Marktverluste. Der beste Key-Schutz der Welt verhindert
  keinen schlechten Trade.
- Kein Ersatz für eine Sicherheitsprüfung deines eigenen Rechners. Ist dein PC mit
  Malware verseucht, hilft auch ein verschlüsselter Vault nur begrenzt (Passwort kann
  beim Eintippen abgegriffen werden).

## Quellen

- [Essential Security Measures for Crypto Trading Bots — alwin.io](https://www.alwin.io/security-measures-for-crypto-bots)
- [Crypto Bot Security and API Key Management — origami.tech](https://origami.tech/articles/crypto-bot-security-and-api-key-management-for-safe-automated-trading)
- [Security Essentials for Crypto Trading — CryptoRobotics](https://cryptorobotics.ai/learn/security-essentials-for-crypto-trading-api-keys-authentication-account-protection/)
- [Secure Crypto Bots: API Key Protection — Bitget Academy](https://www.bitget.com/academy/12560603879287)
