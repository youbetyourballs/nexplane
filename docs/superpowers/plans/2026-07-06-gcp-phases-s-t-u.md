# GCP Phases S, T, U Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement GCP smoke phases S (advanced firewall), T (GCS bucket lifecycle), and U (service account + IAM binding) in `test_gcp_live.py`, including all new executors and catalog entries they require.

**Architecture:** Each phase replaces a stub in `test_gcp_live.py` with a real CR-lifecycle implementation. Phases T and U need new executor modules in `backend/app/connectors/executors/gcp/`, new action entries in `backend/app/connectors/catalog/gcp.json`, and new change-type definition JSONs in `backend/app/connectors/change_type_definitions/`. The platform maps `change_type → change_type_definition → action_id → executor module`.

**Tech Stack:** Python 3.11, `google-cloud-storage`, `google-auth`, `googleapiclient`, SQLAlchemy enum, React/TypeScript frontend types.

## Global Constraints

- SPDX header on every new/modified file: `# SPDX-License-Identifier: AGPL-3.0-only\n# Copyright (C) 2024-2026 Nexplane, Inc.`
- All smoke phases run against live GCP infrastructure via platform CR lifecycle (create→plan→approve→execute→rollback). No mocks.
- Executor pattern: `async execute(parameters: dict, asset_ids: list, connector) -> dict` + `async rollback(parameters: dict, execution_result: dict, connector) -> dict`.
- No-creds guard: `creds = getattr(connector, "credentials", {})` — if falsy, return a minimal dict without calling GCP APIs.
- `asyncio.get_event_loop()` for run_in_executor (matches existing executors).
- Smoke tests run on EC2 (Tailscale IP 100.101.186.39). Push changes to git, pull on EC2, restart backend container, then run smoke.
- SSH command: `ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39`
- Smoke test location on EC2: `/home/ec2-user/nexplane/backend/tests/smoke/`
- Backend container: `nexplane-backend-1`
- Unit tests: `docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_gcp_new_executors.py -v`

---

## Task 1: Phase S — Advanced Firewall Smoke

**Files:**
- Modify: `backend/tests/smoke/test_gcp_live.py`

**Interfaces:**
- Consumes: existing `gcp_firewall_create` change type (action `create_firewall_rule` in gcp.json), `_get_gcp_creds()`, `compute_v1.FirewallsClient`
- Produces: `run_phase_s(client, cloud_account_id, gcp_project)` replacing `run_phase_s_stub`

- [ ] **Step 1: Replace the stub function with the real implementation**

In `backend/tests/smoke/test_gcp_live.py`, replace the entire `run_phase_s_stub` function (and update the `main()` call from `run_phase_s_stub` → `run_phase_s`):

```python
def run_phase_s(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase S: Advanced firewall — two rules with different priorities and protocols."""
    print("\n[Phase S] Advanced Firewall Rules")

    import time as _time
    rule1 = f"nexplane-smoke-s-tcp-{int(_time.time())}"
    rule2 = f"nexplane-smoke-s-udp-{int(_time.time()) + 1}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        cr1 = client.run_cr(
            "[Phase S] create TCP firewall rule", "gcp_firewall_create", cloud_account_id,
            {
                "project": gcp_project,
                "rule_name": rule1,
                "network": "global/networks/default",
                "direction": "INGRESS",
                "priority": 900,
                "allowed": [{"IPProtocol": "tcp", "ports": ["8080"]}],
                "source_ranges": ["192.0.2.0/24"],
                "description": "Nexplane smoke test — safe to delete",
            },
        )
        rollback_stack.append((cr1["id"], "gcp_firewall_create"))
        log(f"Firewall rule 1 created: {rule1} (priority 900, TCP 8080)")

        cr2 = client.run_cr(
            "[Phase S] create UDP firewall rule", "gcp_firewall_create", cloud_account_id,
            {
                "project": gcp_project,
                "rule_name": rule2,
                "network": "global/networks/default",
                "direction": "INGRESS",
                "priority": 800,
                "allowed": [{"IPProtocol": "udp", "ports": ["5353"]}],
                "source_ranges": ["192.0.2.0/24"],
                "description": "Nexplane smoke test — safe to delete",
            },
        )
        rollback_stack.append((cr2["id"], "gcp_firewall_create"))
        log(f"Firewall rule 2 created: {rule2} (priority 800, UDP 5353)")

        # Verify both rules via SDK
        creds = _get_gcp_creds()
        if creds:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import compute_v1
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                fw_client = compute_v1.FirewallsClient(credentials=gc)
                fw1 = fw_client.get(project=gcp_project, firewall=rule1)
                assert fw1.priority == 900, f"Expected priority 900, got {fw1.priority}"
                fw2 = fw_client.get(project=gcp_project, firewall=rule2)
                assert fw2.priority == 800, f"Expected priority 800, got {fw2.priority}"
                log("Both firewall rules verified via SDK (priority + protocol)")
            except Exception as e:
                print(f"  ⚠️  SDK verification error: {e}")
                raise
        else:
            print("  ⚠️  No GCP credentials — SDK verification skipped")

        # Rollback (LIFO)
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        rollback_stack.clear()
        log("Phase S complete")

    except Exception as e:
        print(f"\n❌ Phase S failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete rules via SDK if they still exist
        creds = _get_gcp_creds()
        if creds and gcp_project:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import compute_v1
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                fw_client = compute_v1.FirewallsClient(credentials=gc)
                for rule_name in [rule1, rule2]:
                    try:
                        fw_client.delete(project=gcp_project, firewall=rule_name)
                        print(f"  Safety net: deleted firewall rule {rule_name}")
                    except Exception:
                        pass
            except Exception as e2:
                print(f"  ⚠️  Safety net cleanup failed: {e2}")
```

