# GCP Phases V, W, X Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement GCP smoke phases V (Cloud DNS), W (Cloud SQL), and X (Cloud Monitoring) in `test_gcp_live.py`, including all new executors and catalog entries they require.

**Architecture:** Each phase replaces a stub in `test_gcp_live.py` with a real CR-lifecycle implementation. All 11 new executor modules go in `backend/app/connectors/executors/gcp/`. Each executor has a paired action entry in `backend/app/connectors/catalog/gcp.json` and a change-type definition JSON in `backend/app/connectors/change_type_definitions/`.

**Tech Stack:** Python 3.11, `googleapiclient` (discovery for DNS, Cloud SQL, IAM, CRM), `google-cloud-monitoring` (`google.cloud.monitoring_v3`), SQLAlchemy enum, React/TypeScript frontend types.

## Global Constraints

- SPDX header on every new/modified file: `# SPDX-License-Identifier: AGPL-3.0-only\n# Copyright (C) 2024-2026 Nexplane, Inc.`
- All smoke phases run against live GCP infrastructure via platform CR lifecycle (create→plan→approve→execute→rollback). No mocks.
- Executor pattern: `async execute(parameters: dict, asset_ids: list, connector) -> dict` + `async rollback(parameters: dict, execution_result: dict, connector) -> dict`.
- No-creds guard: `creds = getattr(connector, "credentials", {})` — if falsy, return a minimal dict without calling GCP APIs.
- `asyncio.get_event_loop()` for run_in_executor (matches existing executors).
- Smoke tests run on EC2 (Tailscale IP 100.101.186.39). Push to git, pull on EC2, restart backend container, then run smoke.
- SSH command: `ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39`
- Smoke test location on EC2: `/home/ec2-user/nexplane/backend/tests/smoke/`
- Backend container: `nexplane-backend-1`
- Phase W Cloud SQL timeout: provisioning takes 5-10 minutes; use polling with 900s deadline.
- Unit tests: `docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_gcp_vwx_executors.py -v`

---

## Task 1: Cloud DNS Executors + Catalog + ChangeType

**Files:**
- Create: `backend/app/connectors/executors/gcp/create_dns_zone.py`
- Create: `backend/app/connectors/executors/gcp/delete_dns_zone.py`
- Create: `backend/app/connectors/executors/gcp/create_dns_record.py`
- Create: `backend/app/connectors/executors/gcp/delete_dns_record.py`
- Modify: `backend/app/connectors/catalog/gcp.json` (add 4 action entries)
- Create: `backend/app/connectors/change_type_definitions/gcp_dns_zone_create.json`
- Create: `backend/app/connectors/change_type_definitions/gcp_dns_zone_delete.json`
- Create: `backend/app/connectors/change_type_definitions/gcp_dns_record_create.json`
- Create: `backend/app/connectors/change_type_definitions/gcp_dns_record_delete.json`
- Modify: `backend/app/models/change_request.py` (add 4 ChangeType enum values)
- Modify: `frontend/src/types/api.ts` (add 4 ChangeType union values)
- Create: `backend/app/tests/test_gcp_vwx_executors.py` (unit tests)

**Interfaces:**
- Produces: `create_dns_zone.execute({"zone_name": str, "dns_name": str, "description": str}, [], connector) -> {"action": "create_dns_zone", "zone_name": str, "dns_name": str}`
- Produces: `delete_dns_zone.execute({"zone_name": str}, [], connector) -> {"action": "delete_dns_zone", "zone_name": str, "deleted": bool}`
- Produces: `create_dns_record.execute({"zone_name": str, "record_name": str, "record_type": str, "ttl": int, "rrdatas": list}, [], connector) -> {"action": "create_dns_record", "zone_name": str, "record_name": str}`
- Produces: `delete_dns_record.execute({"zone_name": str, "record_name": str, "record_type": str}, [], connector) -> {"action": "delete_dns_record", "zone_name": str, "record_name": str, "deleted": bool}`

- [ ] **Step 1: Write the failing unit tests**

Create `backend/app/tests/test_gcp_vwx_executors.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


class _FakeConnector:
    credentials = {}


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# --- DNS ---

def test_create_dns_zone_no_creds():
    from app.connectors.executors.gcp.create_dns_zone import execute
    result = _run(execute(
        {"zone_name": "smoke-zone", "dns_name": "smoke.example.", "description": "test"},
        [], _FakeConnector()
    ))
    assert result["action"] == "create_dns_zone"
    assert result["zone_name"] == "smoke-zone"


def test_delete_dns_zone_no_creds():
    from app.connectors.executors.gcp.delete_dns_zone import execute
    result = _run(execute({"zone_name": "smoke-zone"}, [], _FakeConnector()))
    assert result["action"] == "delete_dns_zone"
    assert result["deleted"] is True


def test_create_dns_record_no_creds():
    from app.connectors.executors.gcp.create_dns_record import execute
    result = _run(execute(
        {"zone_name": "smoke-zone", "record_name": "test.smoke.example.", "record_type": "A",
         "ttl": 300, "rrdatas": ["1.2.3.4"]},
        [], _FakeConnector()
    ))
    assert result["action"] == "create_dns_record"
    assert result["record_name"] == "test.smoke.example."


def test_delete_dns_record_no_creds():
    from app.connectors.executors.gcp.delete_dns_record import execute
    result = _run(execute(
        {"zone_name": "smoke-zone", "record_name": "test.smoke.example.", "record_type": "A"},
        [], _FakeConnector()
    ))
    assert result["action"] == "delete_dns_record"
    assert result["deleted"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_gcp_vwx_executors.py::test_create_dns_zone_no_creds -v
```

Expected: `ModuleNotFoundError: No module named 'app.connectors.executors.gcp.create_dns_zone'`

- [ ] **Step 3: Create `create_dns_zone.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name = parameters["zone_name"]
    dns_name = parameters["dns_name"]
    description = parameters.get("description", "Nexplane managed zone")
    if not creds:
        return {"action": "create_dns_zone", "zone_name": zone_name, "dns_name": dns_name}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _create():
        from googleapiclient.discovery import build
        svc = build("dns", "v1", credentials=credentials)
        zone = svc.managedZones().create(
            project=project,
            body={
                "name": zone_name,
                "dnsName": dns_name,
                "description": description,
                "visibility": "public",
            },
        ).execute()
        return zone["name"], zone["dnsName"]

    name, created_dns_name = await loop.run_in_executor(None, _create)
    return {"action": "create_dns_zone", "zone_name": name, "dns_name": created_dns_name}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_dns_zone import execute as delete
    zone_name = execution_result.get("zone_name", parameters.get("zone_name"))
    return await delete({"zone_name": zone_name}, [], connector)
```

