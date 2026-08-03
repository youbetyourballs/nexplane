# Cross-Cloud Container Image Transfer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single `container_image_transfer` CR that pulls a container image from any of ECR/OCIR/ACR/GCR and pushes it to any other, with full rollback.

**Architecture:** One cloud-agnostic executor inspects the destination connector type and routes to one of two transfer paths: ACR import API (server-side, no agent) or Nexplane agent Docker pull/tag/push (all other destination clouds). Credentials are resolved from registered connectors at runtime and passed per-command; the agent never stores them.

**Tech Stack:** Python asyncio executor pattern, existing `_client.py` per-cloud credential factories, Nexplane agent command protocol (existing), `docker` CLI on agent host, Azure ACR REST import API (`azure-mgmt-containerregistry` or direct REST), ECR boto3, GCR/Artifact Registry OCI Distribution spec REST, OCIR artifacts SDK.

## Global Constraints

- `ROLLBACK_CAPABILITY = "full"` with one caveat: if the destination registry GC policy deleted the untagged original manifest between transfer and rollback, degrade gracefully to `rolled_back: "partial"` with explanation.
- Executor follows the standard 5-phase pattern: preflight → snapshot → transfer → verify → report.
- Credentials are never stored by the agent; passed per-command and discarded.
- `overwrite_existing` defaults to `false`; preflight fails explicitly when destination tag exists and flag is not set.
- Tag is always preserved from source; only the destination repo path is remappable.
- Smoke test must cover both transfer paths: agent (ECR→OCIR) and ACR import (ECR→ACR).
- GCR covered by unit tests only (shares agent path code with ECR/OCIR).
- All new code lives under `backend/app/connectors/executors/` following existing patterns.
- New ChangeType enum value and Alembic migration required before executor.

---

## File Structure

- Create: `backend/app/connectors/executors/container_image_transfer.py` — main executor (cloud-agnostic, routes to ACR import or agent path)
- Create: `backend/app/connectors/executors/_registry_client.py` — per-cloud registry helpers: resolve hostname, get auth token, check tag exists, get manifest digest, delete tag, restore tag by digest
- Modify: `backend/app/models/change_request.py` — add `container_image_transfer` to ChangeType enum
- Create: `backend/alembic/versions/<hash>_add_container_image_transfer_change_type.py` — Alembic migration
- Modify: `backend/app/change_type_definitions/catalog.json` (or equivalent catalog file) — add catalog entry
- Create: `backend/tests/unit/test_container_image_transfer.py` — unit tests (mock registry calls, both transfer paths, rollback cases)
- Create: `backend/tests/smoke/test_smoke_container_image_transfer.py` — smoke test (AGENT_TRANSFER and ACR_IMPORT phases)

---

## Task 1: ChangeType enum + Alembic migration

**Files:**
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/<hash>_add_container_image_transfer_change_type.py`

**Interfaces:**
- Produces: `ChangeType.container_image_transfer` enum value consumed by Tasks 2 and 5

- [ ] **Step 1: Locate the ChangeType enum**

Run: `grep -n "container_image\|ChangeType" backend/app/models/change_request.py | head -20`

- [ ] **Step 2: Add the new enum value**

Add `container_image_transfer = "container_image_transfer"` to the ChangeType enum, alphabetically among other container/cloud entries.

- [ ] **Step 3: Generate Alembic migration**

```bash
cd backend
alembic revision --autogenerate -m "add_container_image_transfer_change_type"
```

Verify the generated migration updates the enum type (PostgreSQL `ALTER TYPE ... ADD VALUE`).

- [ ] **Step 4: Apply migration locally (on EC2)**

```bash
alembic upgrade head
```

Expected: `Running upgrade ... -> <hash>`

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/change_request.py backend/alembic/versions/
git commit -m "feat(image-transfer): add container_image_transfer ChangeType enum + migration"
```

---

## Task 2: Registry client helpers (`_registry_client.py`)

**Files:**
- Create: `backend/app/connectors/executors/_registry_client.py`

