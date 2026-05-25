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
