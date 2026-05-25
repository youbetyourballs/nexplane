import pytest
from typing import runtime_checkable
from app.services.secret_backend import SecretBackend


def test_secret_backend_is_a_protocol():
    from typing import Protocol
    import inspect
    assert issubclass(SecretBackend, Protocol)


def test_secret_backend_protocol_methods():
    # Verify the protocol surface matches what callers expect
    import inspect
    methods = {name for name, _ in inspect.getmembers(SecretBackend, predicate=inspect.isfunction)}
    assert "encrypt" in methods
    assert "decrypt" in methods
    assert "encrypt_json" in methods
    assert "decrypt_json" in methods


def test_fernet_backend_satisfies_protocol():
    from app.services.secret_backend import SecretBackend
    from app.services.backends.fernet_backend import FernetBackend
    backend = FernetBackend(secret_key="test-key-for-testing-only")
    assert isinstance(backend, SecretBackend)


def test_fernet_backend_encrypt_decrypt_roundtrip():
    from app.services.backends.fernet_backend import FernetBackend
    backend = FernetBackend(secret_key="test-key-for-testing-only")
    original = "super-secret-value"
    encrypted = backend.encrypt(original)
    assert encrypted != original
    assert backend.decrypt(encrypted) == original


def test_fernet_backend_encrypt_json_decrypt_json_roundtrip():
    from app.services.backends.fernet_backend import FernetBackend
    backend = FernetBackend(secret_key="test-key-for-testing-only")
    data = {"username": "admin", "password": "hunter2", "host": "10.0.0.1"}
    encrypted = backend.encrypt_json(data)
    assert isinstance(encrypted, str)
    result = backend.decrypt_json(encrypted)
    assert result == data
