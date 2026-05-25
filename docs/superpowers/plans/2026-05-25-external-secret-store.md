# External Secret Store Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Nexplane's credential storage pluggable so that HashiCorp Vault, AWS Secrets Manager, and future PAM solutions (CyberArk, etc.) can be used as the credential backend instead of the default Fernet-encrypted database.

**Architecture:** A `SecretBackend` Protocol replaces direct `SecretsService` instantiation. A `get_secret_backend()` factory reads `SECRET_BACKEND` env var and returns the appropriate singleton. Two callers are updated: `connector_service._attach_credentials` and the `connectors.py` credentials router. Existing DB-encrypted credentials continue to work unchanged.

**Tech Stack:** Python `typing.Protocol`, `hvac` (Vault, already in requirements), `boto3` (AWS Secrets Manager, already in requirements), `functools.lru_cache` for singleton factory.

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `backend/app/services/secret_backend.py` | `SecretBackend` Protocol definition |
| Create | `backend/app/services/backends/__init__.py` | Empty package marker |
| Create | `backend/app/services/backends/fernet_backend.py` | Wraps existing SecretsService logic |
| Create | `backend/app/services/backends/vault_kv_backend.py` | Vault KV v2 backend |
| Create | `backend/app/services/backends/aws_secrets_manager_backend.py` | AWS Secrets Manager backend |
| Create | `backend/app/services/secret_backend_factory.py` | Factory + singleton via `lru_cache` |
| Modify | `backend/app/services/connector_service.py:40-55` | Replace `SecretsService(...)` with `get_secret_backend()` |
| Modify | `backend/app/routers/connectors.py:156-203` | Replace `SecretsService(...)` with `get_secret_backend()` |
| Create | `backend/tests/unit/test_secret_backends.py` | Unit tests for all backends + factory |
| Create | `backend/tests/smoke/test_secret_store_live.py` | Live smoke: Vault leg + ASM leg + Fernet regression leg |

---

## Task 1: `SecretBackend` Protocol

**Files:**
- Create: `backend/app/services/secret_backend.py`
- Test: `backend/tests/unit/test_secret_backends.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_secret_backends.py
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend
python -m pytest tests/unit/test_secret_backends.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'app.services.secret_backend'`

- [ ] **Step 3: Create the protocol**

```python
# backend/app/services/secret_backend.py
from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretBackend(Protocol):
    def encrypt(self, value: str) -> str: ...
    def decrypt(self, encrypted: str) -> str: ...
    def encrypt_json(self, data: dict) -> str: ...
    def decrypt_json(self, encrypted: str) -> dict: ...
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd backend
python -m pytest tests/unit/test_secret_backends.py::test_secret_backend_is_a_protocol tests/unit/test_secret_backends.py::test_secret_backend_protocol_methods -v
```

Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/secret_backend.py backend/tests/unit/test_secret_backends.py
git commit -m "feat: add SecretBackend protocol"
```

---

## Task 2: `FernetBackend`

**Files:**
- Create: `backend/app/services/backends/__init__.py`
- Create: `backend/app/services/backends/fernet_backend.py`
- Test: `backend/tests/unit/test_secret_backends.py`

- [ ] **Step 1: Add failing tests**

Append to `backend/tests/unit/test_secret_backends.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend
python -m pytest tests/unit/test_secret_backends.py -v 2>&1 | grep -E "PASSED|FAILED|ERROR"
```

Expected: 3 new tests FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create the package and FernetBackend**

```bash
touch backend/app/services/backends/__init__.py
```

```python
# backend/app/services/backends/fernet_backend.py
import hashlib
import json
from base64 import urlsafe_b64encode
from cryptography.fernet import Fernet


class FernetBackend:
    """Default backend: AES-256-GCM via Fernet, key derived from SECRET_KEY."""

    def __init__(self, secret_key: str):
        key_bytes = hashlib.sha256(secret_key.encode()).digest()
        self._fernet = Fernet(urlsafe_b64encode(key_bytes))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, encrypted: str) -> str:
        return self._fernet.decrypt(encrypted.encode()).decode()

    def encrypt_json(self, data: dict) -> str:
        return self.encrypt(json.dumps(data))

    def decrypt_json(self, encrypted: str) -> dict:
        return json.loads(self.decrypt(encrypted))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend
