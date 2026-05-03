import pytest
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4, UUID
from app.services.secrets_service import SecretsService, SecretVersion


# ── helpers ───────────────────────────────────────────────────────────────────

def _svc() -> SecretsService:
    return SecretsService("test-secret-key-32-chars-minimum!")


# ── SecretVersion dataclass ───────────────────────────────────────────────────

def test_secret_version_fields():
    sid = uuid4()
    ver = SecretVersion(
        id=uuid4(),
        secret_id=sid,
        ciphertext="enc",
        status="current",
        created_at=datetime.utcnow(),
    )
    assert ver.secret_id == sid
    assert ver.status == "current"


# ── rotate_secret ─────────────────────────────────────────────────────────────

def test_rotate_secret_creates_new_current_version():
    svc = _svc()
    secret_id = uuid4()

    new_ver = svc.rotate_secret(secret_id, "new-password-abc123")

    assert new_ver.secret_id == secret_id
    assert new_ver.status == "current"
    assert svc.decrypt(new_ver.ciphertext) == "new-password-abc123"


def test_rotate_secret_demotes_old_version_to_superseded():
    svc = _svc()
    secret_id = uuid4()

    first = svc.rotate_secret(secret_id, "password-v1")
    second = svc.rotate_secret(secret_id, "password-v2")

    # The in-memory store should hold the superseded version
    superseded_plaintext = svc.get_superseded_secret(secret_id)
    assert superseded_plaintext == "password-v1"


def test_rotate_secret_multiple_times_keeps_latest_superseded():
    svc = _svc()
    secret_id = uuid4()

    svc.rotate_secret(secret_id, "password-v1")
    svc.rotate_secret(secret_id, "password-v2")
    svc.rotate_secret(secret_id, "password-v3")

    # get_superseded_secret returns the immediately prior version (v2)
    superseded = svc.get_superseded_secret(secret_id)
    assert superseded == "password-v2"


# ── get_superseded_secret ─────────────────────────────────────────────────────

def test_get_superseded_secret_returns_none_when_no_prior():
    svc = _svc()
    result = svc.get_superseded_secret(uuid4())
    assert result is None


def test_get_superseded_secret_after_single_rotate():
    svc = _svc()
    secret_id = uuid4()
    svc.rotate_secret(secret_id, "only-rotation")
    # One rotation creates a current version but no prior superseded
    assert svc.get_superseded_secret(secret_id) is None


def test_superseded_value_is_encrypted_at_rest():
    svc = _svc()
    secret_id = uuid4()
    svc.rotate_secret(secret_id, "v1-plaintext")
    svc.rotate_secret(secret_id, "v2-plaintext")

    # Internal _versions store must hold ciphertext, not plaintext
    versions = svc._versions.get(secret_id, [])
    superseded = [v for v in versions if v.status == "superseded"]
    assert superseded
    assert superseded[0].ciphertext != "v1-plaintext"
    # But decrypts correctly
    assert svc.decrypt(superseded[0].ciphertext) == "v1-plaintext"


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