**Interfaces:**
- Consumes: connector credentials dict (from `_attach_credentials`)
- Produces:
  - `get_registry_hostname(connector_type, creds) -> str`
  - `get_auth_token(connector_type, creds) -> str` — short-lived Docker auth token
  - `check_tag_exists(connector_type, creds, repo, tag) -> bool`
  - `get_manifest_digest(connector_type, creds, repo, tag) -> str | None` — `"sha256:..."` or None
  - `delete_tag(connector_type, creds, repo, tag) -> None`
  - `restore_tag_by_digest(connector_type, creds, repo, tag, digest) -> bool` — returns False if manifest GC'd

- [ ] **Step 1: Write failing tests for get_registry_hostname**

```python
# backend/tests/unit/test_container_image_transfer.py
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

Run: `pytest backend/tests/unit/test_container_image_transfer.py::test_get_registry_hostname_ecr -v`
Expected: FAIL — `ImportError`

- [ ] **Step 2: Implement `get_registry_hostname`**

```python
# backend/app/connectors/executors/_registry_client.py

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

- [ ] **Step 4: Write and implement `check_tag_exists` + `get_manifest_digest`**

```python
# Tests
def test_check_tag_exists_ecr_found(mocker):
    mock_boto = mocker.patch("boto3.client")
    mock_boto.return_value.describe_images.return_value = {"imageDetails": [{"imageDigest": "sha256:abc"}]}
    assert check_tag_exists("aws", {"account_id": "123", "region": "us-east-1", "access_key_id": "k", "secret_access_key": "s"}, "myrepo", "v1") is True

def test_check_tag_exists_ecr_missing(mocker):
    import botocore.exceptions
    mock_boto = mocker.patch("boto3.client")
    mock_boto.return_value.describe_images.side_effect = mock_boto.return_value.exceptions.ImageNotFoundException(
        {"Error": {"Code": "ImageNotFoundException"}}, "describe_images"
    )
    assert check_tag_exists("aws", {"account_id": "123", "region": "us-east-1", "access_key_id": "k", "secret_access_key": "s"}, "myrepo", "v1") is False
```

Implementation — ECR:
```python
def check_tag_exists(connector_type: str, creds: dict, repo: str, tag: str) -> bool:
    return get_manifest_digest(connector_type, creds, repo, tag) is not None

def get_manifest_digest(connector_type: str, creds: dict, repo: str, tag: str) -> str | None:
    if connector_type == "aws":
        import boto3, botocore.exceptions
        ecr = boto3.client("ecr", region_name=creds["region"],
                           aws_access_key_id=creds["access_key_id"],
                           aws_secret_access_key=creds["secret_access_key"])
        try:
            resp = ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": tag}])
            return resp["imageDetails"][0]["imageDigest"]
        except ecr.exceptions.ImageNotFoundException:
            return None
    if connector_type == "azure":
        import requests
        token = _get_acr_token(creds, repo)
        hostname = get_registry_hostname("azure", creds)
        r = requests.get(f"https://{hostname}/v2/{repo}/manifests/{tag}",
                         headers={"Authorization": f"Bearer {token}",
                                  "Accept": "application/vnd.docker.distribution.manifest.v2+json"},
                         timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.headers.get("Docker-Content-Digest")
    if connector_type == "gcp":
        import requests
        token = _get_gcr_token(creds)
        hostname = get_registry_hostname("gcp", creds)
        r = requests.get(f"https://{hostname}/{repo}/manifests/{tag}",
                         headers={"Authorization": f"Bearer {token}",
                                  "Accept": "application/vnd.docker.distribution.manifest.v2+json"},
                         timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.headers.get("Docker-Content-Digest")
    if connector_type == "oci":
        return _get_ocir_digest(creds, repo, tag)
    raise ValueError(f"Unsupported connector_type: {connector_type}")
```

- [ ] **Step 5: Implement auth token helpers**