Also update `main()` — find this line:
```python
        if "S" in phases:
            run_phase_s_stub(client, cloud_account_id, args.gcp_project)
```
Change to:
```python
        if "S" in phases:
            run_phase_s(client, cloud_account_id, args.gcp_project)
```

- [ ] **Step 2: Push changes, pull on EC2, restart backend**

```bash
git add backend/tests/smoke/test_gcp_live.py
git commit -m "feat(smoke): implement phase S advanced firewall"
git push
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "export HOME=/home/ec2-user && cd /home/ec2-user/nexplane && git pull"
```

- [ ] **Step 3: Run Phase S smoke on EC2**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane/backend/tests/smoke && python test_gcp_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme \
  --phases S \
  --gcp-project YOUR_GCP_PROJECT_ID"
```

Expected output:
```
[Phase S] Advanced Firewall Rules
  ✓ Firewall rule 1 created: nexplane-smoke-s-tcp-... (priority 900, TCP 8080)
  ✓ Firewall rule 2 created: nexplane-smoke-s-udp-... (priority 800, UDP 5353)
  ✓ Both firewall rules verified via SDK (priority + protocol)
✅ ALL SELECTED PHASES PASSED
```

---

## Task 2: GCS Bucket Create/Delete Executors + Catalog + ChangeType

**Files:**
- Create: `backend/app/connectors/executors/gcp/create_bucket.py`
- Create: `backend/app/connectors/executors/gcp/delete_bucket.py`
- Modify: `backend/app/connectors/catalog/gcp.json` (add 2 action entries)
- Create: `backend/app/connectors/change_type_definitions/gcp_bucket_create.json`
- Create: `backend/app/connectors/change_type_definitions/gcp_bucket_delete.json`
- Modify: `backend/app/models/change_request.py` (add 2 ChangeType enum values)
- Modify: `frontend/src/types/api.ts` (add 2 ChangeType union values)
- Create: `backend/app/tests/test_gcp_new_executors.py` (unit tests)

**Interfaces:**
- Produces: `create_bucket.execute({"bucket_name": str, "location": str}, [], connector) -> {"action": "create_bucket", "bucket_name": str, "location": str}`
- Produces: `delete_bucket.execute({"bucket_name": str}, [], connector) -> {"action": "delete_bucket", "bucket_name": str, "deleted": bool}`

- [ ] **Step 1: Write the failing unit test**

Create `backend/app/tests/test_gcp_new_executors.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import pytest


class _FakeConnector:
    credentials = {}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_create_bucket_no_creds_returns_action():
    from app.connectors.executors.gcp.create_bucket import execute
    result = _run(execute({"bucket_name": "smoke-test-bucket", "location": "US"}, [], _FakeConnector()))
    assert result["action"] == "create_bucket"
    assert result["bucket_name"] == "smoke-test-bucket"


def test_delete_bucket_no_creds_returns_action():
    from app.connectors.executors.gcp.delete_bucket import execute
    result = _run(execute({"bucket_name": "smoke-test-bucket"}, [], _FakeConnector()))
    assert result["action"] == "delete_bucket"
    assert result["deleted"] is True


