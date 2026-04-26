import hashlib
from base64 import urlsafe_b64encode
from cryptography.fernet import Fernet


class SecretsService:
    """
    Encrypts and decrypts string secrets using Fernet (AES-256-GCM).

    Uses a key derived from the app SECRET_KEY. This class is designed
    as an abstraction: future implementations can delegate to HashiCorp
    Vault, AWS Secrets Manager, or an HSM without changing callers.
    """

    def __init__(self, secret_key: str):
        key_bytes = hashlib.sha256(secret_key.encode()).digest()
        self._fernet = Fernet(urlsafe_b64encode(key_bytes))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, encrypted: str) -> str:
        return self._fernet.decrypt(encrypted.encode()).decode()
