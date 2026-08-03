# Cross-Cloud Container Image Transfer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `container_image_transfer` CR that pulls a container image from any of ECR/OCIR/ACR/GCR and pushes it to any other, with full rollback.

**Architecture:** One cloud-agnostic executor inspects the destination connector type and routes to one of two transfer paths: ACR import API (server-side, no agent) or Nexplane agent Docker pull/tag/push via `dispatch_agent_job("run_command", ...)` (all other destination clouds). Source and destination credentials are loaded from the DB using `source_connector_id` / `dest_connector_id` parameters; the agent never stores them.

**Tech Stack:** Python asyncio executor pattern, existing `_client.py` per-cloud credential factories, `dispatch_agent_job("run_command", ...)` for agent Docker path, Azure Management API for ACR import, `azure.identity.ClientSecretCredential`, `boto3` for ECR, `google-auth` for GCR, `oci` SDK for OCIR.

## Global Constraints

- `ROLLBACK_CAPABILITY = "full"` with one caveat: if destination registry GC policy deleted the untagged original manifest between transfer and rollback, degrade gracefully to `rolled_back: "partial"` with `note: "partial_original_manifest_gc_deleted"`.
- Executor follows the standard 5-phase pattern: preflight → snapshot → transfer → verify → report.
- Credentials are loaded per-call from DB via `ConnectorCredential` + `get_secret_backend()`; never stored by the agent.
- `overwrite_existing` defaults to `false`; preflight fails explicitly (status `"failed"`) when destination tag exists and flag is not set.
- Tag is always preserved from source; only the destination repo path is configurable via `dest_repo`.
- Smoke test must cover both transfer paths: agent (ECR→OCIR) and ACR import (ECR→ACR).
- GCR covered by unit tests only (shares agent-path code with ECR/OCIR).
- All new executor code lives under `backend/app/connectors/executors/`.
- New ChangeType enum value `container_image_transfer` and Alembic migration required before executor is usable.
- SPDX header `# SPDX-License-Identifier: AGPL-3.0-only` on every new file.

---

## File Structure

- **Create:** `backend/app/connectors/executors/_registry_client.py` — per-cloud registry helpers (hostname, token, tag check, digest lookup, delete, restore)
- **Create:** `backend/app/connectors/executors/container_image_transfer.py` — main executor (routes to ACR import or agent path)
- **Create:** `backend/app/connectors/executors/container_registry/__init__.py` — empty, makes this a Python package
- **Create:** `backend/app/connectors/executors/container_registry/container_image_transfer.py` — re-export shim for catalog routing
- **Create:** `backend/app/connectors/catalog/container_registry.json` — ActionCatalogService entry (used by rollback routing)
- **Modify:** `backend/app/models/change_request.py` — add `container_image_transfer` to ChangeType enum
- **Create:** `backend/alembic/versions/<hash>_add_container_image_transfer_change_type.py` — Alembic migration
- **Modify:** `backend/app/workflows/activities.py` — add execute dispatch
- **Modify:** `backend/app/services/rollback_executor.py` — add `"container_registry"` to `_conn_types_to_try`
- **Create:** `backend/tests/unit/test_container_image_transfer.py` — unit tests (mock registry calls, both transfer paths, rollback cases)
- **Create:** `backend/tests/smoke/test_smoke_container_image_transfer.py` — smoke test (AGENT_TRANSFER and ACR_IMPORT phases)

---

## Task 1: ChangeType enum + Alembic migration

**Files:**
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/<hash>_add_container_image_transfer_change_type.py`

**Interfaces:**
- Produces: `ChangeType.container_image_transfer` enum value, consumed by Tasks 3 and 5

- [ ] **Step 1: Locate the ChangeType enum and find the right alphabetical insertion point**

```bash
grep -n "container_image\|ecs_rolling\|ChangeType" backend/app/models/change_request.py | head -20
```

You will see the enum class definition and nearby entries like `ecs_rolling_deploy`. Add the new value alphabetically near the "c" entries (e.g. after `certificate_rotation`, before `check_fleet_health`).

- [ ] **Step 2: Write failing test**

```python
# backend/tests/unit/test_container_image_transfer.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

def test_change_type_enum_value():
    from app.models.change_request import ChangeType
    assert ChangeType.container_image_transfer.value == "container_image_transfer"
```

Run: `pytest backend/tests/unit/test_container_image_transfer.py::test_change_type_enum_value -v`
Expected: FAIL — `AttributeError: container_image_transfer`

- [ ] **Step 3: Add the enum value**

In `backend/app/models/change_request.py`, add to the ChangeType enum:

```python
container_image_transfer = "container_image_transfer"
```

Place it alphabetically among the "c" entries.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest backend/tests/unit/test_container_image_transfer.py::test_change_type_enum_value -v`
Expected: PASS

- [ ] **Step 5: Generate and apply Alembic migration (run on EC2 inside backend container)**

```bash
docker exec nexplane-backend-1 bash -c "cd /app && alembic revision --autogenerate -m 'add_container_image_transfer_change_type'"
```

Then copy the generated file back to your local working copy via `scp`. Apply it:

```bash
docker exec nexplane-backend-1 bash -c "cd /app && alembic upgrade head"
```

Expected: `Running upgrade ... -> <hash>, add_container_image_transfer_change_type`