- [ ] **Step 4: Create `delete_dns_zone.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name = parameters["zone_name"]
    if not creds:
        return {"action": "delete_dns_zone", "zone_name": zone_name, "deleted": True}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _delete():
        from googleapiclient.discovery import build
        svc = build("dns", "v1", credentials=credentials)
        # Delete all record sets except SOA and NS before deleting zone
        page_token = None
        while True:
            kwargs = {"project": project, "managedZone": zone_name}
            if page_token:
                kwargs["pageToken"] = page_token
            resp = svc.resourceRecordSets().list(**kwargs).execute()
            records = [r for r in resp.get("rrsets", []) if r["type"] not in ("SOA", "NS")]
            if records:
                changes = {"deletions": records}
                svc.changes().create(project=project, managedZone=zone_name, body=changes).execute()
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        svc.managedZones().delete(project=project, managedZone=zone_name).execute()

    await loop.run_in_executor(None, _delete)
    return {"action": "delete_dns_zone", "zone_name": zone_name, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "DNS zone deletion is irreversible"}
```

- [ ] **Step 5: Create `create_dns_record.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name = parameters["zone_name"]
    record_name = parameters["record_name"]
    record_type = parameters.get("record_type", "A")
    ttl = parameters.get("ttl", 300)
    rrdatas = parameters["rrdatas"]
    if not creds:
        return {"action": "create_dns_record", "zone_name": zone_name, "record_name": record_name}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _create():
        from googleapiclient.discovery import build
        svc = build("dns", "v1", credentials=credentials)
        change = {
            "additions": [{"name": record_name, "type": record_type, "ttl": ttl, "rrdatas": rrdatas}]
        }
        svc.changes().create(project=project, managedZone=zone_name, body=change).execute()

    await loop.run_in_executor(None, _create)
    return {
        "action": "create_dns_record",
        "zone_name": zone_name,
        "record_name": record_name,
        "record_type": record_type,
        "ttl": ttl,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_dns_record import execute as delete
    return await delete(
        {
            "zone_name": execution_result.get("zone_name", parameters["zone_name"]),
            "record_name": execution_result.get("record_name", parameters["record_name"]),
            "record_type": execution_result.get("record_type", parameters.get("record_type", "A")),
            "rrdatas": parameters.get("rrdatas", []),
        },
        [], connector,
    )
```

- [ ] **Step 6: Create `delete_dns_record.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    zone_name = parameters["zone_name"]
    record_name = parameters["record_name"]
    record_type = parameters.get("record_type", "A")
    rrdatas = parameters.get("rrdatas", [])
    if not creds:
        return {"action": "delete_dns_record", "zone_name": zone_name, "record_name": record_name, "deleted": True}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _delete():
        from googleapiclient.discovery import build
        svc = build("dns", "v1", credentials=credentials)
        # Fetch current TTL + rrdatas if not provided
        actual_rrdatas = rrdatas
        ttl = 300
        if not actual_rrdatas:
            records_resp = svc.resourceRecordSets().list(
                project=project, managedZone=zone_name
            ).execute()
            for r in records_resp.get("rrsets", []):
                if r["name"] == record_name and r["type"] == record_type:
                    actual_rrdatas = r["rrdatas"]
                    ttl = r["ttl"]
                    break
        if not actual_rrdatas:
            return  # Record not found — already gone
        change = {
            "deletions": [{"name": record_name, "type": record_type, "ttl": ttl, "rrdatas": actual_rrdatas}]
        }
        svc.changes().create(project=project, managedZone=zone_name, body=change).execute()

    await loop.run_in_executor(None, _delete)
    return {"action": "delete_dns_record", "zone_name": zone_name, "record_name": record_name, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "DNS record deletion is irreversible"}
```

- [ ] **Step 7: Add 4 action entries to `gcp.json`**

Append to the `"actions"` array in `backend/app/connectors/catalog/gcp.json`:

```json
    {"action_id": "create_dns_zone", "generic_action": "create_dns_zone", "action_type": "change", "execution_tier": 2, "display_name": "Create Cloud DNS Zone", "description": "Create a managed Cloud DNS zone. Rollback: delete zone.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "zone_name", "type": "string", "required": true}, {"name": "dns_name", "type": "string", "required": true, "description": "Must end with a dot, e.g. example.com."}, {"name": "description", "type": "string", "required": false}], "executor": "gcp.create_dns_zone", "rollback_action": "delete_dns_zone", "estimated_duration_seconds": 15},
    {"action_id": "delete_dns_zone", "generic_action": "delete_dns_zone", "action_type": "change", "execution_tier": 2, "display_name": "Delete Cloud DNS Zone", "description": "Delete a managed Cloud DNS zone (clears records first).", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "zone_name", "type": "string", "required": true}], "executor": "gcp.delete_dns_zone", "estimated_duration_seconds": 20, "blast_radius_hint": "destructive"},
    {"action_id": "create_dns_record", "generic_action": "create_dns_record", "action_type": "change", "execution_tier": 2, "display_name": "Create Cloud DNS Record", "description": "Add a resource record set to a managed zone. Rollback: delete record.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "zone_name", "type": "string", "required": true}, {"name": "record_name", "type": "string", "required": true}, {"name": "record_type", "type": "string", "required": false, "default": "A"}, {"name": "ttl", "type": "integer", "required": false, "default": 300}, {"name": "rrdatas", "type": "array", "required": true}], "executor": "gcp.create_dns_record", "rollback_action": "delete_dns_record", "estimated_duration_seconds": 15},
    {"action_id": "delete_dns_record", "generic_action": "delete_dns_record", "action_type": "change", "execution_tier": 2, "display_name": "Delete Cloud DNS Record", "description": "Remove a resource record set from a managed zone.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "zone_name", "type": "string", "required": true}, {"name": "record_name", "type": "string", "required": true}, {"name": "record_type", "type": "string", "required": false, "default": "A"}, {"name": "rrdatas", "type": "array", "required": false}], "executor": "gcp.delete_dns_record", "estimated_duration_seconds": 15}
```

- [ ] **Step 8: Create 4 change-type definition JSONs**

`backend/app/connectors/change_type_definitions/gcp_dns_zone_create.json`:
```json
{
  "change_type": "gcp_dns_zone_create",
  "display_name": "Create Cloud DNS Zone",
  "steps": [{"generic_action": "create_dns_zone", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_dns_zone",
  "rollback_connector_type": "gcp"
}
```

`backend/app/connectors/change_type_definitions/gcp_dns_zone_delete.json`:
```json
{
  "change_type": "gcp_dns_zone_delete",
  "display_name": "Delete Cloud DNS Zone",
  "steps": [{"generic_action": "delete_dns_zone", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_connector_type": "gcp"
}
```

`backend/app/connectors/change_type_definitions/gcp_dns_record_create.json`:
```json
{
  "change_type": "gcp_dns_record_create",
  "display_name": "Create Cloud DNS Record",
  "steps": [{"generic_action": "create_dns_record", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_dns_record",
  "rollback_connector_type": "gcp"
}
```

`backend/app/connectors/change_type_definitions/gcp_dns_record_delete.json`:
```json
{
  "change_type": "gcp_dns_record_delete",
  "display_name": "Delete Cloud DNS Record",
  "steps": [{"generic_action": "delete_dns_record", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_connector_type": "gcp"
}
```

- [ ] **Step 9: Add ChangeType enum values**

In `backend/app/models/change_request.py`, add after `gcp_iam_binding_remove` (or the last GCP entry):

```python
    # GCP Cloud DNS lifecycle
    gcp_dns_zone_create = "gcp_dns_zone_create"
    gcp_dns_zone_delete = "gcp_dns_zone_delete"
    gcp_dns_record_create = "gcp_dns_record_create"
    gcp_dns_record_delete = "gcp_dns_record_delete"
```

