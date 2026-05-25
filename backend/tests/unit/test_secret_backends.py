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


def test_vault_backend_satisfies_protocol():
    from unittest.mock import MagicMock
    from app.services.secret_backend import SecretBackend
    from app.services.backends.vault_kv_backend import VaultKVBackend
    mock_client = MagicMock()
    backend = VaultKVBackend(addr="http://localhost:8200", token="root", _client=mock_client)
    assert isinstance(backend, SecretBackend)


def test_vault_backend_encrypt_json_writes_to_vault():
    from unittest.mock import MagicMock
    from app.services.backends.vault_kv_backend import VaultKVBackend
    mock_client = MagicMock()
    backend = VaultKVBackend(addr="http://localhost:8200", token="root", _client=mock_client)
    connector_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    data = {"username": "admin", "password": "s3cr3t"}
    path = backend.encrypt_json(data, connector_id=connector_id)
    mock_client.secrets.kv.v2.create_or_update_secret.assert_called_once_with(
        path=f"nexplane/connectors/{connector_id}",
        secret=data,
        mount_point="secret",
    )
    assert path == f"secret/data/nexplane/connectors/{connector_id}"


def test_vault_backend_decrypt_json_reads_from_vault():
    from unittest.mock import MagicMock
    from app.services.backends.vault_kv_backend import VaultKVBackend
    mock_client = MagicMock()
    mock_client.secrets.kv.v2.read_secret_version.return_value = {
        "data": {"data": {"username": "admin", "password": "s3cr3t"}}
    }
    backend = VaultKVBackend(addr="http://localhost:8200", token="root", _client=mock_client)
    path = "secret/data/nexplane/connectors/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    result = backend.decrypt_json(path)
    assert result == {"username": "admin", "password": "s3cr3t"}
    mock_client.secrets.kv.v2.read_secret_version.assert_called_once_with(
        path="nexplane/connectors/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        mount_point="secret",
    )


def test_vault_backend_encrypt_decrypt_single_value():
    from unittest.mock import MagicMock
    from app.services.backends.vault_kv_backend import VaultKVBackend
    mock_client = MagicMock()
    mock_client.secrets.kv.v2.read_secret_version.return_value = {
        "data": {"data": {"v": "my-secret-value"}}
    }
    backend = VaultKVBackend(addr="http://localhost:8200", token="root", _client=mock_client)
    path = backend.encrypt("my-secret-value", connector_id="test-id")
    result = backend.decrypt(path)
    assert result == "my-secret-value"


def test_asm_backend_satisfies_protocol():
    from unittest.mock import MagicMock
    from app.services.secret_backend import SecretBackend
    from app.services.backends.aws_secrets_manager_backend import AWSSecretsManagerBackend
    mock_client = MagicMock()
    backend = AWSSecretsManagerBackend(region="us-east-1", _client=mock_client)
    assert isinstance(backend, SecretBackend)


def test_asm_backend_encrypt_json_creates_secret():
    from unittest.mock import MagicMock
    import json
    from app.services.backends.aws_secrets_manager_backend import AWSSecretsManagerBackend
    mock_client = MagicMock()
    mock_client.exceptions.ResourceExistsException = Exception
    backend = AWSSecretsManagerBackend(region="us-east-1", _client=mock_client)
    connector_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    data = {"username": "admin", "password": "s3cr3t"}
    name = backend.encrypt_json(data, connector_id=connector_id)
    expected_name = f"nexplane/connectors/{connector_id}"
    assert name == expected_name
    mock_client.create_secret.assert_called_once_with(
        Name=expected_name,
        SecretString=json.dumps(data),
    )


def test_asm_backend_encrypt_json_updates_existing_secret():
    from unittest.mock import MagicMock, call
    import json
    from app.services.backends.aws_secrets_manager_backend import AWSSecretsManagerBackend

    class FakeResourceExists(Exception):
        pass

    mock_client = MagicMock()
    mock_client.exceptions.ResourceExistsException = FakeResourceExists
    mock_client.create_secret.side_effect = FakeResourceExists("already exists")
    backend = AWSSecretsManagerBackend(region="us-east-1", _client=mock_client)
    connector_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    data = {"username": "admin", "password": "s3cr3t"}
    name = backend.encrypt_json(data, connector_id=connector_id)
    mock_client.put_secret_value.assert_called_once_with(
        SecretId=f"nexplane/connectors/{connector_id}",
        SecretString=json.dumps(data),
    )


def test_asm_backend_decrypt_json_reads_secret():
    from unittest.mock import MagicMock
    import json
    from app.services.backends.aws_secrets_manager_backend import AWSSecretsManagerBackend
    mock_client = MagicMock()
    data = {"username": "admin", "password": "s3cr3t"}
    mock_client.get_secret_value.return_value = {"SecretString": json.dumps(data)}
    backend = AWSSecretsManagerBackend(region="us-east-1", _client=mock_client)
    name = "nexplane/connectors/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    result = backend.decrypt_json(name)
    assert result == data
    mock_client.get_secret_value.assert_called_once_with(SecretId=name)
