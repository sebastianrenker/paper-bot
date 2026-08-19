"""Verschluesselter Schluessel-Tresor (Geldschutz / Schutz gegen Key-Diebstahl).

Recherche-gestuetzt (bitget, cryptorobotics, alwin.io): API-Keys gehoeren NICHT als
Klartext auf die Platte. Dieser Vault verschluesselt sie mit einem Master-Passwort.

Verfahren:
  * Schluesselableitung: PBKDF2-HMAC-SHA256, 480.000 Iterationen, zufaelliger Salt
  * Symmetrische Verschluesselung: Fernet (AES-128-CBC + HMAC-SHA256, authentifiziert)
  * Das Master-Passwort wird nie gespeichert; ohne es sind die Keys unlesbar.

Wenn `cryptography` nicht installiert ist, verweigert der Vault bewusst den Dienst,
statt auf unsichere Klartextspeicherung auszuweichen.

WICHTIGE BEGLEITMASSNAHMEN (nicht durch Code erzwingbar, im SECURITY-Doc dokumentiert):
  * Exchange-API-Key OHNE Auszahlungsrecht (nur read + trade) erstellen.
  * IP-Whitelist der Exchange aktivieren.
  * Keys alle 30-90 Tage rotieren.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Optional

DEFAULT_VAULT = Path(__file__).resolve().parent.parent / "config" / "secrets.vault"


class VaultError(RuntimeError):
    pass


def _require_crypto():
    try:
        from cryptography.fernet import Fernet, InvalidToken  # type: ignore
        from cryptography.hazmat.primitives import hashes  # type: ignore
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # type: ignore
        return Fernet, InvalidToken, hashes, PBKDF2HMAC
    except ImportError as exc:  # pragma: no cover
        raise VaultError(
            "Paket 'cryptography' fehlt. Installiere es (pip install cryptography); "
            "eine unverschluesselte Speicherung wird bewusst nicht angeboten."
        ) from exc


def _derive_key(password: str, salt: bytes) -> bytes:
    _, _, hashes, PBKDF2HMAC = _require_crypto()
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=480_000)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


class SecretsVault:
    def __init__(self, path: Path | str = DEFAULT_VAULT) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def save(self, secrets: dict[str, str], password: str) -> None:
        Fernet, _, _, _ = _require_crypto()
        if not password or len(password) < 8:
            raise VaultError("Master-Passwort muss mindestens 8 Zeichen haben.")
        salt = os.urandom(16)
        token = Fernet(_derive_key(password, salt)).encrypt(json.dumps(secrets).encode("utf-8"))
        payload = {"v": 1, "salt": base64.b64encode(salt).decode(), "data": token.decode()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        try:  # Dateirechte einschraenken, wo unterstuetzt
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def load(self, password: str) -> dict[str, str]:
        Fernet, InvalidToken, _, _ = _require_crypto()
        if not self.exists():
            raise VaultError(f"Kein Vault unter {self.path}.")
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        salt = base64.b64decode(payload["salt"])
        try:
            raw = Fernet(_derive_key(password, salt)).decrypt(payload["data"].encode("utf-8"))
        except InvalidToken as exc:
            raise VaultError("Falsches Master-Passwort oder beschaedigter Vault.") from exc
        return json.loads(raw.decode("utf-8"))

    def load_into_env(self, password: str, *, overwrite: bool = False) -> list[str]:
        """Entschluesselt die Keys und legt sie NUR im Prozess-Environment ab
        (nicht auf Platte). Gibt die gesetzten Variablennamen zurueck."""
        loaded = self.load(password)
        names = []
        for key, value in loaded.items():
            if overwrite or not os.getenv(key):
                os.environ[key] = str(value)
                names.append(key)
        return names
