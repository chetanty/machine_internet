from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet

_VAULT_DIR = Path.home() / ".uaa" / "vault"
_KEY_FILE = Path.home() / ".uaa" / "vault.key"


def _fernet() -> Fernet:
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not _KEY_FILE.exists():
        key = Fernet.generate_key()
        _KEY_FILE.write_bytes(key)
        try:
            _KEY_FILE.chmod(0o600)
        except Exception:
            pass
    return Fernet(_KEY_FILE.read_bytes())


class CredentialVault:
    def __init__(self, vault_dir: Optional[Path] = None) -> None:
        self._dir = vault_dir or _VAULT_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._f = _fernet()

    def store(self, service: str, credentials: dict[str, Any]) -> None:
        encrypted = self._f.encrypt(json.dumps(credentials).encode())
        path = self._dir / f"{service}.enc"
        path.write_bytes(encrypted)
        try:
            path.chmod(0o600)
        except Exception:
            pass

    def get(self, service: str) -> Optional[dict[str, Any]]:
        path = self._dir / f"{service}.enc"
        if not path.exists():
            return None
        try:
            return json.loads(self._f.decrypt(path.read_bytes()))
        except Exception:
            return None

    def delete(self, service: str) -> bool:
        path = self._dir / f"{service}.enc"
        if path.exists():
            path.unlink()
            return True
        return False

    def list_services(self) -> list[str]:
        return [p.stem for p in self._dir.glob("*.enc")]