```python
def _get_acr_token(creds: dict, repo: str) -> str:
    """Exchange AAD token for ACR refresh token, then scope token for the repo."""
    import requests
    from azure.identity import ClientSecretCredential
    aad_token = ClientSecretCredential(
        creds["tenant_id"], creds["client_id"], creds["client_secret"]
    ).get_token("https://management.azure.com/.default").token
    hostname = get_registry_hostname("azure", creds)
    # Exchange for ACR refresh token
    r = requests.post(f"https://{hostname}/oauth2/exchange",
                      data={"grant_type": "access_token", "service": hostname,
                            "access_token": aad_token}, timeout=30)
    r.raise_for_status()
    refresh_token = r.json()["refresh_token"]
    # Exchange for scoped access token
    r2 = requests.post(f"https://{hostname}/oauth2/token",
                       data={"grant_type": "refresh_token", "service": hostname,
                             "scope": f"repository:{repo}:pull,push",
                             "refresh_token": refresh_token}, timeout=30)
    r2.raise_for_status()
    return r2.json()["access_token"]

def get_auth_token(connector_type: str, creds: dict, repo: str = "") -> str:
    """Return a short-lived Docker-compatible bearer token for the given registry."""
    if connector_type == "aws":
        import boto3, base64
        ecr = boto3.client("ecr", region_name=creds["region"],
                           aws_access_key_id=creds["access_key_id"],
                           aws_secret_access_key=creds["secret_access_key"])
        token_b64 = ecr.get_authorization_token()["authorizationData"][0]["authorizationToken"]
        # ECR returns base64("AWS:password") — Docker expects just the password
        return base64.b64decode(token_b64).decode().split(":", 1)[1]
    if connector_type == "azure":
        return _get_acr_token(creds, repo)
    if connector_type == "gcp":
        return _get_gcr_token(creds)
    if connector_type == "oci":
        return _get_ocir_token(creds)
    raise ValueError(f"Unsupported connector_type: {connector_type}")

def _get_gcr_token(creds: dict) -> str:
    import json, tempfile, os
    from google.oauth2 import service_account
    key_json = creds.get("service_account_key_json", {})
    if isinstance(key_json, str):
        import json as _json
        key_json = _json.loads(key_json)
    sa_creds = service_account.Credentials.from_service_account_info(
        key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    import google.auth.transport.requests
    sa_creds.refresh(google.auth.transport.requests.Request())
    return sa_creds.token

def _get_ocir_token(creds: dict) -> str:
    import base64
    from ._client import get_oci_config
    cfg = get_oci_config(creds)
    namespace = creds["tenancy_namespace"]
    # OCIR uses "<username>/<tenancy-namespace>/<username>" + auth token
    username = f"{namespace}/{creds['username']}"
    password = creds["auth_token"]
    return base64.b64encode(f"{username}:{password}".encode()).decode()

def _get_ocir_digest(creds: dict, repo: str, tag: str) -> str | None:
    import requests, base64
    namespace = creds["tenancy_namespace"]
    region_prefix = OCIR_REGION_MAP.get(creds["region"], creds["region"].split("-")[0])
    hostname = f"{region_prefix}.ocir.io"
    username = f"{namespace}/{creds['username']}"
    token = base64.b64encode(f"{username}:{creds['auth_token']}".encode()).decode()
    r = requests.get(f"https://{hostname}/v2/{namespace}/{repo}/manifests/{tag}",
                     headers={"Authorization": f"Basic {token}",
                              "Accept": "application/vnd.docker.distribution.manifest.v2+json"},
                     timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.headers.get("Docker-Content-Digest")
```

- [ ] **Step 6: Implement `delete_tag` and `restore_tag_by_digest`**