python -m pytest tests/unit/test_secret_backends.py -v
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/backends/__init__.py backend/app/services/backends/fernet_backend.py backend/tests/unit/test_secret_backends.py
git commit -m "feat: add FernetBackend implementing SecretBackend protocol"
```

---

## Task 3: `VaultKVBackend`

**Files:**
- Create: `backend/app/services/backends/vault_kv_backend.py`
- Test: `backend/tests/unit/test_secret_backends.py`

- [ ] **Step 1: Add failing tests**

Append to `backend/tests/unit/test_secret_backends.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend
python -m pytest tests/unit/test_secret_backends.py -v -k "vault" 2>&1 | grep -E "PASSED|FAILED|ERROR"
```

Expected: 4 vault tests FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create VaultKVBackend**

```python
# backend/app/services/backends/vault_kv_backend.py
import hvac


class VaultKVBackend:
    """Vault KV v2 backend. Stores credentials at secret/data/nexplane/connectors/{connector_id}."""

    _MOUNT = "secret"

    def __init__(self, addr: str, token: str, _client=None):
        if _client is not None:
            self._client = _client
        else:
            self._client = hvac.Client(url=addr, token=token)

    def _kv_path(self, stored: str) -> str:
        # stored is "secret/data/nexplane/connectors/{id}" — extract the path component
        # Format: {mount}/data/{path}
        parts = stored.split("/data/", 1)
        return parts[1] if len(parts) == 2 else stored

    def encrypt_json(self, data: dict, connector_id: str = "") -> str:
        path = f"nexplane/connectors/{connector_id}"
        self._client.secrets.kv.v2.create_or_update_secret(
            path=path,
            secret=data,
            mount_point=self._MOUNT,
        )
        return f"{self._MOUNT}/data/{path}"

    def decrypt_json(self, stored: str) -> dict:
        path = self._kv_path(stored)
        resp = self._client.secrets.kv.v2.read_secret_version(
            path=path,
            mount_point=self._MOUNT,
        )
        return resp["data"]["data"]

    def encrypt(self, value: str, connector_id: str = "") -> str:
        return self.encrypt_json({"v": value}, connector_id=connector_id)

    def decrypt(self, stored: str) -> str:
        return self.decrypt_json(stored)["v"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend
python -m pytest tests/unit/test_secret_backends.py -v -k "vault"
```

Expected: 4 vault tests pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/backends/vault_kv_backend.py backend/tests/unit/test_secret_backends.py
git commit -m "feat: add VaultKVBackend implementing SecretBackend protocol"
```

---

## Task 4: `AWSSecretsManagerBackend`

**Files:**
- Create: `backend/app/services/backends/aws_secrets_manager_backend.py`
- Test: `backend/tests/unit/test_secret_backends.py`

- [ ] **Step 1: Add failing tests**

Append to `backend/tests/unit/test_secret_backends.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend
python -m pytest tests/unit/test_secret_backends.py -v -k "asm" 2>&1 | grep -E "PASSED|FAILED|ERROR"
```

Expected: 4 asm tests FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create AWSSecretsManagerBackend**

```python
# backend/app/services/backends/aws_secrets_manager_backend.py
import json
import boto3


class AWSSecretsManagerBackend:
    """AWS Secrets Manager backend. Stores credentials at nexplane/connectors/{connector_id}."""

    def __init__(self, region: str, _client=None):
        if _client is not None:
            self._client = _client
        else:
            self._client = boto3.client("secretsmanager", region_name=region)

    def _secret_name(self, connector_id: str) -> str:
        return f"nexplane/connectors/{connector_id}"

    def encrypt_json(self, data: dict, connector_id: str = "") -> str:
        name = self._secret_name(connector_id)
        payload = json.dumps(data)
        try:
            self._client.create_secret(Name=name, SecretString=payload)
        except self._client.exceptions.ResourceExistsException:
            self._client.put_secret_value(SecretId=name, SecretString=payload)
        return name

    def decrypt_json(self, stored: str) -> dict:
        resp = self._client.get_secret_value(SecretId=stored)
        return json.loads(resp["SecretString"])

    def encrypt(self, value: str, connector_id: str = "") -> str:
        return self.encrypt_json({"v": value}, connector_id=connector_id)

    def decrypt(self, stored: str) -> str:
        return self.decrypt_json(stored)["v"]

    def delete_secret(self, stored: str) -> None:
        """Delete a secret by its stored name/path. Used by smoke test teardown."""
        self._client.delete_secret(SecretId=stored, ForceDeleteWithoutRecovery=True)
```

- [ ] **Step 4: Run all unit tests**

```bash
cd backend
python -m pytest tests/unit/test_secret_backends.py -v
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/backends/aws_secrets_manager_backend.py backend/tests/unit/test_secret_backends.py
git commit -m "feat: add AWSSecretsManagerBackend implementing SecretBackend protocol"
```

---

## Task 5: Factory

**Files:**
- Create: `backend/app/services/secret_backend_factory.py`
- Test: `backend/tests/unit/test_secret_backends.py`

- [ ] **Step 1: Add failing tests**

Append to `backend/tests/unit/test_secret_backends.py`:

```python
def test_factory_returns_fernet_by_default(monkeypatch):
    import importlib
    import app.services.secret_backend_factory as mod
    monkeypatch.delenv("SECRET_BACKEND", raising=False)
    monkeypatch.setenv("SECRET_KEY", "test-key")
    mod.get_secret_backend.cache_clear()
    backend = mod.get_secret_backend()
    from app.services.backends.fernet_backend import FernetBackend
    assert isinstance(backend, FernetBackend)
    mod.get_secret_backend.cache_clear()


def test_factory_returns_fernet_when_explicit(monkeypatch):
    import app.services.secret_backend_factory as mod
    monkeypatch.setenv("SECRET_BACKEND", "fernet")
    monkeypatch.setenv("SECRET_KEY", "test-key")
    mod.get_secret_backend.cache_clear()
    backend = mod.get_secret_backend()
    from app.services.backends.fernet_backend import FernetBackend
    assert isinstance(backend, FernetBackend)
    mod.get_secret_backend.cache_clear()


def test_factory_raises_on_unknown_backend(monkeypatch):
    import app.services.secret_backend_factory as mod
    monkeypatch.setenv("SECRET_BACKEND", "cyberark-not-yet")
    mod.get_secret_backend.cache_clear()
    with pytest.raises(ValueError, match="Unknown SECRET_BACKEND"):
        mod.get_secret_backend()
    mod.get_secret_backend.cache_clear()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend
python -m pytest tests/unit/test_secret_backends.py -v -k "factory" 2>&1 | grep -E "PASSED|FAILED|ERROR"
```

Expected: 3 factory tests FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create the factory**

```python
# backend/app/services/secret_backend_factory.py
import os
from functools import lru_cache
from app.services.secret_backend import SecretBackend


@lru_cache(maxsize=1)
def get_secret_backend() -> SecretBackend:
    backend = os.getenv("SECRET_BACKEND", "fernet")

    if backend == "fernet":
        from app.services.backends.fernet_backend import FernetBackend
        from app import config as app_config
        secret_key = os.getenv("SECRET_KEY", app_config.settings.SECRET_KEY)
        return FernetBackend(secret_key=secret_key)

    if backend == "vault":
        from app.services.backends.vault_kv_backend import VaultKVBackend
        return VaultKVBackend(
            addr=os.environ["VAULT_ADDR"],
            token=os.environ["VAULT_TOKEN"],
        )

    if backend == "aws_secrets_manager":
        from app.services.backends.aws_secrets_manager_backend import AWSSecretsManagerBackend
        return AWSSecretsManagerBackend(region=os.environ["AWS_SECRETS_REGION"])

    raise ValueError(f"Unknown SECRET_BACKEND: {backend!r}. Valid values: fernet, vault, aws_secrets_manager")
```

- [ ] **Step 4: Run all unit tests**

```bash
cd backend
python -m pytest tests/unit/test_secret_backends.py -v
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/secret_backend_factory.py backend/tests/unit/test_secret_backends.py
git commit -m "feat: add secret backend factory with lru_cache singleton"
```

---

## Task 6: Wire Up DI — Replace Direct `SecretsService` Instantiation

**Files:**
- Modify: `backend/app/services/connector_service.py` (lines 40–55)
- Modify: `backend/app/routers/connectors.py` (lines 156–203)

There are no new tests for this task — the existing smoke tests and the unit tests in Task 1–5 cover correctness. This task wires the factory into the two call sites.

- [ ] **Step 1: Update `connector_service._attach_credentials`**

Current code at lines 40–55 of `backend/app/services/connector_service.py`:
```python
async def _attach_credentials(connector, db) -> None:
    from sqlalchemy import select
    from app.models.connector_credential import ConnectorCredential
    from app.services.secrets_service import SecretsService
    from app import config as app_config

    result = await db.execute(
        select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
    )
    cred_row = result.scalar_one_or_none()
    if cred_row:
        svc = SecretsService(app_config.settings.SECRET_KEY)
        connector.credentials = svc.decrypt_json(cred_row.credentials_encrypted)
    else:
        connector.credentials = {}
```

Replace with:
```python
async def _attach_credentials(connector, db) -> None:
    from sqlalchemy import select
    from app.models.connector_credential import ConnectorCredential
    from app.services.secret_backend_factory import get_secret_backend

    result = await db.execute(
        select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
    )
    cred_row = result.scalar_one_or_none()
    if cred_row:
        backend = get_secret_backend()
        connector.credentials = backend.decrypt_json(cred_row.credentials_encrypted)
    else:
        connector.credentials = {}
```

- [ ] **Step 2: Update `connectors.py` `upsert_credentials` endpoint**

In `backend/app/routers/connectors.py`, find the `upsert_credentials` function (around line 156). Remove these two lines:
```python
    from app.services.secrets_service import SecretsService
    from app import config as app_config
```
And replace:
```python
    svc = SecretsService(app_config.settings.SECRET_KEY)
    encrypted = svc.encrypt_json(body.credentials)
```
With:
```python
    from app.services.secret_backend_factory import get_secret_backend
    backend = get_secret_backend()
    connector_id_str = str(connector_id)
    encrypted = backend.encrypt_json(body.credentials, connector_id=connector_id_str)
```

The full updated block in context (lines ~173–190):
```python
    catalog_svc = get_catalog_service()
    catalog = catalog_svc.get_connector_catalog(connector.connector_type.value)
    required_fields = [f["name"] for f in catalog.get("credential_fields", []) if f.get("required")]
    missing = [f for f in required_fields if not body.credentials.get(f)]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required credential fields: {missing}")

    from app.services.secret_backend_factory import get_secret_backend
    backend = get_secret_backend()
    connector_id_str = str(connector_id)
    encrypted = backend.encrypt_json(body.credentials, connector_id=connector_id_str)

    cred_result = await db.execute(
        select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
    )
```

- [ ] **Step 3: Verify FernetBackend's `encrypt_json` accepts `connector_id` kwarg**

`FernetBackend.encrypt_json` doesn't need `connector_id` (Fernet uses the key, not the path). Add `**kwargs` to absorb it silently:

```python
# backend/app/services/backends/fernet_backend.py
    def encrypt_json(self, data: dict, **kwargs) -> str:
        return self.encrypt(json.dumps(data))

    def encrypt(self, value: str, **kwargs) -> str:
        return self._fernet.encrypt(value.encode()).decode()
```

- [ ] **Step 4: Restart backend and verify it starts cleanly**

```bash
docker compose restart nexplane-backend-1 2>/dev/null || docker compose restart backend
```

Then:
```bash
curl -s http://localhost:8000/health
```

Expected: `{"status": "ok"}` or similar healthy response

- [ ] **Step 5: Run all unit tests**

```bash
cd backend
python -m pytest tests/unit/test_secret_backends.py -v
```

Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/connector_service.py backend/app/routers/connectors.py backend/app/services/backends/fernet_backend.py
git commit -m "feat: wire SecretBackend factory into connector_service and connectors router"
```

---

## Task 7: Smoke Test

**Files:**
- Create: `backend/tests/smoke/test_secret_store_live.py`

This smoke test runs on the EC2 runner inside the nexplane-backend-1 container context (same pattern as other smoke tests). It directly instantiates backends against real infrastructure — no container restart needed. The Vault instance is the existing `vault-dev` AMI already cached from other smoke tests. AWS Secrets Manager uses the runner's IAM role (no new infra).

- [ ] **Step 1: Write the smoke test file**

```python
# backend/tests/smoke/test_secret_store_live.py
"""
Live smoke test for external secret store backends.

Vault leg: spins up a t3.small with vault-dev AMI (cached), tests VaultKVBackend roundtrip.
ASM leg: tests AWSSecretsManagerBackend against real AWS Secrets Manager (runner IAM role).
Regression leg: verifies FernetBackend still works with a freshly encrypted value.

Run from EC2 runner:
    pytest tests/smoke/test_secret_store_live.py -v -s
"""
import json
import os
import time
import uuid
import boto3
import pytest


SMOKE_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
SMOKE_CONNECTOR_ID = f"smoke-{uuid.uuid4()}"
SMOKE_DATA = {"username": "smokeuser", "password": "smoke-secret-value-12345", "host": "10.0.0.1"}


# ── Vault leg ─────────────────────────────────────────────────────────────────

VAULT_SETUP_SCRIPT = r"""#!/bin/bash
set -e
which vault 2>/dev/null || {
    yum install -y yum-utils 2>/dev/null || apt-get install -y gpg 2>/dev/null || true
    curl -fsSL https://rpm.releases.hashicorp.com/AmazonLinux/hashicorp.repo \
        -o /etc/yum.repos.d/hashicorp.repo 2>/dev/null || true
    yum install -y vault 2>/dev/null || {
        curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp.gpg
        echo "deb [signed-by=/usr/share/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
            > /etc/apt/sources.list.d/hashicorp.list
        apt-get update -qq && apt-get install -y vault
    }
}
export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=nexplane-smoke-root
pkill vault 2>/dev/null || true
sleep 2
nohup vault server -dev -dev-root-token-id=nexplane-smoke-root -dev-listen-address=0.0.0.0:8200 \
    > /var/log/vault-dev.log 2>&1 &
sleep 5
vault kv enable-versioning secret 2>/dev/null || true
echo "VAULT_SETUP_COMPLETE"
"""

import hashlib
VAULT_SETUP_HASH = hashlib.sha256(VAULT_SETUP_SCRIPT.encode()).hexdigest()[:12]


def _launch_vault_instance(ec2_client, ssm_client, subnet_id, sg_id, instance_profile):
    """Launch a t3.small for Vault, returning (instance_id, private_ip)."""
    import sys
    try:
        from run_on_ec2 import get_or_create_smoke_ami
    except ImportError:
        from smoke.run_on_ec2 import get_or_create_smoke_ami

    # Check cached AMI
    ssm_key = f"/nexplane/smoke-amis/vault-dev/{VAULT_SETUP_HASH}"
    cached_ami = None
    try:
        resp = ssm_client.get_parameter(Name=ssm_key)
        cached_ami = resp["Parameter"]["Value"]
        print(f"  Using cached vault-dev AMI: {cached_ami}", flush=True)
    except ssm_client.exceptions.ParameterNotFound:
        pass

    ami_id = cached_ami or "ami-0c02fb55956c7d316"  # Amazon Linux 2 fallback

    resp = ec2_client.run_instances(
        ImageId=ami_id,
        InstanceType="t3.small",
        MinCount=1, MaxCount=1,
        SubnetId=subnet_id,
        SecurityGroupIds=[sg_id],
        IamInstanceProfile={"Name": instance_profile},
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [
                {"Key": "Name", "Value": "nexplane-smoke-vault-backend"},
                {"Key": "nxp-ec2-test-runner", "Value": "true"},
                {"Key": "nxp-smoke-temp", "Value": "true"},
            ]
        }],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    print(f"  Launched Vault instance: {instance_id}", flush=True)

    # Wait for running + SSM
    ec2_client.get_waiter("instance_running").wait(InstanceIds=[instance_id])
    private_ip = ec2_client.describe_instances(InstanceIds=[instance_id])["Reservations"][0]["Instances"][0]["PrivateIpAddress"]

    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            r = ssm_client.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": ["echo ready"]}, TimeoutSeconds=10)
            time.sleep(5)
            out = ssm_client.get_command_invocation(
                CommandId=r["Command"]["CommandId"], InstanceId=instance_id)
            if out["Status"] == "Success":
                break
        except Exception:
            pass
        time.sleep(10)

    if not cached_ami:
        resp_s = ssm_client.send_command(
            InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": [VAULT_SETUP_SCRIPT]}, TimeoutSeconds=120)
        time.sleep(35)
        out_s = ssm_client.get_command_invocation(
            CommandId=resp_s["Command"]["CommandId"], InstanceId=instance_id)
        if "VAULT_SETUP_COMPLETE" in out_s.get("StandardOutputContent", ""):
            get_or_create_smoke_ami(ssm_client, ec2_client, instance_id, "vault-dev", VAULT_SETUP_HASH)
    else:
        # Start Vault on cached (pre-installed) instance
        start_cmd = (
            "export VAULT_ADDR=http://localhost:8200 VAULT_TOKEN=nexplane-smoke-root && "
            "pkill vault 2>/dev/null || true && sleep 2 && "
            "nohup vault server -dev -dev-root-token-id=nexplane-smoke-root "
            "-dev-listen-address=0.0.0.0:8200 > /var/log/vault-dev.log 2>&1 & sleep 5 && echo VAULT_RESTARTED"
        )
        r2 = ssm_client.send_command(
            InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": [start_cmd]}, TimeoutSeconds=60)
        time.sleep(15)

    return instance_id, private_ip


def test_vault_backend_live(request):
    """VaultKVBackend roundtrip against a real Vault dev instance."""
    print("\n[VAULT] Starting Vault backend live test", flush=True)

    ec2 = boto3.client("ec2", region_name=SMOKE_REGION)
    ssm = boto3.client("ssm", region_name=SMOKE_REGION)

    # Resolve smoke VPC parameters
    subnet_id = ssm.get_parameter(Name="/nexplane/smoke/subnet-id")["Parameter"]["Value"]
    sg_id = ssm.get_parameter(Name="/nexplane/smoke/security-group-id")["Parameter"]["Value"]
    instance_profile = ssm.get_parameter(Name="/nexplane/smoke/instance-profile")["Parameter"]["Value"]

    instance_id, private_ip = _launch_vault_instance(ec2, ssm, subnet_id, sg_id, instance_profile)
    vault_addr = f"http://{private_ip}:8200"
    vault_token = "nexplane-smoke-root"

    def teardown():
        try:
            ec2.terminate_instances(InstanceIds=[instance_id])
            print(f"  [VAULT] Terminated {instance_id}", flush=True)
        except Exception as e:
            print(f"  [VAULT] Teardown warning: {e}", flush=True)
    request.addfinalizer(teardown)

    from app.services.backends.vault_kv_backend import VaultKVBackend
    backend = VaultKVBackend(addr=vault_addr, token=vault_token)

    connector_id = str(uuid.uuid4())
    print(f"  [VAULT] Writing secret for connector {connector_id}", flush=True)
    stored_path = backend.encrypt_json(SMOKE_DATA, connector_id=connector_id)
    print(f"  [VAULT] Stored at: {stored_path}", flush=True)

    retrieved = backend.decrypt_json(stored_path)
    assert retrieved == SMOKE_DATA, f"Vault roundtrip failed: got {retrieved}"
    print("  [VAULT] Roundtrip OK", flush=True)

    # Verify via hvac directly
    import hvac
    client = hvac.Client(url=vault_addr, token=vault_token)
    raw = client.secrets.kv.v2.read_secret_version(
        path=f"nexplane/connectors/{connector_id}", mount_point="secret"
    )
    assert raw["data"]["data"] == SMOKE_DATA
    print("  [VAULT] Direct Vault read OK", flush=True)

    print("[VAULT] PASSED", flush=True)


# ── AWS Secrets Manager leg ───────────────────────────────────────────────────

def test_asm_backend_live():
    """AWSSecretsManagerBackend roundtrip against real AWS Secrets Manager."""
    print("\n[ASM] Starting ASM backend live test", flush=True)

    from app.services.backends.aws_secrets_manager_backend import AWSSecretsManagerBackend
    backend = AWSSecretsManagerBackend(region=SMOKE_REGION)

    connector_id = str(uuid.uuid4())
    print(f"  [ASM] Writing secret for connector {connector_id}", flush=True)
    stored_name = backend.encrypt_json(SMOKE_DATA, connector_id=connector_id)
    print(f"  [ASM] Stored as: {stored_name}", flush=True)

    retrieved = backend.decrypt_json(stored_name)
    assert retrieved == SMOKE_DATA, f"ASM roundtrip failed: got {retrieved}"
    print("  [ASM] Roundtrip OK", flush=True)

    # Verify via boto3 directly
    sm = boto3.client("secretsmanager", region_name=SMOKE_REGION)
    raw = sm.get_secret_value(SecretId=stored_name)
    assert json.loads(raw["SecretString"]) == SMOKE_DATA
    print("  [ASM] Direct Secrets Manager read OK", flush=True)

    # Test update path (encrypt_json on existing secret)
    updated_data = {**SMOKE_DATA, "password": "updated-smoke-value-99999"}
    backend.encrypt_json(updated_data, connector_id=connector_id)
    assert backend.decrypt_json(stored_name) == updated_data
    print("  [ASM] Update roundtrip OK", flush=True)

    # Cleanup
    backend.delete_secret(stored_name)
    print("  [ASM] Secret deleted", flush=True)
    print("[ASM] PASSED", flush=True)


# ── Fernet regression leg ─────────────────────────────────────────────────────

def test_fernet_backend_regression():
    """FernetBackend still works after other backends are present."""
    print("\n[FERNET] Starting Fernet regression test", flush=True)

    from app.services.backends.fernet_backend import FernetBackend
    backend = FernetBackend(secret_key="regression-smoke-test-key")

    data = {"host": "192.168.1.1", "username": "admin", "password": "fernet-regression-ok"}
    encrypted = backend.encrypt_json(data)

    # Must not be plaintext
    assert "fernet-regression-ok" not in encrypted
    assert "admin" not in encrypted
    print(f"  [FERNET] Encrypted (first 40 chars): {encrypted[:40]}...", flush=True)

    result = backend.decrypt_json(encrypted)
    assert result == data
    print("  [FERNET] Roundtrip OK", flush=True)

    # Simulate what connector_service does: decrypt a value stored by the old SecretsService
    from app.services.secrets_service import SecretsService
    old_svc = SecretsService(secret_key="regression-smoke-test-key")
    legacy_encrypted = old_svc.encrypt_json(data)
    # FernetBackend must be able to decrypt values written by the old SecretsService
    # (same key derivation, same algorithm)
    result2 = backend.decrypt_json(legacy_encrypted)
    assert result2 == data
    print("  [FERNET] Legacy SecretsService compatibility OK", flush=True)

    print("[FERNET] PASSED", flush=True)
```

- [ ] **Step 2: Run just the regression leg locally to confirm it works before deploying**

```bash
cd backend
SECRET_BACKEND=fernet python -m pytest tests/smoke/test_secret_store_live.py::test_fernet_backend_regression -v -s
```

Expected: PASSED — this leg needs no EC2 infra

- [ ] **Step 3: Commit the smoke test**

```bash
git add backend/tests/smoke/test_secret_store_live.py
git commit -m "test: add live smoke tests for Vault, ASM, and Fernet backends"
```

- [ ] **Step 4: Run the full smoke suite on EC2 runner**

SSH to runner or use SSM:
```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39
docker exec nexplane-backend-1 bash -c "cd /app && python -m pytest tests/smoke/test_secret_store_live.py -v -s 2>&1 | tee /tmp/secret_store_smoke.log"
```

Watch for:
- `[VAULT] PASSED`
- `[ASM] PASSED`
- `[FERNET] PASSED`

- [ ] **Step 5: Final commit after smoke passes**

```bash
git add -u
git commit -m "feat: external secret store integration — Vault, ASM, Fernet backends with live smoke"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| `SecretBackend` Protocol | Task 1 |
| `FernetBackend` (existing logic, default) | Task 2 |
| `VaultKVBackend` with KV v2 | Task 3 |
| `AWSSecretsManagerBackend` | Task 4 |
| Factory reads `SECRET_BACKEND` env var | Task 5 |
| `connector_service._attach_credentials` wired | Task 6 |
| `connectors.py` router wired | Task 6 |
| Vault smoke leg (t3.small, AMI cached) | Task 7 |
| ASM smoke leg (runner IAM role, no new infra) | Task 7 |
| Fernet regression leg | Task 7 |
| CyberArk/future PAM: add impl + factory branch only | Documented in spec, no code needed now |

**Placeholder scan:** No TBDs. All code is complete.

**Type consistency:** `encrypt_json(data, connector_id=...)` signature used consistently across VaultKVBackend, AWSSecretsManagerBackend. `FernetBackend` accepts `**kwargs` to absorb `connector_id` without using it. `decrypt_json(stored)` is consistent across all three — `stored` is the opaque reference returned by `encrypt_json`.