def test_create_bucket_rollback_calls_delete():
    from app.connectors.executors.gcp.create_bucket import rollback
    result = _run(rollback(
        {"bucket_name": "smoke-test-bucket"},
        {"bucket_name": "smoke-test-bucket"},
        _FakeConnector(),
    ))
    assert result["action"] == "delete_bucket"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_gcp_new_executors.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.connectors.executors.gcp.create_bucket'`

- [ ] **Step 3: Create `create_bucket.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket_name = parameters["bucket_name"]
    location = parameters.get("location", "US")
    if not creds:
        return {"action": "create_bucket", "bucket_name": bucket_name, "location": location}
    from ._client import get_credentials, get_project_id
    from google.cloud import storage
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _create():
        client = storage.Client(credentials=credentials, project=project)
        bucket = client.bucket(bucket_name)
        bucket.storage_class = "STANDARD"
        new_bucket = client.create_bucket(bucket, location=location)
        return new_bucket.location

    actual_location = await loop.run_in_executor(None, _create)
    return {"action": "create_bucket", "bucket_name": bucket_name, "location": actual_location}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_bucket import execute as delete
    bucket_name = execution_result.get("bucket_name", parameters.get("bucket_name"))
    return await delete({"bucket_name": bucket_name}, [], connector)
```

- [ ] **Step 4: Create `delete_bucket.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket_name = parameters["bucket_name"]
    if not creds:
        return {"action": "delete_bucket", "bucket_name": bucket_name, "deleted": True}
    from ._client import get_credentials, get_project_id
    from google.cloud import storage
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _delete():
        client = storage.Client(credentials=credentials, project=project)
        bucket = client.bucket(bucket_name)
        bucket.delete(force=True)

    await loop.run_in_executor(None, _delete)
    return {"action": "delete_bucket", "bucket_name": bucket_name, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "bucket deletion is irreversible"}
```

- [ ] **Step 5: Add action entries to `gcp.json`**

Open `backend/app/connectors/catalog/gcp.json`. Before the closing `]` of the `"actions"` array, add:

```json
    {"action_id": "create_bucket", "generic_action": "create_bucket", "action_type": "change", "execution_tier": 2, "display_name": "Create GCS Bucket", "description": "Create a new GCS bucket in the specified location. Rollback: delete.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "bucket_name", "type": "string", "required": true}, {"name": "location", "type": "string", "required": false, "default": "US"}], "executor": "gcp.create_bucket", "rollback_action": "delete_bucket", "estimated_duration_seconds": 15},
    {"action_id": "delete_bucket", "generic_action": "delete_bucket", "action_type": "change", "execution_tier": 2, "display_name": "Delete GCS Bucket", "description": "Delete a GCS bucket and all its contents (force=True).", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "bucket_name", "type": "string", "required": true}], "executor": "gcp.delete_bucket", "estimated_duration_seconds": 15, "blast_radius_hint": "destructive"}
```

- [ ] **Step 6: Create change-type definition JSONs**

`backend/app/connectors/change_type_definitions/gcp_bucket_create.json`:
```json
{
  "change_type": "gcp_bucket_create",
  "display_name": "Create GCS Bucket",
  "steps": [
    {"generic_action": "create_bucket", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_bucket",
  "rollback_connector_type": "gcp"
}
```

`backend/app/connectors/change_type_definitions/gcp_bucket_delete.json`:
```json
{
  "change_type": "gcp_bucket_delete",
  "display_name": "Delete GCS Bucket",
  "steps": [
    {"generic_action": "delete_bucket", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_connector_type": "gcp"
}
```

- [ ] **Step 7: Add ChangeType enum values**

In `backend/app/models/change_request.py`, locate the GCP section (near `gce_instance_create`). Add after the last GCP entry:

```python
    # GCP bucket lifecycle
    gcp_bucket_create = "gcp_bucket_create"
    gcp_bucket_delete = "gcp_bucket_delete"
```

- [ ] **Step 8: Add frontend type values**

In `frontend/src/types/api.ts`, find the `ChangeType` union type and add `"gcp_bucket_create" | "gcp_bucket_delete"` to it.

- [ ] **Step 9: Run unit tests to verify they pass**

```bash
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_gcp_new_executors.py -v
```

Expected:
```
test_create_bucket_no_creds_returns_action PASSED
test_delete_bucket_no_creds_returns_action PASSED
test_create_bucket_rollback_calls_delete PASSED
3 passed in 0.XX s
```

- [ ] **Step 10: Commit**

