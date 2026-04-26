import pytest
from app.services.secrets_service import SecretsService


def test_encrypt_decrypt_roundtrip():
    svc = SecretsService("test-secret-key-32-chars-minimum!")
    original = "sk-ant-api-key-test-value"
    encrypted = svc.encrypt(original)
    assert encrypted != original
    assert svc.decrypt(encrypted) == original


def test_encrypt_produces_different_ciphertext_each_time():
    svc = SecretsService("test-secret-key-32-chars-minimum!")
    v1 = svc.encrypt("same-value")
    v2 = svc.encrypt("same-value")
    # Fernet uses random IV — same plaintext → different ciphertext
    assert v1 != v2
    # But both decrypt correctly
    assert svc.decrypt(v1) == "same-value"
    assert svc.decrypt(v2) == "same-value"


def test_wrong_key_raises_on_decrypt():
    svc1 = SecretsService("test-secret-key-32-chars-minimum!")
    svc2 = SecretsService("different-key-32-chars-min-pad!!")
    encrypted = svc1.encrypt("secret")
    with pytest.raises(Exception):
        svc2.decrypt(encrypted)