```python
def delete_tag(connector_type: str, creds: dict, repo: str, tag: str) -> None:
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
        # Must delete by digest for OCI Distribution spec
        digest = get_manifest_digest(connector_type, creds, repo, tag)
        if digest is None:
            return  # already gone
        r = requests.delete(f"https://{hostname}/v2/{repo}/manifests/{digest}",
                            headers={"Authorization": f"{auth_type} {token}"}, timeout=30)
        if r.status_code not in (200, 202, 404):
            r.raise_for_status()
        return
    raise ValueError(f"Unsupported connector_type: {connector_type}")

def restore_tag_by_digest(connector_type: str, creds: dict, repo: str, tag: str, digest: str) -> bool:
    """Re-point tag to original digest. Returns False if manifest was GC'd (404)."""
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
        # Fetch manifest by digest
        r = requests.get(f"https://{hostname}/v2/{repo}/manifests/{digest}",
                         headers={"Authorization": f"{auth_type} {token}",
                                  "Accept": "application/vnd.docker.distribution.manifest.v2+json"},
                         timeout=30)
        if r.status_code == 404:
            return False
        r.raise_for_status()
        manifest_body = r.content
        content_type = r.headers.get("Content-Type", "application/vnd.docker.distribution.manifest.v2+json")
        # Re-point tag to original manifest
        r2 = requests.put(f"https://{hostname}/v2/{repo}/manifests/{tag}",
                          headers={"Authorization": f"{auth_type} {token}",
                                   "Content-Type": content_type},
                          data=manifest_body, timeout=30)
        if r2.status_code not in (200, 201):
            return False
        return True
    raise ValueError(f"Unsupported connector_type: {connector_type}")
```

- [ ] **Step 7: Run all unit tests for _registry_client**

```bash
pytest backend/tests/unit/test_container_image_transfer.py -v
```
Expected: all registry client tests PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/executors/_registry_client.py backend/tests/unit/test_container_image_transfer.py
git commit -m "feat(image-transfer): add _registry_client helpers for ECR/ACR/GCR/OCIR"
```

---

## Task 3: Main executor (`container_image_transfer.py`)

**Files:**
- Create: `backend/app/connectors/executors/container_image_transfer.py`

**Interfaces:**
- Consumes: `_registry_client` (Task 2), existing agent command protocol
- Produces: `execute()` + `rollback()` conforming to standard executor contract

- [ ] **Step 1: Write failing smoke-level unit test**

```python
# backend/tests/unit/test_container_image_transfer.py
async def test_execute_mock_returns_expected_shape(mocker):
    """Executor with no real credentials returns mock result."""
    from app.connectors.executors.container_image_transfer import execute
    result = await execute({
        "source_image": "myapp/api:v1.2.3",
        "dest_repo": "nexplane/myapp/api",
        "overwrite_existing": False,
    }, [], MockConnector(credentials={}))
    assert result["mock"] is True
    assert result["promote_to"] == "container_image_transfer"
```

Run: `pytest backend/tests/unit/test_container_image_transfer.py::test_execute_mock_returns_expected_shape -v`
Expected: FAIL — ImportError

- [ ] **Step 2: Implement executor skeleton with mock path**

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


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    src_creds = getattr(connector, "source_credentials", {})
    dst_creds = getattr(connector, "dest_credentials", {})
    if not src_creds or not dst_creds:
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
            "rollback_data": {"snapshot": {}, "dest_image": None},
        }
    # Phases wired up in Step 8 once all phase functions are implemented
    raise NotImplementedError("Wire phases in Step 8")
```

- [ ] **Step 3: Implement preflight phase**

```python
async def _preflight(src_connector_type: str, src_creds: dict, source_image: str,
                     dst_connector_type: str, dst_creds: dict, dest_repo: str,
                     overwrite_existing: bool) -> dict:
    try:
        def _do():
            from ._registry_client import check_tag_exists, get_registry_hostname
            # Parse source image ref
            if ":" not in source_image:
                raise ValueError(f"source_image must include tag: '{source_image}'")
            src_repo, src_tag = source_image.rsplit(":", 1)

            # Verify source exists
            if not check_tag_exists(src_connector_type, src_creds, src_repo, src_tag):
                raise ValueError(f"Source image not found: {source_image}")

            # Check destination
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

- [ ] **Step 4: Implement snapshot phase**

```python
async def _snapshot(dst_connector_type: str, dst_creds: dict,
                    dest_repo: str, src_tag: str, dest_tag_exists: bool) -> dict:
    def _do():
        if not dest_tag_exists:
            return {"exists": False, "digest": None}
        from ._registry_client import get_manifest_digest
        digest = get_manifest_digest(dst_connector_type, dst_creds, dest_repo, src_tag)
        return {"exists": True, "digest": digest}
    snap = await _run(_do)
    return {"phase": "snapshot", "status": "ok", "destination_state": snap}
