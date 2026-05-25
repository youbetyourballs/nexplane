# External Secret Store Integration Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow Nexplane to store and retrieve connector credentials from external secret stores (HashiCorp Vault, AWS Secrets Manager, and future PAM solutions like CyberArk) as an alternative to the default Fernet-encrypted database backend, with no disruption to existing credentials.

**Architecture:** A `SecretBackend` protocol replaces the concrete `SecretsService` class. A factory function reads `SECRET_BACKEND` env var at startup and injects the appropriate implementation. All callers remain unchanged.

---

## Background

Credentials for connectors are currently stored as a Fernet-encrypted JSON blob in the `connector_credentials.credentials_encrypted` column. `SecretsService` handles encrypt/decrypt. Its docstring already notes that "future implementations can delegate to HashiCorp Vault, AWS Secrets Manager, or an HSM without changing callers."

This feature makes that intent concrete.

---

## Design Constraints

- **Existing DB-encrypted credentials must continue to work** after this change. No migration, no data loss.
- **Vault and AWS Secrets Manager are the two concrete backends** shipping with this feature. The protocol must not be Vault-specific.
- **CyberArk and other PAM solutions** must be addable later by implementing the protocol + adding an env var value — no other changes required.
- **Vault is optional**. If `SECRET_BACKEND` is not set, behavior is identical to today.
- **Smoke test must not touch production credentials**. The Fernet regression leg runs after Vault/ASM legs to confirm existing connectors are unaffected.

---

## Section 1: Protocol & Factory

### `SecretBackend` Protocol

Defined in `backend/app/services/secret_backend.py`:

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class SecretBackend(Protocol):
    def encrypt(self, value: str) -> str: ...
    def decrypt(self, encrypted: str) -> str: ...
    def encrypt_json(self, data: dict) -> str: ...
    def decrypt_json(self, encrypted: str) -> dict: ...
```

These four methods are the complete interface. All existing callers use only these methods.

### Factory

Defined in `backend/app/services/secret_backend_factory.py`:

```python
import os

def get_secret_backend() -> SecretBackend:
    backend = os.getenv("SECRET_BACKEND", "fernet")
    if backend == "fernet":
        return FernetBackend(secret_key=settings.SECRET_KEY)
    elif backend == "vault":
        return VaultKVBackend(addr=os.environ["VAULT_ADDR"], token=os.environ["VAULT_TOKEN"])
    elif backend == "aws_secrets_manager":
        return AWSSecretsManagerBackend(region=os.environ["AWS_SECRETS_REGION"])
    else:
        raise ValueError(f"Unknown SECRET_BACKEND: {backend!r}")