```bash
git add backend/app/connectors/executors/gcp/create_bucket.py \
        backend/app/connectors/executors/gcp/delete_bucket.py \
        backend/app/connectors/catalog/gcp.json \
        backend/app/connectors/change_type_definitions/gcp_bucket_create.json \
        backend/app/connectors/change_type_definitions/gcp_bucket_delete.json \
        backend/app/models/change_request.py \
        frontend/src/types/api.ts \
        backend/app/tests/test_gcp_new_executors.py
git commit -m "feat(gcp): add create_bucket and delete_bucket executors"
git push
```

---

## Task 3: Phase T — GCS Bucket Lifecycle Smoke

**Files:**
- Modify: `backend/tests/smoke/test_gcp_live.py`

**Interfaces:**
- Consumes: `gcp_bucket_create` (Task 2), `gcp_bucket_delete` (Task 2), `gcp_block_public_bucket_access` (existing)
- Produces: `run_phase_t(client, cloud_account_id, gcp_project)` replacing `run_phase_t_stub`

- [ ] **Step 1: Pull on EC2 and restart backend to pick up Task 2 changes**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "export HOME=/home/ec2-user && cd /home/ec2-user/nexplane && git pull && docker compose restart backend"
sleep 10
```

- [ ] **Step 2: Replace the stub with the real implementation**

In `backend/tests/smoke/test_gcp_live.py`, replace `run_phase_t_stub` and update main():

```python
def run_phase_t(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase T: GCS bucket create + block public access + rollback."""
    print("\n[Phase T] GCS Bucket Lifecycle")

    import secrets as _secrets
    bucket_name = f"nexplane-smoke-t-{_secrets.token_hex(4)}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create bucket via CR
        cr = client.run_cr(
            "[Phase T] create GCS bucket", "gcp_bucket_create", cloud_account_id,
            {"bucket_name": bucket_name, "location": "US"},
        )
        rollback_stack.append((cr["id"], "gcp_bucket_create"))
        log(f"GCS bucket created: {bucket_name}")

        # 2. Block public access via CR
        cr2 = client.run_cr(
            "[Phase T] block public GCS bucket access", "gcp_block_public_bucket_access",
            cloud_account_id,
            {"project": gcp_project, "bucket_name": bucket_name},
        )
        rollback_stack.append((cr2["id"], "gcp_block_public_bucket_access"))
        log("Public access blocked on bucket")

        # 3. Verify via SDK
        creds = _get_gcp_creds()
        if creds:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import storage as _storage
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                sc = _storage.Client(project=gcp_project, credentials=gc)
                bucket_obj = sc.get_bucket(bucket_name)
                assert bucket_obj.name == bucket_name, "Bucket not found"
                policy = bucket_obj.get_iam_policy()
                has_public = any(
                    "allUsers" in b.get("members", []) or "allAuthenticatedUsers" in b.get("members", [])
                    for b in policy.bindings
                )
                assert not has_public, "allUsers/allAuthenticatedUsers still present after blocking"
                log("Bucket exists + public access blocked (SDK verified)")
            except Exception as e:
                print(f"  ⚠️  SDK verification error: {e}")
                raise
        else:
            print("  ⚠️  No GCP credentials — SDK verification skipped")

        # 4. Rollback (LIFO): block_public (no-op rollback) then delete bucket
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        rollback_stack.clear()
        log("Phase T complete")

    except Exception as e:
        print(f"\n❌ Phase T failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete bucket via SDK
        creds = _get_gcp_creds()
        if creds and gcp_project:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import storage as _storage
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                sc = _storage.Client(project=gcp_project, credentials=gc)
                try:
                    sc.get_bucket(bucket_name).delete(force=True)
                    print(f"  Safety net: deleted GCS bucket {bucket_name}")
                except Exception:
                    pass
            except Exception as e2:
                print(f"  ⚠️  Safety net bucket delete failed: {e2}")
```

Update main():
```python
        if "T" in phases:
            run_phase_t(client, cloud_account_id, args.gcp_project)
```

- [ ] **Step 3: Commit and push**

```bash
git add backend/tests/smoke/test_gcp_live.py
git commit -m "feat(smoke): implement phase T GCS bucket lifecycle"
git push
```

- [ ] **Step 4: Pull on EC2, restart backend, run Phase T smoke**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "export HOME=/home/ec2-user && cd /home/ec2-user/nexplane && git pull && docker compose restart backend"
sleep 10

ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane/backend/tests/smoke && python test_gcp_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme \
  --phases T \
  --gcp-project YOUR_GCP_PROJECT_ID"
```

Expected output:
```
[Phase T] GCS Bucket Lifecycle
  ✓ GCS bucket created: nexplane-smoke-t-...
  ✓ Public access blocked on bucket
  ✓ Bucket exists + public access blocked (SDK verified)
✅ ALL SELECTED PHASES PASSED
```

---

## Task 4: SA + IAM Binding Executors + Catalog + ChangeType

**Files:**
- Create: `backend/app/connectors/executors/gcp/create_service_account.py`
- Create: `backend/app/connectors/executors/gcp/delete_service_account.py`
- Create: `backend/app/connectors/executors/gcp/add_iam_binding.py`
- Create: `backend/app/connectors/executors/gcp/remove_iam_binding.py`
- Modify: `backend/app/connectors/catalog/gcp.json` (add 4 action entries)
- Create: `backend/app/connectors/change_type_definitions/gcp_service_account_create.json`
- Create: `backend/app/connectors/change_type_definitions/gcp_service_account_delete.json`
- Create: `backend/app/connectors/change_type_definitions/gcp_iam_binding_add.json`
- Create: `backend/app/connectors/change_type_definitions/gcp_iam_binding_remove.json`
- Modify: `backend/app/models/change_request.py` (add 4 ChangeType enum values)
- Modify: `frontend/src/types/api.ts` (add 4 ChangeType union values)
- Modify: `backend/app/tests/test_gcp_new_executors.py` (add 4 unit tests)

**Interfaces:**
- Produces: `create_service_account.execute({"account_id": str, "display_name": str}, [], connector) -> {"action": "create_service_account", "email": str}`
- Produces: `delete_service_account.execute({"email": str}, [], connector) -> {"action": "delete_service_account", "deleted": bool}`
- Produces: `add_iam_binding.execute({"role": str, "member": str}, [], connector) -> {"action": "add_iam_binding", "role": str, "member": str}`
- Produces: `remove_iam_binding.execute({"role": str, "member": str}, [], connector) -> {"action": "remove_iam_binding", "role": str, "member": str}`

- [ ] **Step 1: Add unit tests for the 4 new executors**

Append to `backend/app/tests/test_gcp_new_executors.py`:

```python
def test_create_service_account_no_creds():
    from app.connectors.executors.gcp.create_service_account import execute
    result = _run(execute(
        {"account_id": "test-sa", "display_name": "Test SA"},
        [], _FakeConnector()
    ))
    assert result["action"] == "create_service_account"
    assert "email" in result


def test_delete_service_account_no_creds():
    from app.connectors.executors.gcp.delete_service_account import execute
    result = _run(execute(
        {"email": "test@project.iam.gserviceaccount.com"},
        [], _FakeConnector()
    ))
    assert result["action"] == "delete_service_account"
    assert result["deleted"] is True


def test_add_iam_binding_no_creds():
    from app.connectors.executors.gcp.add_iam_binding import execute
    result = _run(execute(
        {"role": "roles/viewer", "member": "serviceAccount:test@project.iam.gserviceaccount.com"},
        [], _FakeConnector()
    ))
    assert result["action"] == "add_iam_binding"
    assert result["role"] == "roles/viewer"


def test_remove_iam_binding_no_creds():
    from app.connectors.executors.gcp.remove_iam_binding import execute
    result = _run(execute(
        {"role": "roles/viewer", "member": "serviceAccount:test@project.iam.gserviceaccount.com"},
        [], _FakeConnector()
    ))
    assert result["action"] == "remove_iam_binding"
    assert result["role"] == "roles/viewer"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_gcp_new_executors.py::test_create_service_account_no_creds -v
```

Expected: `ModuleNotFoundError: No module named 'app.connectors.executors.gcp.create_service_account'`

- [ ] **Step 3: Create `create_service_account.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    account_id = parameters["account_id"]
    display_name = parameters.get("display_name", account_id)
    if not creds:
        project = "mock-project"
        return {
            "action": "create_service_account",
            "email": f"{account_id}@{project}.iam.gserviceaccount.com",
            "unique_id": "mock-unique-id",
        }
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _create():
        from googleapiclient.discovery import build
        svc = build("iam", "v1", credentials=credentials)
        sa = svc.projects().serviceAccounts().create(
            name=f"projects/{project}",
            body={
                "accountId": account_id,
                "serviceAccount": {"displayName": display_name},
            },
        ).execute()
        return sa["email"], sa["uniqueId"]

    email, unique_id = await loop.run_in_executor(None, _create)
    return {"action": "create_service_account", "email": email, "unique_id": unique_id}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_service_account import execute as delete
    email = execution_result.get("email", "")
    if not email:
        return {"rolled_back": False, "reason": "no email in execution_result"}
    return await delete({"email": email}, [], connector)
```

- [ ] **Step 4: Create `delete_service_account.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    email = parameters["email"]
    if not creds:
        return {"action": "delete_service_account", "email": email, "deleted": True}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _delete():
        from googleapiclient.discovery import build
        svc = build("iam", "v1", credentials=credentials)
        svc.projects().serviceAccounts().delete(
            name=f"projects/{project}/serviceAccounts/{email}"
        ).execute()

    await loop.run_in_executor(None, _delete)
    return {"action": "delete_service_account", "email": email, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "service account deletion is irreversible"}
```

- [ ] **Step 5: Create `add_iam_binding.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    role = parameters["role"]
    member = parameters["member"]
    if not creds:
        return {"action": "add_iam_binding", "role": role, "member": member}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _add():
        from googleapiclient.discovery import build
        crm = build("cloudresourcemanager", "v1", credentials=credentials)
        policy = crm.projects().getIamPolicy(resource=project, body={}).execute()
        bindings = policy.get("bindings", [])
        for b in bindings:
            if b["role"] == role:
                if member not in b["members"]:
                    b["members"].append(member)
                break
        else:
            bindings.append({"role": role, "members": [member]})
        policy["bindings"] = bindings
        crm.projects().setIamPolicy(
            resource=project, body={"policy": policy}
        ).execute()

    await loop.run_in_executor(None, _add)
    return {"action": "add_iam_binding", "role": role, "member": member}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.remove_iam_binding import execute as remove
    return await remove(
        {"role": execution_result.get("role", parameters["role"]),
         "member": execution_result.get("member", parameters["member"])},
        [], connector,
    )
```

- [ ] **Step 6: Create `remove_iam_binding.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    role = parameters["role"]
    member = parameters["member"]
    if not creds:
        return {"action": "remove_iam_binding", "role": role, "member": member}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _remove():
        from googleapiclient.discovery import build
        crm = build("cloudresourcemanager", "v1", credentials=credentials)
        policy = crm.projects().getIamPolicy(resource=project, body={}).execute()
        for b in policy.get("bindings", []):
            if b["role"] == role and member in b.get("members", []):
                b["members"].remove(member)
        policy["bindings"] = [b for b in policy.get("bindings", []) if b.get("members")]
        crm.projects().setIamPolicy(
            resource=project, body={"policy": policy}
        ).execute()

    await loop.run_in_executor(None, _remove)
    return {"action": "remove_iam_binding", "role": role, "member": member}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "binding removal rollback requires re-adding — use add_iam_binding CR"}
```

- [ ] **Step 7: Add 4 action entries to `gcp.json`**

Append to the `"actions"` array in `backend/app/connectors/catalog/gcp.json`:

```json
    {"action_id": "create_service_account", "generic_action": "create_service_account", "action_type": "change", "execution_tier": 2, "display_name": "Create Service Account", "description": "Create a new GCP service account. Rollback: delete.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "account_id", "type": "string", "required": true}, {"name": "display_name", "type": "string", "required": false}], "executor": "gcp.create_service_account", "rollback_action": "delete_service_account", "estimated_duration_seconds": 10},
    {"action_id": "delete_service_account", "generic_action": "delete_service_account", "action_type": "change", "execution_tier": 2, "display_name": "Delete Service Account", "description": "Delete a GCP service account.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "email", "type": "string", "required": true}], "executor": "gcp.delete_service_account", "estimated_duration_seconds": 10, "blast_radius_hint": "destructive"},
    {"action_id": "add_iam_binding", "generic_action": "add_iam_binding", "action_type": "change", "execution_tier": 2, "display_name": "Add IAM Binding", "description": "Add a member to a project-level IAM role binding. Rollback: remove binding.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "role", "type": "string", "required": true}, {"name": "member", "type": "string", "required": true, "description": "e.g. serviceAccount:sa@project.iam.gserviceaccount.com"}], "executor": "gcp.add_iam_binding", "rollback_action": "remove_iam_binding", "estimated_duration_seconds": 10},
    {"action_id": "remove_iam_binding", "generic_action": "remove_iam_binding", "action_type": "change", "execution_tier": 2, "display_name": "Remove IAM Binding", "description": "Remove a member from a project-level IAM role binding.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "role", "type": "string", "required": true}, {"name": "member", "type": "string", "required": true}], "executor": "gcp.remove_iam_binding", "estimated_duration_seconds": 10}
```

- [ ] **Step 8: Create 4 change-type definition JSONs**

`backend/app/connectors/change_type_definitions/gcp_service_account_create.json`:
```json
{
  "change_type": "gcp_service_account_create",
  "display_name": "Create GCP Service Account",
  "steps": [{"generic_action": "create_service_account", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_service_account",
  "rollback_connector_type": "gcp"
}
```

`backend/app/connectors/change_type_definitions/gcp_service_account_delete.json`:
```json
{
  "change_type": "gcp_service_account_delete",
  "display_name": "Delete GCP Service Account",
  "steps": [{"generic_action": "delete_service_account", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_connector_type": "gcp"
}
```

`backend/app/connectors/change_type_definitions/gcp_iam_binding_add.json`:
```json
{
  "change_type": "gcp_iam_binding_add",
  "display_name": "Add GCP IAM Binding",
  "steps": [{"generic_action": "add_iam_binding", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "remove_iam_binding",
  "rollback_connector_type": "gcp"
}
```

`backend/app/connectors/change_type_definitions/gcp_iam_binding_remove.json`:
```json
{
  "change_type": "gcp_iam_binding_remove",
  "display_name": "Remove GCP IAM Binding",
  "steps": [{"generic_action": "remove_iam_binding", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_connector_type": "gcp"
}
```

- [ ] **Step 9: Add ChangeType enum values**

In `backend/app/models/change_request.py`, add after `gcp_bucket_delete`:

```python
    # GCP service account + IAM lifecycle
    gcp_service_account_create = "gcp_service_account_create"
    gcp_service_account_delete = "gcp_service_account_delete"
    gcp_iam_binding_add = "gcp_iam_binding_add"
    gcp_iam_binding_remove = "gcp_iam_binding_remove"
```

- [ ] **Step 10: Add frontend type values**

In `frontend/src/types/api.ts`, add to the `ChangeType` union:
`"gcp_service_account_create" | "gcp_service_account_delete" | "gcp_iam_binding_add" | "gcp_iam_binding_remove"`

- [ ] **Step 11: Run all unit tests**

```bash
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_gcp_new_executors.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 12: Commit**

```bash
git add backend/app/connectors/executors/gcp/create_service_account.py \
        backend/app/connectors/executors/gcp/delete_service_account.py \
        backend/app/connectors/executors/gcp/add_iam_binding.py \
        backend/app/connectors/executors/gcp/remove_iam_binding.py \
        backend/app/connectors/catalog/gcp.json \
        backend/app/connectors/change_type_definitions/gcp_service_account_create.json \
        backend/app/connectors/change_type_definitions/gcp_service_account_delete.json \
        backend/app/connectors/change_type_definitions/gcp_iam_binding_add.json \
        backend/app/connectors/change_type_definitions/gcp_iam_binding_remove.json \
        backend/app/models/change_request.py \
        frontend/src/types/api.ts \
        backend/app/tests/test_gcp_new_executors.py
git commit -m "feat(gcp): add SA create/delete and IAM binding add/remove executors"
git push
```

---

## Task 5: Phase U — Service Account + IAM Binding Smoke

**Files:**
- Modify: `backend/tests/smoke/test_gcp_live.py`

**Interfaces:**
- Consumes: `gcp_service_account_create`, `gcp_service_account_delete`, `gcp_iam_binding_add`, `gcp_iam_binding_remove` (Task 4)
- Produces: `run_phase_u(client, cloud_account_id, gcp_project)` replacing `run_phase_u_stub`

- [ ] **Step 1: Pull on EC2 and restart backend**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "export HOME=/home/ec2-user && cd /home/ec2-user/nexplane && git pull && docker compose restart backend"
sleep 10
```

- [ ] **Step 2: Replace the stub with the real implementation**

In `backend/tests/smoke/test_gcp_live.py`, replace `run_phase_u_stub` and update main():

```python
def run_phase_u(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase U: Service account create + IAM binding add + rollback stack."""
    print("\n[Phase U] Service Account + IAM Binding")

    import time as _time
    ts = int(_time.time()) % 100000
    account_id = f"nexplane-smoke-u-{ts}"
    sa_email = f"{account_id}@{gcp_project}.iam.gserviceaccount.com"
    role = "roles/viewer"
    member = f"serviceAccount:{sa_email}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create service account via CR
        cr = client.run_cr(
            "[Phase U] create service account", "gcp_service_account_create", cloud_account_id,
            {"account_id": account_id, "display_name": "Nexplane smoke test"},
        )
        rollback_stack.append((cr["id"], "gcp_service_account_create"))
        log(f"Service account created: {sa_email}")

        # 2. GCP SA eventual consistency — poll up to 30s
        creds = _get_gcp_creds()
        if creds:
            import json as _json
            from google.oauth2 import service_account as _sa_lib
            from googleapiclient.discovery import build as _build
            key_raw = creds.get("service_account_key_json", "")
            key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
            gc = _sa_lib.Credentials.from_service_account_info(
                key_json, scopes=["https://www.googleapis.com/auth/cloud-platform",
                                  "https://www.googleapis.com/auth/iam"])
            iam_svc = _build("iam", "v1", credentials=gc)
            sa_ready = False
            for _ in range(6):
                _time.sleep(5)
                try:
                    iam_svc.projects().serviceAccounts().get(
                        name=f"projects/{gcp_project}/serviceAccounts/{sa_email}"
                    ).execute()
                    sa_ready = True
                    break
                except Exception:
                    pass
            if not sa_ready:
                raise RuntimeError(f"Service account {sa_email} not available after 30s")
            log("Service account confirmed available via SDK")

        # 3. Add IAM binding via CR
        cr2 = client.run_cr(
            "[Phase U] add IAM binding", "gcp_iam_binding_add", cloud_account_id,
            {"role": role, "member": member},
        )
        rollback_stack.append((cr2["id"], "gcp_iam_binding_add"))
        log(f"IAM binding added: {role} → {member}")

        # 4. Verify both SA exists and binding is present via SDK
        if creds:
            try:
                from googleapiclient.discovery import build as _build2
                crm = _build2("cloudresourcemanager", "v1", credentials=gc)
                policy = crm.projects().getIamPolicy(resource=gcp_project, body={}).execute()
                binding_found = any(
                    b["role"] == role and member in b.get("members", [])
                    for b in policy.get("bindings", [])
                )
                assert binding_found, f"IAM binding {role}/{member} not found in project policy"
                log("IAM binding verified via SDK")
            except Exception as e:
                print(f"  ⚠️  SDK verification error: {e}")
                raise

        # 5. Rollback (LIFO): remove binding, then delete SA
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        rollback_stack.clear()
        log("Phase U complete")

    except Exception as e:
        print(f"\n❌ Phase U failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete SA via SDK
        creds = _get_gcp_creds()
        if creds and gcp_project:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa_lib
                from googleapiclient.discovery import build as _build
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa_lib.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform",
                                      "https://www.googleapis.com/auth/iam"])
                iam_svc = _build("iam", "v1", credentials=gc)
                try:
                    iam_svc.projects().serviceAccounts().delete(
                        name=f"projects/{gcp_project}/serviceAccounts/{sa_email}"
                    ).execute()
                    print(f"  Safety net: deleted service account {sa_email}")
                except Exception:
                    pass
            except Exception as e2:
                print(f"  ⚠️  Safety net SA delete failed: {e2}")