- [ ] **Step 10: Add frontend type values**

In `frontend/src/types/api.ts`, add to the `ChangeType` union:
`"gcp_dns_zone_create" | "gcp_dns_zone_delete" | "gcp_dns_record_create" | "gcp_dns_record_delete"`

- [ ] **Step 11: Run unit tests**

```bash
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_gcp_vwx_executors.py -v
```

Expected: all 4 DNS tests pass.

- [ ] **Step 12: Commit**

```bash
git add backend/app/connectors/executors/gcp/create_dns_zone.py \
        backend/app/connectors/executors/gcp/delete_dns_zone.py \
        backend/app/connectors/executors/gcp/create_dns_record.py \
        backend/app/connectors/executors/gcp/delete_dns_record.py \
        backend/app/connectors/catalog/gcp.json \
        backend/app/connectors/change_type_definitions/gcp_dns_zone_create.json \
        backend/app/connectors/change_type_definitions/gcp_dns_zone_delete.json \
        backend/app/connectors/change_type_definitions/gcp_dns_record_create.json \
        backend/app/connectors/change_type_definitions/gcp_dns_record_delete.json \
        backend/app/models/change_request.py \
        frontend/src/types/api.ts \
        backend/app/tests/test_gcp_vwx_executors.py
git commit -m "feat(gcp): add Cloud DNS zone and record executors"
git push
```

---

## Task 2: Phase V — Cloud DNS Smoke

**Files:**
- Modify: `backend/tests/smoke/test_gcp_live.py`

**Interfaces:**
- Consumes: `gcp_dns_zone_create`, `gcp_dns_zone_delete`, `gcp_dns_record_create`, `gcp_dns_record_delete` (Task 1)
- Produces: `run_phase_v(client, cloud_account_id, gcp_project)` replacing `run_phase_v_stub`