```

The factory is called once at app startup and the result is registered as a FastAPI dependency. All callers that currently instantiate `SecretsService` directly are updated to use `Depends(get_secret_backend)`.

---

## Section 2: Concrete Implementations

### `FernetBackend`

File: `backend/app/services/backends/fernet_backend.py`

Wraps the current `SecretsService` logic verbatim. Existing `credentials_encrypted` blobs in the DB remain valid — no migration needed. This is the default when `SECRET_BACKEND` is unset or `"fernet"`.

### `VaultKVBackend`

File: `backend/app/services/backends/vault_kv_backend.py`

- Uses Vault KV v2 engine.
- Secret path: `secret/data/nexplane/connectors/{connector_id}`
- `encrypt_json(data)` → writes dict to Vault, returns the Vault path as the stored "ciphertext."
- `decrypt_json(path)` → reads from Vault by path, returns the dict.
- `encrypt(value)` / `decrypt(path)` → single-value variants: wrap as `{"v": value}`, delegate to `encrypt_json`/`decrypt_json`, return `result["v"]`.
- Auth: `VAULT_ADDR` + `VAULT_TOKEN` env vars. Root token is acceptable for dev/smoke. **AppRole is the documented production auth pattern** — documented in ops runbook, not enforced by code in this iteration.
- HTTP client: `hvac` Python library.

### `AWSSecretsManagerBackend`

File: `backend/app/services/backends/aws_secrets_manager_backend.py`

- Uses AWS Secrets Manager via `boto3`.
- Secret name: `nexplane/connectors/{connector_id}`
- `encrypt_json(data)` → `create_secret` or `update_secret` with JSON string value, returns secret name as stored "ciphertext."
- `decrypt_json(name)` → `get_secret_value`, returns parsed JSON dict.
- Auth: instance IAM role (no static credentials needed when running on EC2). `AWS_SECRETS_REGION` env var selects the region.
- Cleanup: `decrypt_json` does not delete; deletion is explicit via a separate `delete_secret(name)` helper called during rollback/teardown.

### Connector credential DB row

The `connector_credentials.credentials_encrypted` column continues to exist for all backends. For Vault and ASM backends, this column stores the **path/name** (a stable pointer), not actual ciphertext. No schema migration is required. The column name remains `credentials_encrypted` for backward compatibility.

---

## Section 3: Dependency Injection Wiring

Current: `connector_service.py` imports and instantiates `SecretsService` directly.

After: `connector_service._attach_credentials()` and `connectors.py` router receive the backend via `Depends(get_secret_backend)`. The factory is called once at startup (singleton pattern via `@lru_cache` on the factory or FastAPI `lifespan`).

No other files change.

---

## Section 4: Smoke Test

### Infrastructure

- **Vault leg**: t3.small EC2, Vault OSS installed, started in dev mode with a known root token. AMI cached via `get_or_create_smoke_ami()` (hash of setup script → SSM → AMI ID). No public IP; accessed via private VPC IP.
- **AWS Secrets Manager leg**: No new EC2. The runner's existing IAM role grants `secretsmanager:*` on `nexplane/*`. `AWS_SECRETS_REGION` set to the smoke region.

### Smoke Phase Steps

**Vault leg**
1. Launch Vault AMI (or use cached AMI).
2. Via SSM command CR (dogfooding), set `SECRET_BACKEND=vault`, `VAULT_ADDR`, `VAULT_TOKEN` in backend container environment and restart container.
3. POST credentials for a test connector via API → credentials land in Vault KV.
4. Create and execute a CR using that connector → executor reads credentials from Vault at runtime.
5. Assert the Vault path `secret/data/nexplane/connectors/{id}` exists and contains expected keys.
6. Execute rollback CR → credential path deleted from Vault.

**AWS Secrets Manager leg**
1. Via SSM command CR, set `SECRET_BACKEND=aws_secrets_manager`, `AWS_SECRETS_REGION` and restart backend container.
2. POST credentials for a second test connector → secret lands in AWS Secrets Manager at `nexplane/connectors/{id}`.
3. Create and execute a CR → executor reads from Secrets Manager.
4. Assert secret exists via boto3 `get_secret_value`.
5. Execute rollback → secret deleted from Secrets Manager.

**Regression leg**
1. Via SSM command CR, restore `SECRET_BACKEND=fernet` and restart backend container.
2. Verify a pre-existing DB-encrypted connector (created before the test) can still resolve credentials and execute a CR successfully.
3. Assert no credentials were modified or deleted in the DB.

---

## Section 5: Adding Future PAM Backends (CyberArk, BeyondTrust, Delinea)

To add a new backend:
1. Create `backend/app/services/backends/{vendor}_backend.py` implementing `SecretBackend`.
2. Add an `elif backend == "{vendor}":` branch in `secret_backend_factory.py`.
3. Document required env vars.
4. Add a smoke leg.

No other files change.

---

## Out of Scope

- Per-org or per-connector backend selection (global env var only for now).
- AppRole or AWS IAM Vault auth enforcement in code (documented pattern only).
- UI for configuring Vault/ASM connection details (env var only).
- Migration tooling to move existing Fernet credentials into Vault.
- Secret rotation orchestration through Vault's dynamic secrets engine.