```

- [ ] **Step 5: Implement ACR import transfer path**

```python
async def _transfer_acr_import(src_connector_type: str, src_creds: dict,
                                src_hostname: str, src_repo: str, src_tag: str,
                                dst_creds: dict, dest_repo: str) -> dict:
    """Use ACR import API — Azure pulls the image server-side."""
    def _do():
        import requests
        from azure.identity import ClientSecretCredential
        hostname = f"{dst_creds['registry_name']}.azurecr.io"
        subscription_id = dst_creds["subscription_id"]
        resource_group = dst_creds["resource_group"]
        registry_name = dst_creds["registry_name"]

        # Get AAD token for management plane
        aad_creds = ClientSecretCredential(
            dst_creds["tenant_id"], dst_creds["client_id"], dst_creds["client_secret"]
        )
        mgmt_token = aad_creds.get_token("https://management.azure.com/.default").token

        # Get source registry credentials for ACR to pull from
        from ._registry_client import get_auth_token, get_registry_hostname
        src_token = get_auth_token(src_connector_type, src_creds, src_repo)

        source_ref = f"{src_hostname}/{src_repo}:{src_tag}"
        target_tags = [f"{dest_repo}:{src_tag}"]

        body = {
            "source": {
                "registryUri": src_hostname,
                "sourceImage": f"{src_repo}:{src_tag}",
                "credentials": {
                    "username": "token" if src_connector_type == "aws" else "_json_key",
                    "password": src_token,
                },
            },
            "targetTags": target_tags,
            "mode": "Force",
        }
        url = (f"https://management.azure.com/subscriptions/{subscription_id}"
               f"/resourceGroups/{resource_group}/providers/Microsoft.ContainerRegistry"
               f"/registries/{registry_name}/importImage?api-version=2019-05-01")
        r = requests.post(url, json=body, headers={"Authorization": f"Bearer {mgmt_token}"}, timeout=60)
        if r.status_code == 200:
            return "acr_import"
        if r.status_code == 202:
            # Async — poll until complete
            import time
            operation_url = r.headers.get("Location")
            for _ in range(60):  # 5 min max
                time.sleep(5)
                poll = requests.get(operation_url, headers={"Authorization": f"Bearer {mgmt_token}"}, timeout=30)
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
```

- [ ] **Step 6: Implement agent Docker pull/tag/push transfer path**

```python
async def _transfer_via_agent(agent_connector, src_connector_type: str, src_creds: dict,
                               src_hostname: str, src_repo: str, src_tag: str,
                               dst_connector_type: str, dst_creds: dict,
                               dst_hostname: str, dest_repo: str) -> dict:
    """Send docker pull/tag/push command to a Nexplane agent."""
    from ._registry_client import get_auth_token, get_registry_hostname

    src_token = get_auth_token(src_connector_type, src_creds, src_repo)
    dst_token = get_auth_token(dst_connector_type, dst_creds, dest_repo)

    src_full = f"{src_hostname}/{src_repo}:{src_tag}"
    dst_full = f"{dst_hostname}/{dest_repo}:{src_tag}"

    # Docker login credentials differ by registry type:
    # ECR: username="AWS", password=short-lived ECR token (get_auth_token returns this)
    # GCR/Artifact Registry: username="oauth2accesstoken", password=GCP access token
    # OCIR: username="<namespace>/<username>", password=auth_token (not base64 — raw)
    # Use --password-stdin to avoid token appearing in process list
    src_user = "AWS" if src_connector_type == "aws" else (
        "oauth2accesstoken" if src_connector_type == "gcp" else
        f"{src_creds.get('tenancy_namespace')}/{src_creds.get('username')}"  # OCIR
    )
    dst_user = "AWS" if dst_connector_type == "aws" else (
        "oauth2accesstoken" if dst_connector_type == "gcp" else
        f"{dst_creds.get('tenancy_namespace')}/{dst_creds.get('username')}"  # OCIR
    )
    # For OCIR, get_auth_token returns base64(user:pass) for HTTP API use;
    # Docker login needs the raw auth_token instead
    src_docker_pass = src_creds.get("auth_token", src_token) if src_connector_type == "oci" else src_token
    dst_docker_pass = dst_creds.get("auth_token", dst_token) if dst_connector_type == "oci" else dst_token

    script = (
        f"echo '{src_docker_pass}' | docker login {src_hostname} -u {src_user} --password-stdin && "
        f"echo '{dst_docker_pass}' | docker login {dst_hostname} -u {dst_user} --password-stdin && "
        f"docker pull {src_full} && "
        f"docker tag {src_full} {dst_full} && "
        f"docker push {dst_full} && "
        f"docker rmi {src_full} {dst_full} || true"
    )

    # Execute via agent using existing run_command protocol
    result = await agent_connector.run_command(script, timeout=600)
    if result.get("exit_code", 1) != 0:
        raise RuntimeError(f"Agent docker transfer failed: {result.get('stderr', '')[:500]}")

    return {"phase": "transfer", "status": "ok", "method": "agent_docker"}