- [ ] **Step 1: Pull on EC2 and restart backend**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "export HOME=/home/ec2-user && cd /home/ec2-user/nexplane && git pull && docker compose restart backend"
sleep 10
```

- [ ] **Step 2: Replace the stub with the real implementation**

In `backend/tests/smoke/test_gcp_live.py`, replace `run_phase_v_stub` and update main():

```python
def run_phase_v(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase V: Cloud DNS — managed zone + A record lifecycle."""
    print("\n[Phase V] Cloud DNS")

    import time as _time
    ts = int(_time.time())
    zone_name = f"nexplane-smoke-v-{ts}"
    dns_name = f"nexplane-smoke-v-{ts}.example."
    record_name = f"test.nexplane-smoke-v-{ts}.example."
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create DNS zone via CR
        cr = client.run_cr(
            "[Phase V] create DNS zone", "gcp_dns_zone_create", cloud_account_id,
            {
                "zone_name": zone_name,
                "dns_name": dns_name,
                "description": "Nexplane smoke test zone — safe to delete",
            },
        )
        rollback_stack.append((cr["id"], "gcp_dns_zone_create"))
        log(f"DNS zone created: {zone_name} ({dns_name})")

        # 2. Create A record via CR
        cr2 = client.run_cr(
            "[Phase V] create A record", "gcp_dns_record_create", cloud_account_id,
            {
                "zone_name": zone_name,
                "record_name": record_name,
                "record_type": "A",
                "ttl": 300,
                "rrdatas": ["1.2.3.4"],
            },
        )
        rollback_stack.append((cr2["id"], "gcp_dns_record_create"))
        log(f"A record created: {record_name} → 1.2.3.4")

        # 3. Verify via SDK
        creds = _get_gcp_creds()
        if creds:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from googleapiclient.discovery import build as _build
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                dns_svc = _build("dns", "v1", credentials=gc)
                zone_obj = dns_svc.managedZones().get(
                    project=gcp_project, managedZone=zone_name
                ).execute()
                assert zone_obj["name"] == zone_name, "Zone name mismatch"
                records_resp = dns_svc.resourceRecordSets().list(
                    project=gcp_project, managedZone=zone_name
                ).execute()
                found = any(
                    r["name"] == record_name and r["type"] == "A"
                    for r in records_resp.get("rrsets", [])
                )
                assert found, f"A record {record_name} not found in zone"
                log("Zone and A record verified via SDK")
            except Exception as e:
                print(f"  ⚠️  SDK verification error: {e}")
                raise

        # 4. Rollback (LIFO): delete record, then delete zone
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        rollback_stack.clear()
        log("Phase V complete")

    except Exception as e:
        print(f"\n❌ Phase V failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete zone via SDK (clears records first)
        creds = _get_gcp_creds()
        if creds and gcp_project:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from googleapiclient.discovery import build as _build
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                dns_svc = _build("dns", "v1", credentials=gc)
                # Clear non-SOA/NS records
                try:
                    resp = dns_svc.resourceRecordSets().list(
                        project=gcp_project, managedZone=zone_name
                    ).execute()
                    to_delete = [r for r in resp.get("rrsets", []) if r["type"] not in ("SOA", "NS")]
                    if to_delete:
                        dns_svc.changes().create(
                            project=gcp_project, managedZone=zone_name,
                            body={"deletions": to_delete}
                        ).execute()
                    dns_svc.managedZones().delete(
                        project=gcp_project, managedZone=zone_name
                    ).execute()
                    print(f"  Safety net: deleted DNS zone {zone_name}")
                except Exception:
                    pass
            except Exception as e2:
                print(f"  ⚠️  Safety net DNS zone delete failed: {e2}")
```

Update main():
```python
        if "V" in phases:
            run_phase_v(client, cloud_account_id, args.gcp_project)
```

- [ ] **Step 3: Commit and push**

```bash
git add backend/tests/smoke/test_gcp_live.py
git commit -m "feat(smoke): implement phase V Cloud DNS"
git push
```

- [ ] **Step 4: Pull on EC2, restart, run Phase V smoke**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "export HOME=/home/ec2-user && cd /home/ec2-user/nexplane && git pull && docker compose restart backend"
sleep 10

ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane/backend/tests/smoke && python test_gcp_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme \
  --phases V \
  --gcp-project YOUR_GCP_PROJECT_ID"
```

Expected output:
```
[Phase V] Cloud DNS
  ✓ DNS zone created: nexplane-smoke-v-... (nexplane-smoke-v-....example.)
  ✓ A record created: test.nexplane-smoke-v-....example. → 1.2.3.4
  ✓ Zone and A record verified via SDK
✅ ALL SELECTED PHASES PASSED
```

---

## Task 3: Cloud SQL Executors + Catalog + ChangeType

**Files:**
- Create: `backend/app/connectors/executors/gcp/create_cloudsql_instance.py`
- Create: `backend/app/connectors/executors/gcp/delete_cloudsql_instance.py`
- Create: `backend/app/connectors/executors/gcp/create_cloudsql_backup.py`
- Modify: `backend/app/connectors/catalog/gcp.json` (add 3 action entries)
- Create: `backend/app/connectors/change_type_definitions/gcp_cloudsql_instance_create.json`
- Create: `backend/app/connectors/change_type_definitions/gcp_cloudsql_instance_delete.json`
- Create: `backend/app/connectors/change_type_definitions/gcp_cloudsql_backup_create.json`
- Modify: `backend/app/models/change_request.py` (add 3 ChangeType enum values)
- Modify: `frontend/src/types/api.ts` (add 3 ChangeType union values)
- Modify: `backend/app/tests/test_gcp_vwx_executors.py` (add 3 unit tests)

**Interfaces:**
- Produces: `create_cloudsql_instance.execute({"instance_name": str, "database_version": str, "tier": str, "region": str}, [], connector) -> {"action": "create_cloudsql_instance", "instance_name": str, "connection_name": str}`
- Produces: `delete_cloudsql_instance.execute({"instance_name": str}, [], connector) -> {"action": "delete_cloudsql_instance", "instance_name": str, "deleted": bool}`
- Produces: `create_cloudsql_backup.execute({"instance_name": str}, [], connector) -> {"action": "create_cloudsql_backup", "instance_name": str, "backup_run_id": int}`

- [ ] **Step 1: Add unit tests for Cloud SQL executors**

Append to `backend/app/tests/test_gcp_vwx_executors.py`:

```python
# --- Cloud SQL ---

def test_create_cloudsql_instance_no_creds():
    from app.connectors.executors.gcp.create_cloudsql_instance import execute
    result = _run(execute(
        {"instance_name": "smoke-sql", "database_version": "POSTGRES_14",
         "tier": "db-f1-micro", "region": "us-central1"},
        [], _FakeConnector()
    ))
    assert result["action"] == "create_cloudsql_instance"
    assert result["instance_name"] == "smoke-sql"


def test_delete_cloudsql_instance_no_creds():
    from app.connectors.executors.gcp.delete_cloudsql_instance import execute
    result = _run(execute({"instance_name": "smoke-sql"}, [], _FakeConnector()))
    assert result["action"] == "delete_cloudsql_instance"
    assert result["deleted"] is True


def test_create_cloudsql_backup_no_creds():
    from app.connectors.executors.gcp.create_cloudsql_backup import execute
    result = _run(execute({"instance_name": "smoke-sql"}, [], _FakeConnector()))
    assert result["action"] == "create_cloudsql_backup"
    assert result["instance_name"] == "smoke-sql"
```

- [ ] **Step 2: Create `create_cloudsql_instance.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    database_version = parameters.get("database_version", "POSTGRES_14")
    tier = parameters.get("tier", "db-f1-micro")
    region = parameters.get("region", "us-central1")
    if not creds:
        return {
            "action": "create_cloudsql_instance",
            "instance_name": instance_name,
            "connection_name": f"mock-project:us-central1:{instance_name}",
        }
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _create_and_wait():
        from googleapiclient.discovery import build
        svc = build("sqladmin", "v1beta4", credentials=credentials)
        body = {
            "name": instance_name,
            "databaseVersion": database_version,
            "region": region,
            "settings": {
                "tier": tier,
                "backupConfiguration": {"enabled": True},
            },
        }
        op = svc.instances().insert(project=project, body=body).execute()
        op_name = op["name"]
        deadline = time.time() + 900
        while time.time() < deadline:
            time.sleep(15)
            op_status = svc.operations().get(project=project, operation=op_name).execute()
            if op_status["status"] == "DONE":
                if "error" in op_status:
                    raise RuntimeError(f"Cloud SQL create failed: {op_status['error']}")
                break
        else:
            raise TimeoutError(f"Cloud SQL instance {instance_name} did not become DONE within 900s")
        instance = svc.instances().get(project=project, instance=instance_name).execute()
        return instance.get("connectionName", f"{project}:{region}:{instance_name}")

    connection_name = await loop.run_in_executor(None, _create_and_wait)
    return {
        "action": "create_cloudsql_instance",
        "instance_name": instance_name,
        "connection_name": connection_name,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_cloudsql_instance import execute as delete
    instance_name = execution_result.get("instance_name", parameters.get("instance_name"))
    return await delete({"instance_name": instance_name}, [], connector)
```

- [ ] **Step 3: Create `delete_cloudsql_instance.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    if not creds:
        return {"action": "delete_cloudsql_instance", "instance_name": instance_name, "deleted": True}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _delete_and_wait():
        from googleapiclient.discovery import build
        svc = build("sqladmin", "v1beta4", credentials=credentials)
        op = svc.instances().delete(project=project, instance=instance_name).execute()
        op_name = op["name"]
        deadline = time.time() + 600
        while time.time() < deadline:
            time.sleep(15)
            op_status = svc.operations().get(project=project, operation=op_name).execute()
            if op_status["status"] == "DONE":
                if "error" in op_status:
                    raise RuntimeError(f"Cloud SQL delete failed: {op_status['error']}")
                break
        else:
            raise TimeoutError(f"Cloud SQL delete for {instance_name} did not complete within 600s")

    await loop.run_in_executor(None, _delete_and_wait)
    return {"action": "delete_cloudsql_instance", "instance_name": instance_name, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "Cloud SQL instance deletion is irreversible"}
```

- [ ] **Step 4: Create `create_cloudsql_backup.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import time


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_name = parameters["instance_name"]
    if not creds:
        return {"action": "create_cloudsql_backup", "instance_name": instance_name, "backup_run_id": 0}
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _backup_and_wait():
        from googleapiclient.discovery import build
        svc = build("sqladmin", "v1beta4", credentials=credentials)
        op = svc.backupRuns().insert(project=project, instance=instance_name, body={}).execute()
        op_name = op["name"]
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(10)
            op_status = svc.operations().get(project=project, operation=op_name).execute()
            if op_status["status"] == "DONE":
                if "error" in op_status:
                    raise RuntimeError(f"Cloud SQL backup failed: {op_status['error']}")
                break
        else:
            raise TimeoutError(f"Cloud SQL backup for {instance_name} did not complete within 300s")
        # Fetch the most recent backup run ID
        runs = svc.backupRuns().list(project=project, instance=instance_name).execute()
        items = runs.get("items", [])
        backup_run_id = items[0]["id"] if items else 0
        return backup_run_id

    backup_run_id = await loop.run_in_executor(None, _backup_and_wait)
    return {
        "action": "create_cloudsql_backup",
        "instance_name": instance_name,
        "backup_run_id": backup_run_id,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "backup is cleaned up automatically when instance is deleted"}
```

- [ ] **Step 5: Add 3 action entries to `gcp.json`**

Append to the `"actions"` array in `backend/app/connectors/catalog/gcp.json`:

```json
    {"action_id": "create_cloudsql_instance", "generic_action": "create_cloudsql_instance", "action_type": "change", "execution_tier": 3, "display_name": "Create Cloud SQL Instance", "description": "Provision a Cloud SQL instance (5-10 min). Rollback: delete instance.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "instance_name", "type": "string", "required": true}, {"name": "database_version", "type": "string", "required": false, "default": "POSTGRES_14"}, {"name": "tier", "type": "string", "required": false, "default": "db-f1-micro"}, {"name": "region", "type": "string", "required": false, "default": "us-central1"}], "executor": "gcp.create_cloudsql_instance", "rollback_action": "delete_cloudsql_instance", "estimated_duration_seconds": 600},
    {"action_id": "delete_cloudsql_instance", "generic_action": "delete_cloudsql_instance", "action_type": "change", "execution_tier": 3, "display_name": "Delete Cloud SQL Instance", "description": "Delete a Cloud SQL instance and all its data.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "instance_name", "type": "string", "required": true}], "executor": "gcp.delete_cloudsql_instance", "estimated_duration_seconds": 300, "blast_radius_hint": "destructive"},
    {"action_id": "create_cloudsql_backup", "generic_action": "create_cloudsql_backup", "action_type": "change", "execution_tier": 2, "display_name": "Create Cloud SQL Backup", "description": "Trigger an on-demand Cloud SQL backup run.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "instance_name", "type": "string", "required": true}], "executor": "gcp.create_cloudsql_backup", "estimated_duration_seconds": 120}
```

- [ ] **Step 6: Create 3 change-type definition JSONs**

`backend/app/connectors/change_type_definitions/gcp_cloudsql_instance_create.json`:
```json
{
  "change_type": "gcp_cloudsql_instance_create",
  "display_name": "Create Cloud SQL Instance",
  "steps": [{"generic_action": "create_cloudsql_instance", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_cloudsql_instance",
  "rollback_connector_type": "gcp"
}
```

`backend/app/connectors/change_type_definitions/gcp_cloudsql_instance_delete.json`:
```json
{
  "change_type": "gcp_cloudsql_instance_delete",
  "display_name": "Delete Cloud SQL Instance",
  "steps": [{"generic_action": "delete_cloudsql_instance", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_connector_type": "gcp"
}
```

`backend/app/connectors/change_type_definitions/gcp_cloudsql_backup_create.json`:
```json
{
  "change_type": "gcp_cloudsql_backup_create",
  "display_name": "Create Cloud SQL Backup",
  "steps": [{"generic_action": "create_cloudsql_backup", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_connector_type": "gcp"
}
```

- [ ] **Step 7: Add ChangeType enum values**

In `backend/app/models/change_request.py`, add after `gcp_dns_record_delete`:

```python
    # GCP Cloud SQL lifecycle
    gcp_cloudsql_instance_create = "gcp_cloudsql_instance_create"
    gcp_cloudsql_instance_delete = "gcp_cloudsql_instance_delete"
    gcp_cloudsql_backup_create = "gcp_cloudsql_backup_create"
```

- [ ] **Step 8: Add frontend type values**

In `frontend/src/types/api.ts`, add to the `ChangeType` union:
`"gcp_cloudsql_instance_create" | "gcp_cloudsql_instance_delete" | "gcp_cloudsql_backup_create"`

- [ ] **Step 9: Run unit tests**

```bash
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_gcp_vwx_executors.py -v
```

Expected: all 7 tests pass (4 DNS + 3 Cloud SQL).

- [ ] **Step 10: Commit**

```bash
git add backend/app/connectors/executors/gcp/create_cloudsql_instance.py \
        backend/app/connectors/executors/gcp/delete_cloudsql_instance.py \
        backend/app/connectors/executors/gcp/create_cloudsql_backup.py \
        backend/app/connectors/catalog/gcp.json \
        backend/app/connectors/change_type_definitions/gcp_cloudsql_instance_create.json \
        backend/app/connectors/change_type_definitions/gcp_cloudsql_instance_delete.json \
        backend/app/connectors/change_type_definitions/gcp_cloudsql_backup_create.json \
        backend/app/models/change_request.py \
        frontend/src/types/api.ts \
        backend/app/tests/test_gcp_vwx_executors.py
git commit -m "feat(gcp): add Cloud SQL instance and backup executors"
git push
```

---

## Task 4: Phase W — Cloud SQL Smoke

**Files:**
- Modify: `backend/tests/smoke/test_gcp_live.py`

**Interfaces:**
- Consumes: `gcp_cloudsql_instance_create`, `gcp_cloudsql_instance_delete`, `gcp_cloudsql_backup_create` (Task 3)
- Produces: `run_phase_w(client, cloud_account_id, gcp_project)` replacing `run_phase_w_stub`

- [ ] **Step 1: Pull on EC2 and restart backend**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "export HOME=/home/ec2-user && cd /home/ec2-user/nexplane && git pull && docker compose restart backend"
sleep 10
```

- [ ] **Step 2: Replace the stub with the real implementation**

In `backend/tests/smoke/test_gcp_live.py`, replace `run_phase_w_stub` and update main():

```python
def run_phase_w(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase W: Cloud SQL — instance create + backup + rollback. (~10 min)"""
    print("\n[Phase W] Cloud SQL Instance + Backup")
    print("  ⚠️  This phase takes 5-10 min for Cloud SQL provisioning")

    import time as _time
    ts = int(_time.time()) % 10000
    instance_name = f"nexplane-smoke-w-{ts}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create Cloud SQL instance via CR (polls internally up to 900s)
        cr = client.run_cr(
            "[Phase W] create Cloud SQL instance", "gcp_cloudsql_instance_create", cloud_account_id,
            {
                "instance_name": instance_name,
                "database_version": "POSTGRES_14",
                "tier": "db-f1-micro",
                "region": "us-central1",
            },
        )
        rollback_stack.append((cr["id"], "gcp_cloudsql_instance_create"))
        log(f"Cloud SQL instance created: {instance_name}")

        # 2. Create backup via CR
        cr2 = client.run_cr(
            "[Phase W] create Cloud SQL backup", "gcp_cloudsql_backup_create", cloud_account_id,
            {"instance_name": instance_name},
        )
        rollback_stack.append((cr2["id"], "gcp_cloudsql_backup_create"))
        log(f"Cloud SQL backup created for {instance_name}")

        # 3. Verify via SDK
        creds = _get_gcp_creds()
        if creds:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from googleapiclient.discovery import build as _build
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                svc = _build("sqladmin", "v1beta4", credentials=gc)
                instance_obj = svc.instances().get(
                    project=gcp_project, instance=instance_name
                ).execute()
                assert instance_obj["state"] == "RUNNABLE", f"Instance state: {instance_obj['state']}"
                runs = svc.backupRuns().list(
                    project=gcp_project, instance=instance_name
                ).execute()
                items = runs.get("items", [])
                successful = [r for r in items if r.get("status") == "SUCCESSFUL"]
                assert successful, "No successful backup run found"
                log(f"Instance RUNNABLE + backup SUCCESSFUL (SDK verified)")
            except Exception as e:
                print(f"  ⚠️  SDK verification error: {e}")
                raise

        # 4. Rollback (LIFO): backup rollback is no-op, then delete instance
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        rollback_stack.clear()
        log("Phase W complete")

    except Exception as e:
        print(f"\n❌ Phase W failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete instance via SDK
        creds = _get_gcp_creds()
        if creds and gcp_project:
            try:
                import json as _json, time as _time2
                from google.oauth2 import service_account as _sa
                from googleapiclient.discovery import build as _build
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                svc = _build("sqladmin", "v1beta4", credentials=gc)
                try:
                    op = svc.instances().delete(project=gcp_project, instance=instance_name).execute()
                    print(f"  Safety net: initiated Cloud SQL delete for {instance_name}")
                except Exception:
                    pass
            except Exception as e2:
                print(f"  ⚠️  Safety net Cloud SQL delete failed: {e2}")
```

Update main():
```python
        if "W" in phases:
            run_phase_w(client, cloud_account_id, args.gcp_project)
```

- [ ] **Step 3: Commit and push**

```bash
git add backend/tests/smoke/test_gcp_live.py
git commit -m "feat(smoke): implement phase W Cloud SQL instance + backup"
git push
```

- [ ] **Step 4: Pull on EC2, restart, run Phase W smoke**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "export HOME=/home/ec2-user && cd /home/ec2-user/nexplane && git pull && docker compose restart backend"
sleep 10

ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane/backend/tests/smoke && python test_gcp_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme \
  --phases W \
  --gcp-project YOUR_GCP_PROJECT_ID"
```

Expected output (may take 10+ min):
```
[Phase W] Cloud SQL Instance + Backup
  ⚠️  This phase takes 5-10 min for Cloud SQL provisioning
  ✓ Cloud SQL instance created: nexplane-smoke-w-...
  ✓ Cloud SQL backup created for nexplane-smoke-w-...
  ✓ Instance RUNNABLE + backup SUCCESSFUL (SDK verified)
✅ ALL SELECTED PHASES PASSED
```

---

## Task 5: Cloud Monitoring Executors + Catalog + ChangeType

**Files:**
- Create: `backend/app/connectors/executors/gcp/create_alert_policy.py`
- Create: `backend/app/connectors/executors/gcp/delete_alert_policy.py`
- Create: `backend/app/connectors/executors/gcp/create_uptime_check.py`
- Create: `backend/app/connectors/executors/gcp/delete_uptime_check.py`
- Modify: `backend/app/connectors/catalog/gcp.json` (add 4 action entries)
- Create: `backend/app/connectors/change_type_definitions/gcp_alert_policy_create.json`
- Create: `backend/app/connectors/change_type_definitions/gcp_alert_policy_delete.json`
- Create: `backend/app/connectors/change_type_definitions/gcp_uptime_check_create.json`
- Create: `backend/app/connectors/change_type_definitions/gcp_uptime_check_delete.json`
- Modify: `backend/app/models/change_request.py` (add 4 ChangeType enum values)
- Modify: `frontend/src/types/api.ts` (add 4 ChangeType union values)
- Modify: `backend/app/tests/test_gcp_vwx_executors.py` (add 4 unit tests)

**Interfaces:**
- Produces: `create_alert_policy.execute({"display_name": str, "condition_threshold": float, "duration_seconds": int}, [], connector) -> {"action": "create_alert_policy", "policy_name": str, "display_name": str}`
- Produces: `delete_alert_policy.execute({"policy_name": str}, [], connector) -> {"action": "delete_alert_policy", "policy_name": str, "deleted": bool}`
- Produces: `create_uptime_check.execute({"display_name": str, "host": str, "path": str, "period_seconds": int}, [], connector) -> {"action": "create_uptime_check", "check_id": str, "display_name": str}`
- Produces: `delete_uptime_check.execute({"check_id": str}, [], connector) -> {"action": "delete_uptime_check", "check_id": str, "deleted": bool}`

- [ ] **Step 1: Add unit tests for monitoring executors**

Append to `backend/app/tests/test_gcp_vwx_executors.py`:

```python
# --- Cloud Monitoring ---

def test_create_alert_policy_no_creds():
    from app.connectors.executors.gcp.create_alert_policy import execute
    result = _run(execute(
        {"display_name": "smoke-alert", "condition_threshold": 0.9, "duration_seconds": 60},
        [], _FakeConnector()
    ))
    assert result["action"] == "create_alert_policy"
    assert result["display_name"] == "smoke-alert"


def test_delete_alert_policy_no_creds():
    from app.connectors.executors.gcp.delete_alert_policy import execute
    result = _run(execute(
        {"policy_name": "projects/p/alertPolicies/123"},
        [], _FakeConnector()
    ))
    assert result["action"] == "delete_alert_policy"
    assert result["deleted"] is True


def test_create_uptime_check_no_creds():
    from app.connectors.executors.gcp.create_uptime_check import execute
    result = _run(execute(
        {"display_name": "smoke-uptime", "host": "google.com", "path": "/", "period_seconds": 60},
        [], _FakeConnector()
    ))
    assert result["action"] == "create_uptime_check"
    assert result["display_name"] == "smoke-uptime"


def test_delete_uptime_check_no_creds():
    from app.connectors.executors.gcp.delete_uptime_check import execute
    result = _run(execute(
        {"check_id": "projects/p/uptimeCheckConfigs/abc"},
        [], _FakeConnector()
    ))
    assert result["action"] == "delete_uptime_check"
    assert result["deleted"] is True
```

- [ ] **Step 2: Create `create_alert_policy.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    display_name = parameters["display_name"]
    condition_threshold = float(parameters.get("condition_threshold", 0.9))
    duration_seconds = int(parameters.get("duration_seconds", 60))
    if not creds:
        return {
            "action": "create_alert_policy",
            "policy_name": "projects/mock/alertPolicies/mock",
            "display_name": display_name,
        }
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _create():
        from google.cloud import monitoring_v3
        client = monitoring_v3.AlertPolicyServiceClient(credentials=credentials)
        policy = monitoring_v3.AlertPolicy(
            display_name=display_name,
            conditions=[
                monitoring_v3.AlertPolicy.Condition(
                    display_name="CPU utilization high",
                    condition_threshold=monitoring_v3.AlertPolicy.Condition.MetricThreshold(
                        filter='resource.type = "gce_instance" AND metric.type = "compute.googleapis.com/instance/cpu/utilization"',
                        comparison=monitoring_v3.ComparisonType.COMPARISON_GT,
                        threshold_value=condition_threshold,
                        duration={"seconds": duration_seconds},
                        aggregations=[
                            monitoring_v3.Aggregation(
                                alignment_period={"seconds": 60},
                                per_series_aligner=monitoring_v3.Aggregation.Aligner.ALIGN_MEAN,
                            )
                        ],
                    ),
                )
            ],
            combiner=monitoring_v3.AlertPolicy.ConditionCombinerType.AND,
            enabled=True,
        )
        result = client.create_alert_policy(
            name=f"projects/{project}", alert_policy=policy
        )
        return result.name

    policy_name = await loop.run_in_executor(None, _create)
    return {"action": "create_alert_policy", "policy_name": policy_name, "display_name": display_name}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_alert_policy import execute as delete
    policy_name = execution_result.get("policy_name", "")
    if not policy_name:
        return {"rolled_back": False, "reason": "no policy_name in execution_result"}
    return await delete({"policy_name": policy_name}, [], connector)
```

- [ ] **Step 3: Create `delete_alert_policy.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    policy_name = parameters["policy_name"]
    if not creds:
        return {"action": "delete_alert_policy", "policy_name": policy_name, "deleted": True}
    from ._client import get_credentials
    credentials = get_credentials(creds)
    loop = asyncio.get_event_loop()

    def _delete():
        from google.cloud import monitoring_v3
        client = monitoring_v3.AlertPolicyServiceClient(credentials=credentials)
        client.delete_alert_policy(name=policy_name)

    await loop.run_in_executor(None, _delete)
    return {"action": "delete_alert_policy", "policy_name": policy_name, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "alert policy deletion is irreversible"}
```

- [ ] **Step 4: Create `create_uptime_check.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    display_name = parameters["display_name"]
    host = parameters.get("host", "google.com")
    path = parameters.get("path", "/")
    period_seconds = int(parameters.get("period_seconds", 60))
    if not creds:
        return {
            "action": "create_uptime_check",
            "check_id": "projects/mock/uptimeCheckConfigs/mock",
            "display_name": display_name,
        }
    from ._client import get_credentials, get_project_id
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()

    def _create():
        from google.cloud import monitoring_v3
        from google.protobuf import duration_pb2
        client = monitoring_v3.UptimeCheckServiceClient(credentials=credentials)
        config = monitoring_v3.UptimeCheckConfig(
            display_name=display_name,
            monitored_resource=monitoring_v3.MonitoredResource(
                type="uptime_url",
                labels={"host": host},
            ),
            http_check=monitoring_v3.UptimeCheckConfig.HttpCheck(
                path=path, port=80, use_ssl=False
            ),
            period=duration_pb2.Duration(seconds=period_seconds),
            timeout=duration_pb2.Duration(seconds=10),
        )
        result = client.create_uptime_check_config(
            parent=f"projects/{project}", uptime_check_config=config
        )
        return result.name

    check_id = await loop.run_in_executor(None, _create)
    return {"action": "create_uptime_check", "check_id": check_id, "display_name": display_name}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.gcp.delete_uptime_check import execute as delete
    check_id = execution_result.get("check_id", "")
    if not check_id:
        return {"rolled_back": False, "reason": "no check_id in execution_result"}
    return await delete({"check_id": check_id}, [], connector)
```

- [ ] **Step 5: Create `delete_uptime_check.py`**

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    check_id = parameters["check_id"]
    if not creds:
        return {"action": "delete_uptime_check", "check_id": check_id, "deleted": True}
    from ._client import get_credentials
    credentials = get_credentials(creds)
    loop = asyncio.get_event_loop()

    def _delete():
        from google.cloud import monitoring_v3
        client = monitoring_v3.UptimeCheckServiceClient(credentials=credentials)
        client.delete_uptime_check_config(name=check_id)

    await loop.run_in_executor(None, _delete)
    return {"action": "delete_uptime_check", "check_id": check_id, "deleted": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "uptime check deletion is irreversible"}
```

- [ ] **Step 6: Add 4 action entries to `gcp.json`**

Append to the `"actions"` array in `backend/app/connectors/catalog/gcp.json`:

```json
    {"action_id": "create_alert_policy", "generic_action": "create_alert_policy", "action_type": "change", "execution_tier": 2, "display_name": "Create Alert Policy", "description": "Create a Cloud Monitoring alert policy. Rollback: delete policy.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "display_name", "type": "string", "required": true}, {"name": "condition_threshold", "type": "number", "required": false, "default": 0.9}, {"name": "duration_seconds", "type": "integer", "required": false, "default": 60}], "executor": "gcp.create_alert_policy", "rollback_action": "delete_alert_policy", "estimated_duration_seconds": 15},
    {"action_id": "delete_alert_policy", "generic_action": "delete_alert_policy", "action_type": "change", "execution_tier": 2, "display_name": "Delete Alert Policy", "description": "Delete a Cloud Monitoring alert policy by resource name.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "policy_name", "type": "string", "required": true, "description": "Full resource name, e.g. projects/p/alertPolicies/123"}], "executor": "gcp.delete_alert_policy", "estimated_duration_seconds": 10},
    {"action_id": "create_uptime_check", "generic_action": "create_uptime_check", "action_type": "change", "execution_tier": 2, "display_name": "Create Uptime Check", "description": "Create a Cloud Monitoring uptime check targeting an HTTP endpoint. Rollback: delete check.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "display_name", "type": "string", "required": true}, {"name": "host", "type": "string", "required": true}, {"name": "path", "type": "string", "required": false, "default": "/"}, {"name": "period_seconds", "type": "integer", "required": false, "default": 60}], "executor": "gcp.create_uptime_check", "rollback_action": "delete_uptime_check", "estimated_duration_seconds": 15},
    {"action_id": "delete_uptime_check", "generic_action": "delete_uptime_check", "action_type": "change", "execution_tier": 2, "display_name": "Delete Uptime Check", "description": "Delete a Cloud Monitoring uptime check by resource name.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "check_id", "type": "string", "required": true, "description": "Full resource name, e.g. projects/p/uptimeCheckConfigs/abc"}], "executor": "gcp.delete_uptime_check", "estimated_duration_seconds": 10}
```

- [ ] **Step 7: Create 4 change-type definition JSONs**

`backend/app/connectors/change_type_definitions/gcp_alert_policy_create.json`:
```json
{
  "change_type": "gcp_alert_policy_create",
  "display_name": "Create Cloud Monitoring Alert Policy",
  "steps": [{"generic_action": "create_alert_policy", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_alert_policy",
  "rollback_connector_type": "gcp"
}
```

`backend/app/connectors/change_type_definitions/gcp_alert_policy_delete.json`:
```json
{
  "change_type": "gcp_alert_policy_delete",
  "display_name": "Delete Cloud Monitoring Alert Policy",
  "steps": [{"generic_action": "delete_alert_policy", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_connector_type": "gcp"
}
```

`backend/app/connectors/change_type_definitions/gcp_uptime_check_create.json`:
```json
{
  "change_type": "gcp_uptime_check_create",
  "display_name": "Create Cloud Monitoring Uptime Check",
  "steps": [{"generic_action": "create_uptime_check", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_uptime_check",
  "rollback_connector_type": "gcp"
}
```

`backend/app/connectors/change_type_definitions/gcp_uptime_check_delete.json`:
```json
{
  "change_type": "gcp_uptime_check_delete",
  "display_name": "Delete Cloud Monitoring Uptime Check",
  "steps": [{"generic_action": "delete_uptime_check", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_connector_type": "gcp"
}
```

- [ ] **Step 8: Add ChangeType enum values**

In `backend/app/models/change_request.py`, add after `gcp_cloudsql_backup_create`:

```python
    # GCP Cloud Monitoring lifecycle
    gcp_alert_policy_create = "gcp_alert_policy_create"
    gcp_alert_policy_delete = "gcp_alert_policy_delete"
    gcp_uptime_check_create = "gcp_uptime_check_create"
    gcp_uptime_check_delete = "gcp_uptime_check_delete"
```

- [ ] **Step 9: Add frontend type values**

In `frontend/src/types/api.ts`, add to the `ChangeType` union:
`"gcp_alert_policy_create" | "gcp_alert_policy_delete" | "gcp_uptime_check_create" | "gcp_uptime_check_delete"`

- [ ] **Step 10: Run all unit tests**

```bash
docker exec nexplane-backend-1 python -m pytest backend/app/tests/test_gcp_vwx_executors.py -v
```

Expected: all 11 tests pass (4 DNS + 3 Cloud SQL + 4 Monitoring).

- [ ] **Step 11: Commit**

```bash
git add backend/app/connectors/executors/gcp/create_alert_policy.py \
        backend/app/connectors/executors/gcp/delete_alert_policy.py \
        backend/app/connectors/executors/gcp/create_uptime_check.py \
        backend/app/connectors/executors/gcp/delete_uptime_check.py \
        backend/app/connectors/catalog/gcp.json \
        backend/app/connectors/change_type_definitions/gcp_alert_policy_create.json \
        backend/app/connectors/change_type_definitions/gcp_alert_policy_delete.json \
        backend/app/connectors/change_type_definitions/gcp_uptime_check_create.json \
        backend/app/connectors/change_type_definitions/gcp_uptime_check_delete.json \
        backend/app/models/change_request.py \
        frontend/src/types/api.ts \
        backend/app/tests/test_gcp_vwx_executors.py
git commit -m "feat(gcp): add Cloud Monitoring alert policy and uptime check executors"
git push
```

---

## Task 6: Phase X — Cloud Monitoring Smoke

**Files:**
- Modify: `backend/tests/smoke/test_gcp_live.py`

**Interfaces:**
- Consumes: `gcp_alert_policy_create`, `gcp_alert_policy_delete`, `gcp_uptime_check_create`, `gcp_uptime_check_delete` (Task 5)
- Produces: `run_phase_x(client, cloud_account_id, gcp_project)` replacing `run_phase_x_stub`

- [ ] **Step 1: Pull on EC2 and restart backend**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "export HOME=/home/ec2-user && cd /home/ec2-user/nexplane && git pull && docker compose restart backend"
sleep 10
```

- [ ] **Step 2: Replace the stub with the real implementation**

In `backend/tests/smoke/test_gcp_live.py`, replace `run_phase_x_stub` and update main():

```python
def run_phase_x(client: NexplaneClient, cloud_account_id: str, gcp_project: str) -> None:
    """Phase X: Cloud Monitoring — alert policy + uptime check lifecycle."""
    print("\n[Phase X] Cloud Monitoring")

    import time as _time
    ts = int(_time.time())
    alert_display = f"nexplane-smoke-x-alert-{ts}"
    uptime_display = f"nexplane-smoke-x-uptime-{ts}"
    rollback_stack: list[tuple[str, str]] = []

    try:
        # 1. Create alert policy via CR
        cr = client.run_cr(
            "[Phase X] create alert policy", "gcp_alert_policy_create", cloud_account_id,
            {
                "display_name": alert_display,
                "condition_threshold": 0.9,
                "duration_seconds": 60,
            },
        )
        rollback_stack.append((cr["id"], "gcp_alert_policy_create"))
        policy_name = cr.get("execution_result", {}).get("policy_name", "")
        log(f"Alert policy created: {alert_display}")

        # 2. Create uptime check via CR
        cr2 = client.run_cr(
            "[Phase X] create uptime check", "gcp_uptime_check_create", cloud_account_id,
            {
                "display_name": uptime_display,
                "host": "google.com",
                "path": "/",
                "period_seconds": 60,
            },
        )
        rollback_stack.append((cr2["id"], "gcp_uptime_check_create"))
        check_id = cr2.get("execution_result", {}).get("check_id", "")
        log(f"Uptime check created: {uptime_display}")

        # 3. Verify via SDK
        creds = _get_gcp_creds()
        if creds:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import monitoring_v3
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])

                alert_client = monitoring_v3.AlertPolicyServiceClient(credentials=gc)
                policies = list(alert_client.list_alert_policies(name=f"projects/{gcp_project}"))
                found_alert = any(p.display_name == alert_display for p in policies)
                assert found_alert, f"Alert policy '{alert_display}' not found"

                uptime_client = monitoring_v3.UptimeCheckServiceClient(credentials=gc)
                checks = list(uptime_client.list_uptime_check_configs(parent=f"projects/{gcp_project}"))
                found_uptime = any(c.display_name == uptime_display for c in checks)
                assert found_uptime, f"Uptime check '{uptime_display}' not found"

                log("Alert policy and uptime check verified via SDK")
            except Exception as e:
                print(f"  ⚠️  SDK verification error: {e}")
                raise

        # 4. Rollback (LIFO): delete uptime check, then delete alert policy
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        rollback_stack.clear()
        log("Phase X complete")

    except Exception as e:
        print(f"\n❌ Phase X failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete via SDK using display_name search
        creds = _get_gcp_creds()
        if creds and gcp_project:
            try:
                import json as _json
                from google.oauth2 import service_account as _sa
                from google.cloud import monitoring_v3
                key_raw = creds.get("service_account_key_json", "")
                key_json = _json.loads(key_raw) if isinstance(key_raw, str) else key_raw
                gc = _sa.Credentials.from_service_account_info(
                    key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
                try:
                    alert_client = monitoring_v3.AlertPolicyServiceClient(credentials=gc)
                    for p in alert_client.list_alert_policies(name=f"projects/{gcp_project}"):
                        if p.display_name == alert_display:
                            alert_client.delete_alert_policy(name=p.name)
                            print(f"  Safety net: deleted alert policy {alert_display}")
                except Exception:
                    pass
                try:
                    uptime_client = monitoring_v3.UptimeCheckServiceClient(credentials=gc)
                    for c in uptime_client.list_uptime_check_configs(parent=f"projects/{gcp_project}"):
                        if c.display_name == uptime_display:
                            uptime_client.delete_uptime_check_config(name=c.name)
                            print(f"  Safety net: deleted uptime check {uptime_display}")
                except Exception:
                    pass
            except Exception as e2:
                print(f"  ⚠️  Safety net monitoring cleanup failed: {e2}")
```

Update main():
```python
        if "X" in phases:
            run_phase_x(client, cloud_account_id, args.gcp_project)
```

- [ ] **Step 3: Commit and push**

```bash
git add backend/tests/smoke/test_gcp_live.py
git commit -m "feat(smoke): implement phase X Cloud Monitoring alert policy + uptime check"
git push
```

- [ ] **Step 4: Pull on EC2, restart, run Phase X smoke**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "export HOME=/home/ec2-user && cd /home/ec2-user/nexplane && git pull && docker compose restart backend"
sleep 10

ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane/backend/tests/smoke && python test_gcp_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme \
  --phases X \
  --gcp-project YOUR_GCP_PROJECT_ID"
```

Expected output:
```
[Phase X] Cloud Monitoring
  ✓ Alert policy created: nexplane-smoke-x-alert-...
  ✓ Uptime check created: nexplane-smoke-x-uptime-...
  ✓ Alert policy and uptime check verified via SDK
✅ ALL SELECTED PHASES PASSED
```

- [ ] **Step 5: Run V, W, X combined to verify no regressions**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane/backend/tests/smoke && python test_gcp_live.py \
  --base-url http://localhost:8000 \
  --email admin@nexplane.local \
  --password changeme \
  --phases V,X \
  --gcp-project YOUR_GCP_PROJECT_ID"
```

Note: Run V and X together for speed. Run W separately when you have 15+ minutes available (Cloud SQL provisioning).

Expected: all selected phases pass with `✅ ALL SELECTED PHASES PASSED`.
