import hashlib
import json
from base64 import urlsafe_b64encode
from cryptography.fernet import Fernet
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID, uuid4
from typing import Optional


@dataclass
class SecretVersion:
    secret_id: UUID
    ciphertext: str
    status: str          # "current" | "superseded"
    created_at: datetime
    id: UUID = field(default_factory=uuid4)


class SecretsService:
    """
    Encrypts and decrypts string secrets using Fernet (AES-256-GCM).

    Uses a key derived from the app SECRET_KEY. This class is designed
    as an abstraction: future implementations can delegate to HashiCorp
    Vault, AWS Secrets Manager, or an HSM without changing callers.

    _versions is an in-memory dict[UUID, list[SecretVersion]] used when
    a DB backend is not wired. Production usage should override
    _load_versions / _save_versions to persist via SQLAlchemy.
    """

    def __init__(self, secret_key: str):
        key_bytes = hashlib.sha256(secret_key.encode()).digest()
        self._fernet = Fernet(urlsafe_b64encode(key_bytes))
        self._versions: dict[UUID, list[SecretVersion]] = {}

    # ── primitive encrypt/decrypt ─────────────────────────────────────────────

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, encrypted: str) -> str:
        return self._fernet.decrypt(encrypted.encode()).decode()

    def encrypt_json(self, data: dict) -> str:
        """Serialize dict to JSON then encrypt."""
        return self.encrypt(json.dumps(data))

    def decrypt_json(self, encrypted: str) -> dict:
        """Decrypt then deserialize JSON to dict."""
        return json.loads(self.decrypt(encrypted))

    # ── versioned rotation ────────────────────────────────────────────────────

    def rotate_secret(self, secret_id: UUID, new_value: str) -> SecretVersion:
        """
        Encrypts new_value, stores it as the current version for secret_id,
        and demotes any existing current version to 'superseded'.

        Returns the newly created SecretVersion. The caller should persist
        the returned version to the DB (insert) and update the old version
        status to 'superseded' in the DB.
        """
        existing = self._versions.get(secret_id, [])

        # Demote the current version to superseded
        for ver in existing:
            if ver.status == "current":
                ver.status = "superseded"

        new_ver = SecretVersion(
            secret_id=secret_id,
            ciphertext=self.encrypt(new_value),
            status="current",
            created_at=datetime.utcnow(),
        )
        self._versions.setdefault(secret_id, []).append(new_ver)
        return new_ver

    def get_superseded_secret(self, secret_id: UUID) -> Optional[str]:
        """
        Returns the plaintext of the most recently superseded version for
        secret_id, or None if no superseded version exists.

        Used by rollback logic to recover the old credential.
        """
        versions = self._versions.get(secret_id, [])
        superseded = [v for v in versions if v.status == "superseded"]
        if not superseded:
            return None
        # Most recent superseded = last demoted
        latest = max(superseded, key=lambda v: v.created_at)
        return self.decrypt(latest.ciphertext)