```

- [ ] **Step 7: Implement verify phase**

```python
async def _verify(dst_connector_type: str, dst_creds: dict,
                  dest_repo: str, src_tag: str,
                  src_connector_type: str, src_creds: dict, src_repo: str) -> dict:
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

- [ ] **Step 8: Wire up execute() with all phases**

```python
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    src_creds = getattr(connector, "source_credentials", {})
    dst_creds = getattr(connector, "dest_credentials", {})
    agent_connector = getattr(connector, "agent_connector", None)

    if not src_creds or not dst_creds:
        return {  # mock path — unchanged from Step 2 }

    src_connector_type = connector.source_connector_type
    dst_connector_type = connector.dest_connector_type
    source_image = parameters["source_image"]
    dest_repo = parameters["dest_repo"]
    overwrite_existing = parameters.get("overwrite_existing", False)

    phases = []
    rollback_data = {"snapshot": {}, "dest_image": None}

    phase1 = await _preflight(src_connector_type, src_creds, source_image,
                               dst_connector_type, dst_creds, dest_repo, overwrite_existing)
    phases.append(phase1)
    if phase1["status"] == "failed":
        return {"phases": phases, "failed": True, "rollback_data": rollback_data}

    src_repo = phase1["src_repo"]
    src_tag = phase1["src_tag"]
    src_hostname = phase1["src_hostname"]
    dst_hostname = phase1["dst_hostname"]

    phase2 = await _snapshot(dst_connector_type, dst_creds, dest_repo, src_tag, phase1["dest_tag_exists"])
    phases.append(phase2)
    rollback_data["snapshot"] = phase2["destination_state"]
    rollback_data["dest_image"] = f"{dest_repo}:{src_tag}"
    rollback_data["dst_connector_type"] = dst_connector_type

    # Choose transfer method
    if dst_connector_type == "azure":
        phase3 = await _transfer_acr_import(src_connector_type, src_creds, src_hostname,
                                             src_repo, src_tag, dst_creds, dest_repo)
    else:
        phase3 = await _transfer_via_agent(agent_connector, src_connector_type, src_creds,
                                           src_hostname, src_repo, src_tag,
                                           dst_connector_type, dst_creds,
                                           dst_hostname, dest_repo)
    phases.append(phase3)

    phase4 = await _verify(dst_connector_type, dst_creds, dest_repo, src_tag,
                            src_connector_type, src_creds, src_repo)
    phases.append(phase4)

    dest_full = f"{dst_hostname}/{dest_repo}:{src_tag}"
    summary = {
        "source_image": f"{src_hostname}/{source_image}",
        "destination_image": dest_full,
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

- [ ] **Step 9: Implement rollback()**

```python
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    dst_creds = getattr(connector, "dest_credentials", {})
    if not dst_creds:
        return {"rolled_back": True, "mock": True}

    rd = execution_result.get("rollback_data", {})
    snap = rd.get("snapshot", {})
    dest_image = rd.get("dest_image")  # "repo:tag"
    dst_connector_type = rd.get("dst_connector_type", connector.dest_connector_type)

    if not dest_image:
        return {"rolled_back": True, "note": "nothing_to_undo"}

    dest_repo, dest_tag = dest_image.rsplit(":", 1)

    def _do():
        from ._registry_client import delete_tag, restore_tag_by_digest
        if not snap.get("exists"):
            # Net-new: delete the pushed tag
            delete_tag(dst_connector_type, dst_creds, dest_repo, dest_tag)
            return True, "deleted_net_new_tag"
        else:
            # Overwrite: restore original digest
            original_digest = snap["digest"]
            restored = restore_tag_by_digest(dst_connector_type, dst_creds,
                                              dest_repo, dest_tag, original_digest)
            if restored:
                return True, "restored_original_digest"
            else:
                # GC edge case — fall back to deletion
                try:
                    delete_tag(dst_connector_type, dst_creds, dest_repo, dest_tag)
                except Exception:
                    pass
                return False, "partial_original_manifest_gc_deleted"

    try:
        ok, note = await _run(_do)
        return {"rolled_back": ok, "note": note,
                "rolled_back_image": dest_image,
                "partial": not ok}
    except Exception as e:
        logger.error("Image transfer rollback failed: %s", e)
        return {"rolled_back": False, "error": str(e)}