Check the generated migration — for PostgreSQL enum columns, autogenerate produces an `ALTER TYPE ... ADD VALUE` statement. If it produces an empty migration (SQLite in dev doesn't track enum columns), manually add:

```python
def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE changetype ADD VALUE IF NOT EXISTS 'container_image_transfer'")

def downgrade():
    pass  # PostgreSQL cannot drop enum values; leave it
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/change_request.py backend/alembic/versions/
git add backend/tests/unit/test_container_image_transfer.py
git commit -m "feat(image-transfer): add container_image_transfer ChangeType enum + migration"
```

---

## Task 2: Registry client helpers (`_registry_client.py`)

**Files:**
- Create: `backend/app/connectors/executors/_registry_client.py`
- Modify: `backend/tests/unit/test_container_image_transfer.py`

**Interfaces:**
- Consumes: connector credentials dict (as decrypted from `ConnectorCredential` in the DB)
- Produces (all public, consumed by Task 3):
  - `get_registry_hostname(connector_type: str, creds: dict) -> str`
  - `get_auth_token(connector_type: str, creds: dict, repo: str = "") -> str` — Docker-compatible bearer token
  - `check_tag_exists(connector_type: str, creds: dict, repo: str, tag: str) -> bool`
  - `get_manifest_digest(connector_type: str, creds: dict, repo: str, tag: str) -> str | None` — `"sha256:..."` or None
  - `delete_tag(connector_type: str, creds: dict, repo: str, tag: str) -> None`
  - `restore_tag_by_digest(connector_type: str, creds: dict, repo: str, tag: str, digest: str) -> bool` — returns False if manifest GC'd

- [ ] **Step 1: Write failing hostname tests**

Add to `backend/tests/unit/test_container_image_transfer.py`:

```python
from app.connectors.executors._registry_client import get_registry_hostname

def test_get_registry_hostname_ecr():
    creds = {"account_id": "123456789012", "region": "us-east-1"}
    assert get_registry_hostname("aws", creds) == "123456789012.dkr.ecr.us-east-1.amazonaws.com"

def test_get_registry_hostname_acr():
    creds = {"registry_name": "myregistry"}
    assert get_registry_hostname("azure", creds) == "myregistry.azurecr.io"

def test_get_registry_hostname_gcr():
    creds = {"project_id": "myproject", "region": "us"}
    assert get_registry_hostname("gcp", creds) == "us-docker.pkg.dev/myproject"

def test_get_registry_hostname_ocir():
    creds = {"region": "us-ashburn-1", "tenancy_namespace": "mynamespace"}
    assert get_registry_hostname("oci", creds) == "iad.ocir.io/mynamespace"
```

Run: `pytest backend/tests/unit/test_container_image_transfer.py -k "hostname" -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 2: Create `_registry_client.py` with `get_registry_hostname`**

```python
# backend/app/connectors/executors/_registry_client.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Per-cloud container registry helpers for container_image_transfer executor."""

OCIR_REGION_MAP = {
    "us-ashburn-1": "iad", "us-phoenix-1": "phx", "eu-frankfurt-1": "fra",
    "ap-tokyo-1": "nrt", "ap-sydney-1": "syd", "uk-london-1": "lhr",
    "ca-toronto-1": "yyz", "sa-saopaulo-1": "gru", "ap-mumbai-1": "bom",
    "ap-seoul-1": "icn", "ap-osaka-1": "kix", "me-jeddah-1": "jed",
}


def get_registry_hostname(connector_type: str, creds: dict) -> str:
    if connector_type == "aws":
        return f"{creds['account_id']}.dkr.ecr.{creds['region']}.amazonaws.com"
    if connector_type == "azure":
        return f"{creds['registry_name']}.azurecr.io"
    if connector_type == "gcp":
        region = creds.get("region", "us")
        return f"{region}-docker.pkg.dev/{creds['project_id']}"
    if connector_type == "oci":
        prefix = OCIR_REGION_MAP.get(creds["region"], creds["region"].split("-")[0])
        return f"{prefix}.ocir.io/{creds['tenancy_namespace']}"
    raise ValueError(f"Unsupported connector_type: {connector_type}")
```

- [ ] **Step 3: Run hostname tests**

Run: `pytest backend/tests/unit/test_container_image_transfer.py -k "hostname" -v`
Expected: 4 PASS

- [ ] **Step 4: Implement auth token helpers**

Add to `_registry_client.py`:

```python
def _get_acr_token(creds: dict, repo: str) -> str:
    """Exchange AAD token for an ACR scope token for the given repo."""
    import requests
    from azure.identity import ClientSecretCredential
    hostname = get_registry_hostname("azure", creds)
    aad_token = ClientSecretCredential(
        creds["tenant_id"], creds["client_id"], creds["client_secret"]
    ).get_token("https://management.azure.com/.default").token
    r = requests.post(f"https://{hostname}/oauth2/exchange",
                      data={"grant_type": "access_token", "service": hostname,
                            "access_token": aad_token}, timeout=30)
    r.raise_for_status()
    refresh_token = r.json()["refresh_token"]
    r2 = requests.post(f"https://{hostname}/oauth2/token",
                       data={"grant_type": "refresh_token", "service": hostname,
                             "scope": f"repository:{repo}:pull,push",
                             "refresh_token": refresh_token}, timeout=30)
    r2.raise_for_status()
    return r2.json()["access_token"]


def _get_gcr_token(creds: dict) -> str:
    import json as _json
    from google.oauth2 import service_account
    import google.auth.transport.requests
    key_json = creds.get("service_account_key_json", {})
    if isinstance(key_json, str):
        key_json = _json.loads(key_json)
    sa_creds = service_account.Credentials.from_service_account_info(
        key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    sa_creds.refresh(google.auth.transport.requests.Request())
    return sa_creds.token


def _get_ocir_raw_token(creds: dict) -> str:
    """Return raw auth_token for docker login (not base64-encoded)."""
    return creds["auth_token"]


def get_auth_token(connector_type: str, creds: dict, repo: str = "") -> str:
    """Return a short-lived Docker-compatible bearer token for the registry."""
    if connector_type == "aws":
        import boto3, base64
        ecr = boto3.client("ecr", region_name=creds["region"],
                           aws_access_key_id=creds["access_key_id"],
                           aws_secret_access_key=creds["secret_access_key"])
        token_b64 = ecr.get_authorization_token()["authorizationData"][0]["authorizationToken"]
        # ECR returns base64("AWS:password") — split to get just the password
        return base64.b64decode(token_b64).decode().split(":", 1)[1]
    if connector_type == "azure":
        return _get_acr_token(creds, repo)
    if connector_type == "gcp":
        return _get_gcr_token(creds)
    if connector_type == "oci":
        return _get_ocir_raw_token(creds)
    raise ValueError(f"Unsupported connector_type: {connector_type}")
```

- [ ] **Step 5: Implement `get_manifest_digest` and `check_tag_exists`**

Add to `_registry_client.py`:

```python
def get_manifest_digest(connector_type: str, creds: dict, repo: str, tag: str) -> str | None:
    """Return SHA256 digest for repo:tag, or None if not found."""
    if connector_type == "aws":
        import boto3
        ecr = boto3.client("ecr", region_name=creds["region"],
                           aws_access_key_id=creds["access_key_id"],
                           aws_secret_access_key=creds["secret_access_key"])
        try:
            resp = ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": tag}])
            return resp["imageDetails"][0]["imageDigest"]
        except ecr.exceptions.ImageNotFoundException:
            return None
    if connector_type in ("azure", "gcp"):
        import requests
        hostname = get_registry_hostname(connector_type, creds)
        token = get_auth_token(connector_type, creds, repo)
        r = requests.get(f"https://{hostname}/v2/{repo}/manifests/{tag}",
                         headers={"Authorization": f"Bearer {token}",
                                  "Accept": "application/vnd.docker.distribution.manifest.v2+json"},
                         timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.headers.get("Docker-Content-Digest")
    if connector_type == "oci":
        import requests, base64
        namespace = creds["tenancy_namespace"]
        region_prefix = OCIR_REGION_MAP.get(creds["region"], creds["region"].split("-")[0])
        hostname = f"{region_prefix}.ocir.io"
        username = f"{namespace}/{creds['username']}"
        auth = base64.b64encode(f"{username}:{creds['auth_token']}".encode()).decode()
        r = requests.get(f"https://{hostname}/v2/{namespace}/{repo}/manifests/{tag}",
                         headers={"Authorization": f"Basic {auth}",
                                  "Accept": "application/vnd.docker.distribution.manifest.v2+json"},
                         timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.headers.get("Docker-Content-Digest")
    raise ValueError(f"Unsupported connector_type: {connector_type}")


def check_tag_exists(connector_type: str, creds: dict, repo: str, tag: str) -> bool:
    return get_manifest_digest(connector_type, creds, repo, tag) is not None
```

- [ ] **Step 6: Write tests for check_tag_exists (mocked)**

Add to `backend/tests/unit/test_container_image_transfer.py`:

```python
def test_check_tag_exists_ecr_found(mocker):
    from app.connectors.executors._registry_client import check_tag_exists
    mock_ecr = mocker.MagicMock()
    mock_ecr.describe_images.return_value = {"imageDetails": [{"imageDigest": "sha256:abc"}]}
    mocker.patch("boto3.client", return_value=mock_ecr)
    creds = {"account_id": "123", "region": "us-east-1",
             "access_key_id": "k", "secret_access_key": "s"}
    assert check_tag_exists("aws", creds, "myrepo", "v1") is True

def test_check_tag_exists_ecr_missing(mocker):
    from app.connectors.executors._registry_client import check_tag_exists
    mock_ecr = mocker.MagicMock()
    mock_ecr.describe_images.side_effect = mock_ecr.exceptions.ImageNotFoundException(
        {"Error": {"Code": "ImageNotFoundException", "Message": ""}}, "describe_images"
    )
    mocker.patch("boto3.client", return_value=mock_ecr)
    creds = {"account_id": "123", "region": "us-east-1",
             "access_key_id": "k", "secret_access_key": "s"}
    assert check_tag_exists("aws", creds, "myrepo", "v1") is False
```

Run: `pytest backend/tests/unit/test_container_image_transfer.py -k "tag_exists" -v`
Expected: 2 PASS

- [ ] **Step 7: Implement `delete_tag` and `restore_tag_by_digest`**

Add to `_registry_client.py`:

```python
def delete_tag(connector_type: str, creds: dict, repo: str, tag: str) -> None:
    """Delete repo:tag from the registry. No-op if tag doesn't exist."""
    if connector_type == "aws":
        import boto3
        ecr = boto3.client("ecr", region_name=creds["region"],
                           aws_access_key_id=creds["access_key_id"],
                           aws_secret_access_key=creds["secret_access_key"])
        ecr.batch_delete_image(repositoryName=repo, imageIds=[{"imageTag": tag}])
        return
    if connector_type in ("azure", "gcp", "oci"):
        import requests
        hostname = get_registry_hostname(connector_type, creds)
        token = get_auth_token(connector_type, creds, repo)
        auth_type = "Basic" if connector_type == "oci" else "Bearer"
        if connector_type == "oci":
            import base64
            namespace = creds["tenancy_namespace"]
            username = f"{namespace}/{creds['username']}"
            token = base64.b64encode(f"{username}:{creds['auth_token']}".encode()).decode()
        digest = get_manifest_digest(connector_type, creds, repo, tag)
        if digest is None:
            return
        r = requests.delete(f"https://{hostname}/v2/{repo}/manifests/{digest}",
                            headers={"Authorization": f"{auth_type} {token}"}, timeout=30)
        if r.status_code not in (200, 202, 404):
            r.raise_for_status()
        return
    raise ValueError(f"Unsupported connector_type: {connector_type}")


def restore_tag_by_digest(connector_type: str, creds: dict, repo: str, tag: str, digest: str) -> bool:
    """Re-point repo:tag to original digest. Returns False if manifest was GC'd."""
    if connector_type == "aws":
        import boto3
        ecr = boto3.client("ecr", region_name=creds["region"],
                           aws_access_key_id=creds["access_key_id"],
                           aws_secret_access_key=creds["secret_access_key"])
        try:
            resp = ecr.batch_get_image(repositoryName=repo, imageIds=[{"imageDigest": digest}])
            if not resp.get("images"):
                return False
            manifest = resp["images"][0]["imageManifest"]
            ecr.put_image(repositoryName=repo, imageManifest=manifest, imageTag=tag)
            return True
        except Exception:
            return False
    if connector_type in ("azure", "gcp", "oci"):
        import requests
        hostname = get_registry_hostname(connector_type, creds)
        token = get_auth_token(connector_type, creds, repo)
        auth_type = "Basic" if connector_type == "oci" else "Bearer"
        if connector_type == "oci":
            import base64
            namespace = creds["tenancy_namespace"]
            username = f"{namespace}/{creds['username']}"
            token = base64.b64encode(f"{username}:{creds['auth_token']}".encode()).decode()
        r = requests.get(f"https://{hostname}/v2/{repo}/manifests/{digest}",
                         headers={"Authorization": f"{auth_type} {token}",
                                  "Accept": "application/vnd.docker.distribution.manifest.v2+json"},
                         timeout=30)
        if r.status_code == 404:
            return False
        r.raise_for_status()
        manifest_body = r.content
        content_type = r.headers.get("Content-Type", "application/vnd.docker.distribution.manifest.v2+json")
        r2 = requests.put(f"https://{hostname}/v2/{repo}/manifests/{tag}",
                          headers={"Authorization": f"{auth_type} {token}",
                                   "Content-Type": content_type},
                          data=manifest_body, timeout=30)
        return r2.status_code in (200, 201)
    raise ValueError(f"Unsupported connector_type: {connector_type}")
```

- [ ] **Step 8: Run all registry client tests**

Run: `pytest backend/tests/unit/test_container_image_transfer.py -v`
Expected: all PASS (5+ tests)

- [ ] **Step 9: Commit**

```bash
git add backend/app/connectors/executors/_registry_client.py
git add backend/tests/unit/test_container_image_transfer.py
git commit -m "feat(image-transfer): add _registry_client helpers for ECR/ACR/GCR/OCIR"
```

---

## Task 3: Main executor (`container_image_transfer.py`)

**Files:**
- Create: `backend/app/connectors/executors/container_image_transfer.py`
- Modify: `backend/tests/unit/test_container_image_transfer.py`

**Interfaces:**
- Consumes: `_registry_client` (Task 2), `dispatch_agent_job` from `nexplane_agent.app_upgrade_base`, `ConnectorCredential` + `get_secret_backend()` for credential loading
- Produces: `execute(parameters, asset_ids, connector) -> dict` and `rollback(parameters, execution_result, connector) -> dict`
- Parameters shape:
  - `source_connector_id: str` — UUID of the source registry connector
  - `dest_connector_id: str` — UUID of the destination registry connector
  - `source_image: str` — e.g. `"myrepo/myapp:v1.2.3"` (must include tag)
  - `dest_repo: str` — destination repo path (tag taken from source)
  - `overwrite_existing: bool` — default `false`
- `asset_ids` — list of Nexplane agent host asset IDs (hosts with Docker); used only for the agent transfer path

- [ ] **Step 1: Write failing mock-path unit test**

Add to `backend/tests/unit/test_container_image_transfer.py`:

```python
import pytest

@pytest.mark.asyncio
async def test_execute_mock_returns_expected_shape():
    from app.connectors.executors.container_image_transfer import execute

    class MockConnector:
        credentials = {}

    result = await execute({
        "source_image": "myapp/api:v1.2.3",
        "dest_repo": "nexplane/myapp/api",
        "overwrite_existing": False,
    }, [], MockConnector())
    assert result["mock"] is True
    assert result["promote_to"] == "container_image_transfer"
    assert "rollback_data" in result
```

Run: `pytest backend/tests/unit/test_container_image_transfer.py::test_execute_mock_returns_expected_shape -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 2: Implement executor with mock path and credential loading helper**

```python
# backend/app/connectors/executors/container_image_transfer.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Cross-cloud container image transfer executor.

Transfers a container image from any of ECR/OCIR/ACR/GCR to any other.
ACR destinations use the ACR import API (server-side, no agent).
All other destinations use a Nexplane agent running docker pull/tag/push.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

ROLLBACK_CAPABILITY = "full"


def _run(fn):
    return asyncio.get_running_loop().run_in_executor(None, fn)


async def _load_creds_by_connector_id(connector_id: str) -> tuple[str, dict]:
    """Return (connector_type, credentials) for the given connector UUID."""
    from app.database import AsyncSessionLocal
    from app.models.connector import Connector
    from app.models.connector_credential import ConnectorCredential
    from app.services.secret_backend_factory import get_secret_backend
    from sqlalchemy import select

    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(Connector).where(Connector.id == connector_id)
        )).scalar_one_or_none()
        if row is None:
            raise ValueError(f"Connector {connector_id} not found")
        connector_type = row.connector_type.value

        cred_row = (await db.execute(
            select(ConnectorCredential).where(
                ConnectorCredential.connector_id == row.id
            ).limit(1)
        )).scalar_one_or_none()
        if cred_row is None or not cred_row.credentials_encrypted:
            raise ValueError(f"No credentials found for connector {connector_id}")

        backend = get_secret_backend()
        creds = backend.decrypt_json(cred_row.credentials_encrypted)
    return connector_type, creds


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    # Mock path — no real connector credentials
    primary_creds = getattr(connector, "credentials", None) or {}
    source_connector_id = parameters.get("source_connector_id")
    dest_connector_id = parameters.get("dest_connector_id")
    if not source_connector_id or not dest_connector_id:
        return {
            "mock": True,
            "phases": [{"phase": p, "status": "mock"} for p in
                       ["preflight", "snapshot", "transfer", "verify", "report"]],
            "summary": {
                "source_image": parameters.get("source_image"),
                "destination_image": None,
                "transfer_method": "mock",
                "digest_matched": True,
            },
            "promote_to": "container_image_transfer",
            "rollback_data": {"snapshot": {}, "dest_image": None, "dst_connector_type": None},
        }

    src_connector_type, src_creds = await _load_creds_by_connector_id(source_connector_id)
    dst_connector_type, dst_creds = await _load_creds_by_connector_id(dest_connector_id)

    source_image = parameters["source_image"]
    dest_repo = parameters["dest_repo"]
    overwrite_existing = parameters.get("overwrite_existing", False)

    phases = []
    rollback_data = {"snapshot": {}, "dest_image": None, "dst_connector_type": dst_connector_type}

    phase1 = await _preflight(src_connector_type, src_creds, source_image,
                               dst_connector_type, dst_creds, dest_repo, overwrite_existing)
    phases.append(phase1)
    if phase1["status"] == "failed":
        return {"phases": phases, "failed": True, "rollback_data": rollback_data}

    src_repo = phase1["src_repo"]
    src_tag = phase1["src_tag"]
    src_hostname = phase1["src_hostname"]
    dst_hostname = phase1["dst_hostname"]

    phase2 = await _snapshot(dst_connector_type, dst_creds, dest_repo, src_tag,
                              phase1["dest_tag_exists"])
    phases.append(phase2)
    rollback_data["snapshot"] = phase2["destination_state"]
    rollback_data["dest_image"] = f"{dest_repo}:{src_tag}"

    if dst_connector_type == "azure":
        phase3 = await _transfer_acr_import(src_connector_type, src_creds,
                                             src_hostname, src_repo, src_tag,
                                             dst_creds, dest_repo)
    else:
        phase3 = await _transfer_via_agent(asset_ids, src_connector_type, src_creds,
                                           src_hostname, src_repo, src_tag,
                                           dst_connector_type, dst_creds,
                                           dst_hostname, dest_repo)
    phases.append(phase3)

    phase4 = await _verify(dst_connector_type, dst_creds, dest_repo, src_tag,
                            src_connector_type, src_creds, src_repo)
    phases.append(phase4)

    src_full = f"{src_hostname}/{source_image}"
    dst_full = f"{dst_hostname}/{dest_repo}:{src_tag}"
    summary = {
        "source_image": src_full,
        "destination_image": dst_full,
        "transfer_method": phase3.get("method"),
        "digest_matched": phase4.get("digest_matched", False),
        "overwrote_existing": phase1["dest_tag_exists"],
    }
    phases.append({"phase": "report", "status": "ok", "summary": summary})

    return {
        "phases": phases,
        "summary": summary,
        "promote_to": "container_image_transfer",
        "rollback_data": rollback_data,
    }
```

- [ ] **Step 3: Run mock test**

Run: `pytest backend/tests/unit/test_container_image_transfer.py::test_execute_mock_returns_expected_shape -v`
Expected: PASS

- [ ] **Step 4: Implement `_preflight`**

Add to `container_image_transfer.py`:

```python
async def _preflight(src_connector_type, src_creds, source_image,
                     dst_connector_type, dst_creds, dest_repo, overwrite_existing) -> dict:
    try:
        def _do():
            from ._registry_client import check_tag_exists, get_registry_hostname
            if ":" not in source_image:
                raise ValueError(f"source_image must include tag: '{source_image}'")
            src_repo, src_tag = source_image.rsplit(":", 1)
            if not check_tag_exists(src_connector_type, src_creds, src_repo, src_tag):
                raise ValueError(f"Source image not found: {source_image}")
            dest_tag_exists = check_tag_exists(dst_connector_type, dst_creds, dest_repo, src_tag)
            if dest_tag_exists and not overwrite_existing:
                raise ValueError(
                    f"Destination tag '{dest_repo}:{src_tag}' already exists. "
                    "Set overwrite_existing=true to overwrite."
                )
            src_hostname = get_registry_hostname(src_connector_type, src_creds)
            dst_hostname = get_registry_hostname(dst_connector_type, dst_creds)
            return src_repo, src_tag, dest_tag_exists, src_hostname, dst_hostname

        src_repo, src_tag, dest_tag_exists, src_hostname, dst_hostname = await _run(_do)
        return {
            "phase": "preflight", "status": "ok",
            "src_repo": src_repo, "src_tag": src_tag,
            "dest_tag_exists": dest_tag_exists,
            "src_hostname": src_hostname, "dst_hostname": dst_hostname,
        }
    except Exception as e:
        return {"phase": "preflight", "status": "failed", "error": str(e)}
```

- [ ] **Step 5: Write and run preflight unit test**

Add to `backend/tests/unit/test_container_image_transfer.py`:

```python
@pytest.mark.asyncio
async def test_preflight_fails_when_source_missing(mocker):
    from app.connectors.executors.container_image_transfer import _preflight
    mocker.patch(
        "app.connectors.executors._registry_client.check_tag_exists",
        return_value=False,
    )
    mocker.patch(
        "app.connectors.executors._registry_client.get_registry_hostname",
        return_value="123.dkr.ecr.us-east-1.amazonaws.com",
    )
    result = await _preflight("aws", {}, "myrepo:v1",
                               "oci", {}, "dest/repo", False)
    assert result["status"] == "failed"
    assert "not found" in result["error"]

@pytest.mark.asyncio
async def test_preflight_fails_when_dest_exists_no_overwrite(mocker):
    from app.connectors.executors.container_image_transfer import _preflight
    mocker.patch(
        "app.connectors.executors._registry_client.check_tag_exists",
        side_effect=[True, True],
    )
    mocker.patch(
        "app.connectors.executors._registry_client.get_registry_hostname",
        return_value="host",
    )
    result = await _preflight("aws", {}, "myrepo:v1",
                               "oci", {}, "dest/repo", False)
    assert result["status"] == "failed"
    assert "overwrite_existing" in result["error"]
```

Run: `pytest backend/tests/unit/test_container_image_transfer.py -k "preflight" -v`
Expected: 2 PASS

- [ ] **Step 6: Implement `_snapshot`, `_verify`, `_transfer_acr_import`, `_transfer_via_agent`**

Add to `container_image_transfer.py`:

```python
async def _snapshot(dst_connector_type, dst_creds, dest_repo, src_tag, dest_tag_exists) -> dict:
    def _do():
        if not dest_tag_exists:
            return {"exists": False, "digest": None}
        from ._registry_client import get_manifest_digest
        digest = get_manifest_digest(dst_connector_type, dst_creds, dest_repo, src_tag)
        return {"exists": True, "digest": digest}
    snap = await _run(_do)
    return {"phase": "snapshot", "status": "ok", "destination_state": snap}


async def _transfer_acr_import(src_connector_type, src_creds, src_hostname,
                                src_repo, src_tag, dst_creds, dest_repo) -> dict:
    """Use ACR import API — Azure pulls the image server-side, no Docker daemon needed."""
    def _do():
        import requests
        from azure.identity import ClientSecretCredential
        from ._registry_client import get_auth_token

        registry_name = dst_creds["registry_name"]
        subscription_id = dst_creds["subscription_id"]
        resource_group = dst_creds["resource_group"]

        aad_creds = ClientSecretCredential(
            dst_creds["tenant_id"], dst_creds["client_id"], dst_creds["client_secret"]
        )
        mgmt_token = aad_creds.get_token("https://management.azure.com/.default").token
        src_token = get_auth_token(src_connector_type, src_creds, src_repo)

        # ECR token: Docker expects username "AWS"
        src_username = "AWS" if src_connector_type == "aws" else "oauth2accesstoken"
        body = {
            "source": {
                "registryUri": src_hostname,
                "sourceImage": f"{src_repo}:{src_tag}",
                "credentials": {"username": src_username, "password": src_token},
            },
            "targetTags": [f"{dest_repo}:{src_tag}"],
            "mode": "Force",
        }
        url = (f"https://management.azure.com/subscriptions/{subscription_id}"
               f"/resourceGroups/{resource_group}/providers/Microsoft.ContainerRegistry"
               f"/registries/{registry_name}/importImage?api-version=2019-05-01")
        r = requests.post(url, json=body,
                          headers={"Authorization": f"Bearer {mgmt_token}"}, timeout=60)
        if r.status_code == 200:
            return "acr_import"
        if r.status_code == 202:
            import time
            operation_url = r.headers.get("Location")
            for _ in range(60):  # 5 min max (60 × 5s)
                time.sleep(5)
                poll = requests.get(operation_url,
                                    headers={"Authorization": f"Bearer {mgmt_token}"}, timeout=30)
                if poll.status_code == 200:
                    return "acr_import"
                status = poll.json().get("status", "")
                if status == "Succeeded":
                    return "acr_import"
                if status == "Failed":
                    raise RuntimeError(f"ACR import failed: {poll.json()}")
            raise TimeoutError("ACR import timed out after 5 minutes")
        r.raise_for_status()

    method = await _run(_do)
    return {"phase": "transfer", "status": "ok", "method": method}


async def _transfer_via_agent(asset_ids, src_connector_type, src_creds,
                               src_hostname, src_repo, src_tag,
                               dst_connector_type, dst_creds,
                               dst_hostname, dest_repo) -> dict:
    """Send docker pull/tag/push to a Nexplane agent host via dispatch_agent_job."""
    from ._registry_client import get_auth_token

    src_token = get_auth_token(src_connector_type, src_creds, src_repo)
    dst_token = get_auth_token(dst_connector_type, dst_creds, dest_repo)

    src_full = f"{src_hostname}/{src_repo}:{src_tag}"
    dst_full = f"{dst_hostname}/{dest_repo}:{src_tag}"

    # Username conventions differ by registry:
    # ECR: "AWS"
    # GCR/Artifact Registry: "oauth2accesstoken"
    # OCIR: "<tenancy_namespace>/<username>"
    def _docker_user(ctype, creds_):
        if ctype == "aws":
            return "AWS"
        if ctype == "gcp":
            return "oauth2accesstoken"
        return f"{creds_.get('tenancy_namespace')}/{creds_.get('username')}"

    # For OCIR, docker login wants the raw auth_token (not base64-encoded)
    def _docker_pass(ctype, creds_, token_):
        if ctype == "oci":
            return creds_.get("auth_token", token_)
        return token_

    src_user = _docker_user(src_connector_type, src_creds)
    dst_user = _docker_user(dst_connector_type, dst_creds)
    src_pass = _docker_pass(src_connector_type, src_creds, src_token)
    dst_pass = _docker_pass(dst_connector_type, dst_creds, dst_token)

    script = (
        f"echo '{src_pass}' | docker login {src_hostname} -u {src_user} --password-stdin && "
        f"echo '{dst_pass}' | docker login {dst_hostname} -u {dst_user} --password-stdin && "
        f"docker pull {src_full} && "
        f"docker tag {src_full} {dst_full} && "
        f"docker push {dst_full} && "
        f"docker rmi {src_full} {dst_full} || true"
    )

    from app.connectors.executors.nexplane_agent.app_upgrade_base import dispatch_agent_job
    result = await dispatch_agent_job(
        command="run_command",
        parameters={"command": script, "timeout": 600},
        asset_ids=asset_ids,
        timeout_seconds=660,
    )
    if result.get("exit_code", 1) != 0:
        raise RuntimeError(f"Agent docker transfer failed: {result.get('stderr', '')[:500]}")
    return {"phase": "transfer", "status": "ok", "method": "agent_docker"}


async def _verify(dst_connector_type, dst_creds, dest_repo, src_tag,
                  src_connector_type, src_creds, src_repo) -> dict:
    def _do():
        from ._registry_client import get_manifest_digest
        dst_digest = get_manifest_digest(dst_connector_type, dst_creds, dest_repo, src_tag)
        if dst_digest is None:
            return False, None, None
        src_digest = get_manifest_digest(src_connector_type, src_creds, src_repo, src_tag)
        return True, src_digest, dst_digest

    tag_exists, src_digest, dst_digest = await _run(_do)
    digest_matched = tag_exists and src_digest == dst_digest
    return {
        "phase": "verify",
        "status": "ok" if tag_exists else "failed",
        "tag_exists_at_destination": tag_exists,
        "digest_matched": digest_matched,
        "source_digest": src_digest,
        "destination_digest": dst_digest,
    }
```

- [ ] **Step 7: Implement `rollback()`**

Add to `container_image_transfer.py`:

```python
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    rd = execution_result.get("rollback_data", {})
    snap = rd.get("snapshot", {})
    dest_image = rd.get("dest_image")
    dst_connector_type = rd.get("dst_connector_type")
    dest_connector_id = parameters.get("dest_connector_id")

    if not dest_image or not dest_connector_id:
        return {"rolled_back": True, "note": "nothing_to_undo"}

    _, dst_creds = await _load_creds_by_connector_id(dest_connector_id)
    dest_repo, dest_tag = dest_image.rsplit(":", 1)

    def _do():
        from ._registry_client import delete_tag, restore_tag_by_digest
        if not snap.get("exists"):
            delete_tag(dst_connector_type, dst_creds, dest_repo, dest_tag)
            return True, "deleted_net_new_tag"
        original_digest = snap["digest"]
        restored = restore_tag_by_digest(dst_connector_type, dst_creds,
                                          dest_repo, dest_tag, original_digest)
        if restored:
            return True, "restored_original_digest"
        try:
            delete_tag(dst_connector_type, dst_creds, dest_repo, dest_tag)
        except Exception:
            pass
        return False, "partial_original_manifest_gc_deleted"

    try:
        ok, note = await _run(_do)
        return {"rolled_back": ok, "note": note,
                "rolled_back_image": dest_image, "partial": not ok}
    except Exception as e:
        logger.error("Image transfer rollback failed: %s", e)
        return {"rolled_back": False, "error": str(e)}
```

- [ ] **Step 8: Write rollback unit tests**

Add to `backend/tests/unit/test_container_image_transfer.py`:

```python
@pytest.mark.asyncio
async def test_rollback_net_new_tag(mocker):
    from app.connectors.executors.container_image_transfer import rollback
    mocker.patch(
        "app.connectors.executors.container_image_transfer._load_creds_by_connector_id",
        return_value=("oci", {"tenancy_namespace": "ns", "username": "u", "auth_token": "t",
                               "region": "us-ashburn-1"}),
    )
    mock_delete = mocker.patch("app.connectors.executors._registry_client.delete_tag")
    execution_result = {
        "rollback_data": {
            "snapshot": {"exists": False, "digest": None},
            "dest_image": "nexplane/smoke/alpine:3.19",
            "dst_connector_type": "oci",
        }
    }
    result = await rollback(
        {"dest_connector_id": "some-uuid"},
        execution_result,
        None,
    )
    assert result["rolled_back"] is True
    assert result["note"] == "deleted_net_new_tag"
    mock_delete.assert_called_once()

@pytest.mark.asyncio
async def test_rollback_restore_original_digest(mocker):
    from app.connectors.executors.container_image_transfer import rollback
    mocker.patch(
        "app.connectors.executors.container_image_transfer._load_creds_by_connector_id",
        return_value=("oci", {}),
    )
    mocker.patch("app.connectors.executors._registry_client.restore_tag_by_digest", return_value=True)
    execution_result = {
        "rollback_data": {
            "snapshot": {"exists": True, "digest": "sha256:abc"},
            "dest_image": "nexplane/smoke/alpine:3.19",
            "dst_connector_type": "oci",
        }
    }
    result = await rollback({"dest_connector_id": "some-uuid"}, execution_result, None)
    assert result["rolled_back"] is True
    assert result["note"] == "restored_original_digest"
```

- [ ] **Step 9: Run all unit tests**

Run: `pytest backend/tests/unit/test_container_image_transfer.py -v`
Expected: all PASS (10+ tests)

- [ ] **Step 10: Commit**

```bash
git add backend/app/connectors/executors/container_image_transfer.py
git add backend/tests/unit/test_container_image_transfer.py
git commit -m "feat(image-transfer): implement container_image_transfer executor with rollback"
```

---

## Task 4: Catalog entry + executor dispatch wiring

**Background:** The platform has two separate catalog mechanisms:
1. `backend/app/connectors/catalog/*.json` — the **ActionCatalogService** catalog; each file has `connector_type` + `actions[]` with `executor` refs like `"aws.ecr_replication"`. This is used by the rollback executor to auto-discover `rollback()` modules.
2. `backend/app/change_type_definitions/*.json` — per-CR-type metadata for the planning UI. **Not** used for rollback routing.

For `container_image_transfer`, we need:
- A catalog action entry so `rollback_executor.py → catalog_service.get_executor()` can find `rollback()`
- The executor living at `backend/app/connectors/executors/container_registry/container_image_transfer.py` (a thin re-export of the top-level executor from Task 3)
- `"container_registry"` added to `rollback_executor.py`'s `_conn_types_to_try` list
- An `elif` in `activities.py` for the execute path

**Files:**
- Create: `backend/app/connectors/catalog/container_registry.json`
- Create: `backend/app/connectors/executors/container_registry/__init__.py` (empty)
- Create: `backend/app/connectors/executors/container_registry/container_image_transfer.py` (re-export shim)
- Modify: `backend/app/services/rollback_executor.py` — add `"container_registry"` to `_conn_types_to_try`
- Modify: `backend/app/workflows/activities.py` — add execute dispatch

**Interfaces:**
- Produces: catalog routing for rollback + activities dispatch for execute

- [ ] **Step 2: Write failing catalog test**

Add to `backend/tests/unit/test_container_image_transfer.py`:

```python
def test_catalog_entry_loads():
    import json, pathlib
    p = pathlib.Path(__file__).parent.parent.parent / "app" / "connectors" / "catalog" / "container_registry.json"
    assert p.exists(), f"Catalog file not found at {p}"
    data = json.loads(p.read_text())
    assert data["connector_type"] == "container_registry"
    actions = {a["action_id"]: a for a in data.get("actions", [])}
    assert "container_image_transfer" in actions
    assert actions["container_image_transfer"]["executor"] == "container_registry.container_image_transfer"
```

Run: `pytest backend/tests/unit/test_container_image_transfer.py::test_catalog_entry_loads -v`
Expected: FAIL — `AssertionError: Catalog file not found`

- [ ] **Step 3: Create the catalog JSON and re-export shim**

Create `backend/app/connectors/catalog/container_registry.json`:

```json
{
  "connector_type": "container_registry",
  "actions": [
    {
      "action_id": "container_image_transfer",
      "display_name": "Container Image Transfer",
      "description": "Transfer a container image from one cloud registry to another (ECR/OCIR/ACR/GCR) with full rollback.",
      "executor": "container_registry.container_image_transfer",
      "execution_tier": 1,
      "rollback_action": "container_image_transfer",
      "rollback_connector_type": "container_registry",
      "rollback_capability": "full",
      "read_only": false
    }
  ]
}
```

Create `backend/app/connectors/executors/container_registry/__init__.py` (empty file).

Create `backend/app/connectors/executors/container_registry/container_image_transfer.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Re-export shim so catalog_service finds this executor at container_registry.container_image_transfer."""
from app.connectors.executors.container_image_transfer import execute, rollback, ROLLBACK_CAPABILITY  # noqa: F401
```

- [ ] **Step 4: Run catalog test**

Run: `pytest backend/tests/unit/test_container_image_transfer.py::test_catalog_entry_loads -v`
Expected: PASS

- [ ] **Step 5: Add `"container_registry"` to rollback executor's type list**

In `backend/app/services/rollback_executor.py`, find (around line 134–139):

```python
        _conn_types_to_try.extend(
            t for t in ("active_directory", "nexplane_agent", "aws", "azure_ad", "okta")
            if t != _connector_type_from_result
        )
```

Change it to:

```python
        _conn_types_to_try.extend(
            t for t in ("container_registry", "active_directory", "nexplane_agent", "aws", "azure_ad", "okta")
            if t != _connector_type_from_result
        )
```

- [ ] **Step 6: Wire execute dispatch in `activities.py`**

In `backend/app/workflows/activities.py`, find (around line 196–201):

```python
        elif _cr and _cr.change_type.value == "k8s_cluster_upgrade":
            from app.services.k8s_cluster_upgrade_executor import execute_k8s_cluster_upgrade
            _result = await execute_k8s_cluster_upgrade(_cr.id)
            return _result

    step_results = []
```

Insert the new elif **before** `step_results = []` and **after** `k8s_cluster_upgrade`:

```python
        elif _cr and _cr.change_type.value == "container_image_transfer":
            from app.connectors.executors.container_image_transfer import execute as _transfer_execute
            _result = await _transfer_execute(
                parameters=_cr.desired_outcome or {},
                asset_ids=[str(a) for a in (_cr.target_asset_ids or [])],
                connector=None,
            )
            return _result

    step_results = []
```

Note: `connector=None` is correct — the executor loads credentials itself via `_load_creds_by_connector_id()`.

- [ ] **Step 7: Run all unit tests**

Run: `pytest backend/tests/unit/test_container_image_transfer.py -v`
Expected: all PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/catalog/container_registry.json
git add backend/app/connectors/executors/container_registry/
git add backend/app/workflows/activities.py
git add backend/app/services/rollback_executor.py
git add backend/tests/unit/test_container_image_transfer.py
git commit -m "feat(image-transfer): add catalog entry and executor dispatch routing"
```

---

## Task 5: Smoke test

**Files:**
- Create: `backend/tests/smoke/test_smoke_container_image_transfer.py`

**Interfaces:**
- Consumes: live ECR connector, live OCIR connector, live ACR connector, and a Nexplane agent host asset (with Docker) registered in the platform
- Produces: `AGENT_TRANSFER` and `ACR_IMPORT` smoke phases
- Pre-requisite: `alpine:3.19` must be present in the source ECR repo (or any small image ~5 MB). Push it manually once: `docker pull alpine:3.19 && docker tag alpine:3.19 <ecr-host>/nexplane-smoke/alpine:3.19 && docker push <ecr-host>/nexplane-smoke/alpine:3.19`

- [ ] **Step 1: Create the smoke test file with helpers**

```python
# backend/tests/smoke/test_smoke_container_image_transfer.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Smoke test: Cross-Cloud Container Image Transfer

Two phases:
  AGENT_TRANSFER — ECR → OCIR via Nexplane agent docker pull/tag/push
  ACR_IMPORT     — ECR → ACR via Azure ACR import API (server-side)

Run on EC2 inside nexplane-backend-1:
    API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_container_image_transfer.py -v -s

Prerequisites:
  1. API_TOKEN set to a valid nxp_... admin token.
  2. AWS (ECR), OCI (OCIR), Azure (ACR) connectors registered in the platform.
     The test discovers connector IDs via GET /connectors.
  3. alpine:3.19 pushed to the source ECR repo: nexplane-smoke/alpine:3.19
  4. A Nexplane agent host asset with Docker installed registered in the platform.
     The test discovers it via GET /assets?asset_type=host.
"""

import asyncio
import hashlib
import os

import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_BASE_URL = "http://localhost:8000"
_SOURCE_IMAGE = "nexplane-smoke/alpine:3.19"
_DEST_REPO = "nexplane-smoke/alpine-xfer"


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set — skipping image transfer smoke")
    return val


async def _get_jwt(api_token: str) -> str:
    from app.database import AsyncSessionLocal
    from app.models.api_token import ApiToken
    from app.services.auth_service import create_access_token
    from sqlalchemy import select

    token_hash = hashlib.sha256(api_token.encode()).hexdigest()
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(ApiToken).where(
                ApiToken.token_hash == token_hash,
                ApiToken.revoked == False,  # noqa: E712
            )
        )
        tok = r.scalar_one()
        return create_access_token(subject=str(tok.user_id))


async def _find_connector_id(jwt: str, connector_type: str) -> str:
    """Discover connector UUID by type via GET /connectors."""
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.get(
            "/connectors",
            params={"connector_type": connector_type},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"GET /connectors failed: {r.text}"
        items = r.json()
    matching = [c for c in items if c.get("connector_type") == connector_type]
    assert matching, f"No connector of type '{connector_type}' found. Register one first."
    return str(matching[0]["id"])


async def _find_agent_asset_id(jwt: str) -> str:
    """Discover a host asset that has Docker (any registered Nexplane agent host)."""
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.get(
            "/assets",
            params={"asset_type": "host"},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert r.status_code == 200, f"GET /assets failed: {r.text}"
        assets = r.json()
    assert assets, "No host assets found. Register a Nexplane agent host with Docker."
    return str(assets[0]["id"])


async def _plan_and_approve_cr(jwt: str, cr_id: str) -> None:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=60) as client:
        headers = {"Authorization": f"Bearer {jwt}"}
        r = await client.post(f"/change-requests/{cr_id}/plan", headers=headers)
        assert r.status_code == 200, f"POST /plan failed: {r.text}"
        r = await client.post(f"/change-requests/{cr_id}/submit-for-approval", headers=headers)
        assert r.status_code == 200, f"POST /submit-for-approval failed: {r.text}"
        r = await client.post(f"/change-requests/{cr_id}/approve",
                               json={"decision": "approved", "comment": "image-transfer smoke self-approval"},
                               headers=headers)
        assert r.status_code == 200, f"POST /approve failed: {r.text}"


async def _rollback_cr(jwt: str, cr_id: str, timeout: int = 180) -> dict:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post(f"/change-requests/{cr_id}/rollback",
                               headers={"Authorization": f"Bearer {jwt}"})
        assert r.status_code == 200, f"POST /rollback failed: {r.text}"

    interval = 5
    for _ in range(timeout // interval):
        await asyncio.sleep(interval)
        jwt = await _get_jwt(_env("API_TOKEN"))
        async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
            r = await client.get(f"/change-requests/{cr_id}",
                                  headers={"Authorization": f"Bearer {jwt}"})
            detail = r.json()
        status = detail.get("status")
        if status in ("rolled_back", "rollback_failed"):
            runs = detail.get("execution_runs", [])
            rollback_run = next(
                (r for r in reversed(runs) if "rollback" in (r.get("workflow_id") or "")),
                runs[-1] if runs else None,
            )
            raw = rollback_run.get("result", {}) if rollback_run else {}
            steps = raw.get("execution", {}).get("steps", [])
            return steps[0]["result"] if steps else raw
    pytest.fail(f"Rollback for CR {cr_id} timed out after {timeout}s")


async def _create_and_execute_transfer_cr(
    token: str,
    source_connector_type: str,
    dest_connector_type: str,
    dest_repo: str,
    timeout: int = 300,
) -> dict:
    from app.mcp_tools.change_requests import create_change_request, execute_change_request

    jwt = await _get_jwt(token)
    src_connector_id = await _find_connector_id(jwt, source_connector_type)
    dst_connector_id = await _find_connector_id(jwt, dest_connector_type)
    agent_asset_id = await _find_agent_asset_id(jwt)

    cr = await create_change_request(
        token=token,
        change_type="container_image_transfer",
        asset_id=agent_asset_id,
        title=f"Smoke: {source_connector_type}→{dest_connector_type} image transfer",
        parameters={
            "source_connector_id": src_connector_id,
            "dest_connector_id": dst_connector_id,
            "source_image": _SOURCE_IMAGE,
            "dest_repo": dest_repo,
            "overwrite_existing": False,
        },
    )
    assert "id" in cr, f"create_change_request failed: {cr}"
    cr_id = cr["id"]

    await _plan_and_approve_cr(jwt=jwt, cr_id=cr_id)

    executed = await execute_change_request(token=token, cr_id=cr_id)
    assert "error" not in executed, f"execute_change_request failed: {executed}"

    interval = 10
    jwt = await _get_jwt(token)
    for _ in range(timeout // interval):
        await asyncio.sleep(interval)
        async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
            r = await client.get(f"/change-requests/{cr_id}",
                                  headers={"Authorization": f"Bearer {jwt}"})
            assert r.status_code == 200
            detail = r.json()
        status = detail.get("status")
        if status == "completed":
            runs = detail.get("execution_runs", [])
            if runs:
                latest = max(runs, key=lambda x: x.get("started_at") or "")
                raw = latest.get("result", {})
                steps = raw.get("execution", {}).get("steps", [])
                detail["execution_result"] = steps[0]["result"] if steps else raw
            else:
                detail["execution_result"] = {}
            return detail
        if status in ("failed", "rollback_failed"):
            runs = detail.get("execution_runs", [])
            latest_result = runs[-1].get("result", {}) if runs else {}
            pytest.fail(f"CR {cr_id} reached terminal failure: {detail} | exec: {latest_result}")
    pytest.fail(f"CR {cr_id} timed out after {timeout}s")
```

- [ ] **Step 2: Write AGENT_TRANSFER phase test**

Add to `backend/tests/smoke/test_smoke_container_image_transfer.py`:

```python
@pytest.mark.smoke
@pytest.mark.smoke_phase("AGENT_TRANSFER")
async def test_agent_transfer_ecr_to_ocir():
    """Transfer alpine:3.19 from ECR to OCIR via agent docker pull/tag/push. Verify digest + rollback."""
    token = _env("API_TOKEN")

    detail = await _create_and_execute_transfer_cr(
        token=token,
        source_connector_type="aws",
        dest_connector_type="oci",
        dest_repo=_DEST_REPO,
    )
    assert detail["status"] == "completed", f"CR not completed: {detail.get('status')}"
    exec_result = detail.get("execution_result", {})
    summary = exec_result.get("summary", {})

    assert summary.get("digest_matched") is True, f"Digest mismatch after agent transfer: {summary}"
    assert summary.get("transfer_method") == "agent_docker", f"Wrong transfer method: {summary}"
    assert summary.get("overwrote_existing") is False

    jwt = await _get_jwt(token)
    rb = await _rollback_cr(jwt=jwt, cr_id=detail["id"])
    assert rb.get("rolled_back") is True, f"Rollback failed: {rb}"
    assert rb.get("note") == "deleted_net_new_tag", f"Unexpected rollback note: {rb}"
```

- [ ] **Step 3: Write ACR_IMPORT phase test**

Add to `backend/tests/smoke/test_smoke_container_image_transfer.py`:

```python
@pytest.mark.smoke
@pytest.mark.smoke_phase("ACR_IMPORT")
async def test_acr_import_ecr_to_acr():
    """Transfer alpine:3.19 from ECR to ACR via ACR import API. Verify digest + rollback."""
    token = _env("API_TOKEN")

    detail = await _create_and_execute_transfer_cr(
        token=token,
        source_connector_type="aws",
        dest_connector_type="azure",
        dest_repo=_DEST_REPO,
    )
    assert detail["status"] == "completed", f"CR not completed: {detail.get('status')}"
    exec_result = detail.get("execution_result", {})
    summary = exec_result.get("summary", {})

    assert summary.get("digest_matched") is True, f"Digest mismatch after ACR import: {summary}"
    assert summary.get("transfer_method") == "acr_import", f"Wrong transfer method: {summary}"
    assert summary.get("overwrote_existing") is False

    jwt = await _get_jwt(token)
    rb = await _rollback_cr(jwt=jwt, cr_id=detail["id"])
    assert rb.get("rolled_back") is True, f"Rollback failed: {rb}"
    assert rb.get("note") == "deleted_net_new_tag", f"Unexpected rollback note: {rb}"
```

- [ ] **Step 4: Push alpine:3.19 to ECR smoke repo (one-time setup on EC2)**

```bash
# Run on EC2 — substitute <account-id> and <region> for your ECR
aws ecr get-login-password --region <region> | \
  docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com

# Create repo if needed
aws ecr create-repository --repository-name nexplane-smoke/alpine --region <region> || true

docker pull alpine:3.19
docker tag alpine:3.19 <account-id>.dkr.ecr.<region>.amazonaws.com/nexplane-smoke/alpine:3.19
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/nexplane-smoke/alpine:3.19
```

- [ ] **Step 5: Create destination repos in OCIR and ACR (one-time setup)**

OCIR repos are created automatically on first push; no pre-creation needed.

For ACR: the import API creates the repo automatically if it doesn't exist when `mode: "Force"` is specified.

- [ ] **Step 6: Run smoke tests on EC2**

```bash
docker exec -e "API_TOKEN=$(cat /run/secrets/api_token)" nexplane-backend-1 \
  python -m pytest tests/smoke/test_smoke_container_image_transfer.py -v -s
```

Expected: `2 passed` (AGENT_TRANSFER and ACR_IMPORT)

- [ ] **Step 7: Commit**

```bash
git add backend/tests/smoke/test_smoke_container_image_transfer.py
git commit -m "feat(image-transfer): add AGENT_TRANSFER and ACR_IMPORT smoke tests"
```