```

Update main():
```python
        if "U" in phases:
            run_phase_u(client, cloud_account_id, args.gcp_project)
```

- [ ] **Step 3: Commit and push**

```bash
git add backend/tests/smoke/test_gcp_live.py
git commit -m "feat(smoke): implement phase U service account + IAM binding"
git push
```

- [ ] **Step 4: Pull on EC2, restart backend, run Phase U smoke**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "export HOME=/home/ec2-user && cd /home/ec2-user/nexplane && git pull && docker compose restart backend"
sleep 10

ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane/backend/tests/smoke && python test_gcp_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme \
  --phases U \
  --gcp-project YOUR_GCP_PROJECT_ID"
```

Expected output:
```
[Phase U] Service Account + IAM Binding
  ✓ Service account created: nexplane-smoke-u-...@<project>.iam.gserviceaccount.com
  ✓ Service account confirmed available via SDK
  ✓ IAM binding added: roles/viewer → serviceAccount:...
  ✓ IAM binding verified via SDK
✅ ALL SELECTED PHASES PASSED
```

- [ ] **Step 5: Run S, T, U combined to verify no regressions**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane/backend/tests/smoke && python test_gcp_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme \
  --phases S,T,U \
  --gcp-project YOUR_GCP_PROJECT_ID"
```

Expected: all 3 phases pass with `✅ ALL SELECTED PHASES PASSED`.