```

- [ ] **Step 10: Run unit tests**

```bash
pytest backend/tests/unit/test_container_image_transfer.py -v
```
Expected: all PASS

- [ ] **Step 11: Commit**

```bash
git add backend/app/connectors/executors/container_image_transfer.py
git commit -m "feat(image-transfer): implement container_image_transfer executor"
```

---

## Task 4: Catalog entry

**Files:**
- Modify: `backend/app/change_type_definitions/catalog.json` (or equivalent)

**Interfaces:**
- Produces: catalog entry consumed by planning engine and MCP tools

- [ ] **Step 1: Locate catalog file**

```bash
grep -r "container_image\|ecr_repository" backend/app/change_type_definitions/ | head -5
```

- [ ] **Step 2: Add catalog entry**

Following the pattern of existing entries, add:

```json
{
  "change_type": "container_image_transfer",
  "display_name": "Container Image Transfer",
  "description": "Transfers a container image from one cloud registry to another (ECR/OCIR/ACR/GCR). Tag is preserved; destination repo path is configurable. Supports full rollback.",
  "connector_type": "multi",
  "execution_tier": 2,
  "rollback_capability": "full",
  "parameters": [
    {"name": "source_image", "type": "string", "required": true, "description": "Source image ref: repo:tag"},
    {"name": "dest_repo", "type": "string", "required": true, "description": "Destination repo path (tag preserved from source)"},
    {"name": "overwrite_existing", "type": "boolean", "required": false, "default": false, "description": "Fail preflight if destination tag exists unless set to true"}
  ],
  "tags": ["container", "registry", "backup", "cross-cloud", "ecr", "acr", "gcr", "ocir"]
}
```

- [ ] **Step 3: Verify catalog loads without error**

```bash
cd backend && python -c "from app.change_type_definitions import load_catalog; load_catalog()"
```
Expected: no error

- [ ] **Step 4: Commit**

```bash
git add backend/app/change_type_definitions/
git commit -m "feat(image-transfer): add container_image_transfer catalog entry"
```

---

## Task 5: Smoke test

**Files:**
- Create: `backend/tests/smoke/test_smoke_container_image_transfer.py`

**Interfaces:**
- Consumes: live ECR, OCIR, ACR connectors + Nexplane agent connector registered in platform
- Produces: AGENT_TRANSFER and ACR_IMPORT smoke phases

- [ ] **Step 1: Write AGENT_TRANSFER phase (ECR → OCIR)**

```python
# backend/tests/smoke/test_smoke_container_image_transfer.py
# SPDX-License-Identifier: AGPL-3.0-only

