import hashlib
import json
from base64 import urlsafe_b64encode
from cryptography.fernet import Fernet


class FernetBackend:
    """Default backend: AES-256-GCM via Fernet, key derived from SECRET_KEY."""

    def __init__(self, secret_key: str):
        key_bytes = hashlib.sha256(secret_key.encode()).digest()
        self._fernet = Fernet(urlsafe_b64encode(key_bytes))

    def encrypt(self, value: str, **kwargs) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, encrypted: str) -> str:
        return self._fernet.decrypt(encrypted.encode()).decode()

    def encrypt_json(self, data: dict, **kwargs) -> str:
        return self.encrypt(json.dumps(data))

    def decrypt_json(self, encrypted: str) -> dict:
        return json.loads(self.decrypt(encrypted))