import asyncio
import pytest
import httpx

_BASE_URL = "http://localhost:8000"


def _env(key: str) -> str:
    import os
    val = os.environ.get(key)
    assert val, f"Required env var {key} not set"
    return val


async def _get_jwt(api_token: str) -> str:
    async with httpx.AsyncClient(base_url=_BASE_URL, timeout=30) as client:
        r = await client.post("/auth/api-token", json={"token": api_token})
        assert r.status_code == 200
        return r.json()["access_token"]


@pytest.mark.smoke
@pytest.mark.smoke_phase("AGENT_TRANSFER")
async def test_agent_transfer_ecr_to_ocir():
    """Transfer alpine:3.19 from ECR to OCIR via agent docker pull/tag/push. Verify digest match and rollback."""
    token = _env("API_TOKEN")

    detail = await _create_and_execute_transfer_cr(
        token=token,
        source_connector_type="aws",
        source_image="alpine:3.19",   # small image already in ECR
        dest_connector_type="oci",
        dest_repo="nexplane/smoke/alpine",
        overwrite_existing=False,
    )

    assert detail["status"] == "completed", f"CR not completed: {detail.get('status')}"
    exec_result = detail.get("execution_result", {})
    summary = exec_result.get("summary", {})

    assert summary.get("digest_matched") is True, f"Digest mismatch: {summary}"
    assert summary.get("transfer_method") == "agent_docker", f"Wrong method: {summary}"
    assert summary.get("overwrote_existing") is False

    # Rollback: tag was net-new, so it should be deleted
    rb = await _rollback_cr_via_rest(token=token, cr_id=detail["id"])
    assert rb.get("rolled_back") is True, f"Rollback failed: {rb}"
    assert rb.get("note") == "deleted_net_new_tag"


@pytest.mark.smoke
@pytest.mark.smoke_phase("ACR_IMPORT")
async def test_acr_import_ecr_to_acr():
    """Transfer alpine:3.19 from ECR to ACR via ACR import API. Verify digest match and rollback."""
    token = _env("API_TOKEN")

    detail = await _create_and_execute_transfer_cr(
        token=token,
        source_connector_type="aws",
        source_image="alpine:3.19",
        dest_connector_type="azure",
        dest_repo="nexplane/smoke/alpine",
        overwrite_existing=False,
    )

    assert detail["status"] == "completed", f"CR not completed: {detail.get('status')}"
    exec_result = detail.get("execution_result", {})
    summary = exec_result.get("summary", {})

    assert summary.get("digest_matched") is True, f"Digest mismatch: {summary}"
    assert summary.get("transfer_method") == "acr_import", f"Wrong method: {summary}"

    rb = await _rollback_cr_via_rest(token=token, cr_id=detail["id"])
    assert rb.get("rolled_back") is True, f"Rollback failed: {rb}"
    assert rb.get("note") == "deleted_net_new_tag"
```

- [ ] **Step 2: Implement full smoke test body following existing pattern**

Copy the `_create_and_execute_baseline_cr` / `_rollback_cr_via_rest` helpers from `test_smoke_cloud_baseline_monitoring.py` and wire up both phases with real connector IDs discovered via `GET /connectors`.

For the agent transfer phase, use a small public image already mirrored into ECR (e.g. `alpine:3.19`) to avoid large layer transfers in smoke.

- [ ] **Step 3: Run smoke tests on EC2**

```bash
docker exec -e 'API_TOKEN=...' nexplane-backend-1 \
  python -m pytest tests/smoke/test_smoke_container_image_transfer.py -v -s
```
Expected: `2 passed`

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_smoke_container_image_transfer.py
git commit -m "feat(image-transfer): add smoke tests for agent and ACR import transfer paths"
```
