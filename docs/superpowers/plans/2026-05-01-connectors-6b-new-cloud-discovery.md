# Connectors 6b: New Cloud & Discovery Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four new production connectors: GCP, RunZero, Wiz, and Microsoft Entra ID (standalone).

**Architecture:** Single Alembic migration (010) adds 4 new enum values. Each connector gets a catalog JSON, a `_client.py`, and executor files for each action. Real-if-credentials/mock-if-not pattern throughout.

**Tech Stack:** `google-cloud-compute`, `google-cloud-storage`, `google-cloud-iam`, `google-cloud-securitycenter`, `msal`, `requests`, `httpx`

---

## File Map

**Backend:**
- Create: `backend/alembic/versions/010_add_new_connector_types_6b.py`
- Modify: `backend/app/models/connector.py` — add 4 ConnectorType values
- Create: `backend/app/connectors/catalog/gcp.json`
- Create: `backend/app/connectors/catalog/runzero.json`
- Create: `backend/app/connectors/catalog/wiz.json`
- Create: `backend/app/connectors/catalog/entra_id.json`
- Create: `backend/app/connectors/executors/gcp/` (14 files: `_client.py` + 13 executors)
- Create: `backend/app/connectors/executors/runzero/` (3 files: `_client.py` + 5 executors)
- Create: `backend/app/connectors/executors/wiz/` (3 files: `_client.py` + 6 executors)
- Create: `backend/app/connectors/executors/entra_id/` (3 files: `_client.py` + 11 executors)

**Frontend:**
- Modify: `frontend/src/types/api.ts` — add 4 ConnectorType values
- Modify: `frontend/src/pages/Connectors.tsx` — add labels + icons
- Modify: `frontend/src/components/AddConnectorModal.tsx` — add to dropdowns

**Requirements:**
- Modify: `backend/requirements.txt` — add google-cloud packages + msal

---

### Task 1: Database Migration + Model Update

- [ ] **Step 1: Create migration 010**

```python
# backend/alembic/versions/010_add_new_connector_types_6b.py
"""add new connector types for 6b: gcp, runzero, wiz, entra_id

Revision ID: 010
Revises: 009
Create Date: 2026-05-01
"""
from alembic import op

revision = '010'
down_revision = '009'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'gcp'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'runzero'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'wiz'")
    op.execute("ALTER TYPE connector_type ADD VALUE IF NOT EXISTS 'entra_id'")


def downgrade():
    # PostgreSQL does not support removing enum values without recreation
    pass
```

- [ ] **Step 2: Run migration**

```bash
docker compose exec backend alembic upgrade 010
```
Expected: `Running upgrade 009 -> 010`

- [ ] **Step 3: Update ConnectorType in models/connector.py**

Add to ConnectorType enum:
```python
gcp = "gcp"
runzero = "runzero"
wiz = "wiz"
entra_id = "entra_id"
```

- [ ] **Step 4: Add requirements**

Add to `backend/requirements.txt`:
```
google-cloud-compute>=1.14.0
google-cloud-storage>=2.10.0
google-cloud-iam>=2.13.0
google-cloud-securitycenter>=1.23.0
msal>=1.28.0
```

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/010_add_new_connector_types_6b.py backend/app/models/connector.py backend/requirements.txt
git commit -m "feat: add migration 010 and model values for gcp, runzero, wiz, entra_id connector types"
```

---

### Task 2: GCP Connector

**Files:**
- Create: `backend/app/connectors/catalog/gcp.json`
- Create: `backend/app/connectors/executors/gcp/_client.py`
- Create: `backend/app/connectors/executors/gcp/__init__.py`
- Create 13 executor files in `backend/app/connectors/executors/gcp/`

- [ ] **Step 1: Create gcp.json catalog**

```json
{
  "connector_type": "gcp",
  "display_name": "Google Cloud Platform",
  "credential_fields": [
    {"name": "project_id", "label": "GCP Project ID", "type": "string", "required": true},
    {"name": "service_account_key_json", "label": "Service Account Key (JSON)", "type": "password", "required": true, "placeholder": "Paste full JSON key content"}
  ],
  "actions": [
    {"action_id": "discover_compute_instances", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Compute Instances", "description": "All GCE instances: name, zone, machine type, status, IPs, labels, service account", "applicable_asset_types": ["server", "cloud_account"], "parameters": [], "executor": "gcp.discover_compute_instances", "estimated_duration_seconds": 30},
    {"action_id": "discover_iam_bindings", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover IAM Bindings", "description": "Project-level IAM policy bindings: member, role, condition", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "gcp.discover_iam_bindings", "estimated_duration_seconds": 15},
    {"action_id": "discover_storage_buckets", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Storage Buckets", "description": "GCS buckets: public access, versioning, encryption, IAM", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "gcp.discover_storage_buckets", "estimated_duration_seconds": 15},
    {"action_id": "discover_firewall_rules", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Firewall Rules", "description": "VPC firewall rules: direction, source ranges, target tags, ports", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "gcp.discover_firewall_rules", "estimated_duration_seconds": 15},
    {"action_id": "discover_service_accounts", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Service Accounts", "description": "Service accounts: key age, roles, disabled status", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "gcp.discover_service_accounts", "estimated_duration_seconds": 15},
    {"action_id": "ingest_scc_findings", "generic_action": "ingest_findings", "action_type": "ingest", "execution_tier": 1, "display_name": "Ingest SCC Findings", "description": "Security Command Center findings — enriches assets with threat/misconfiguration tags", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "gcp.ingest_scc_findings", "estimated_duration_seconds": 30},
    {"action_id": "stop_instance", "generic_action": "stop_instance", "action_type": "change", "execution_tier": 2, "display_name": "Stop Instance", "description": "Stop a GCE instance. Rollback: start.", "applicable_asset_types": ["server"], "parameters": [{"name": "instance_name", "type": "string", "required": true}, {"name": "zone", "type": "string", "required": true}], "executor": "gcp.stop_instance", "estimated_duration_seconds": 30},
    {"action_id": "start_instance", "generic_action": "start_instance", "action_type": "change", "execution_tier": 2, "display_name": "Start Instance", "description": "Start a stopped GCE instance.", "applicable_asset_types": ["server"], "parameters": [{"name": "instance_name", "type": "string", "required": true}, {"name": "zone", "type": "string", "required": true}], "executor": "gcp.start_instance", "estimated_duration_seconds": 30},
    {"action_id": "delete_instance", "generic_action": "terminate_instance", "action_type": "change", "execution_tier": 3, "display_name": "Delete Instance", "description": "Terminate a GCE instance.", "applicable_asset_types": ["server"], "parameters": [{"name": "instance_name", "type": "string", "required": true}, {"name": "zone", "type": "string", "required": true}], "executor": "gcp.delete_instance", "estimated_duration_seconds": 60, "blast_radius_hint": "destructive"},
    {"action_id": "create_firewall_rule", "generic_action": "create_firewall_rule", "action_type": "change", "execution_tier": 2, "display_name": "Create Firewall Rule", "description": "Add VPC firewall rule (ALLOW or DENY). Rollback: delete.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "rule_name", "type": "string", "required": true}, {"name": "direction", "type": "string", "required": true, "description": "INGRESS or EGRESS"}, {"name": "action", "type": "string", "required": true, "description": "allow or deny"}, {"name": "source_ranges", "type": "array", "required": false}, {"name": "ports", "type": "array", "required": false}], "executor": "gcp.create_firewall_rule", "estimated_duration_seconds": 15},
    {"action_id": "delete_firewall_rule", "generic_action": "delete_firewall_rule", "action_type": "change", "execution_tier": 2, "display_name": "Delete Firewall Rule", "description": "Remove VPC firewall rule.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "rule_name", "type": "string", "required": true}], "executor": "gcp.delete_firewall_rule", "estimated_duration_seconds": 15},
    {"action_id": "block_public_bucket_access", "generic_action": "block_public_access", "action_type": "change", "execution_tier": 2, "display_name": "Block Public Bucket Access", "description": "Remove allUsers/allAuthenticatedUsers IAM bindings from GCS bucket.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "bucket_name", "type": "string", "required": true}], "executor": "gcp.block_public_bucket_access", "estimated_duration_seconds": 10},
    {"action_id": "disable_service_account", "generic_action": "disable_account", "action_type": "change", "execution_tier": 2, "display_name": "Disable Service Account", "description": "Disable a service account. Rollback: enable.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "service_account_email", "type": "string", "required": true}], "executor": "gcp.disable_service_account", "estimated_duration_seconds": 10},
    {"action_id": "rotate_service_account_key", "generic_action": "rotate_credentials", "action_type": "change", "execution_tier": 2, "display_name": "Rotate Service Account Key", "description": "Create new service account key, return in result, delete old key.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "service_account_email", "type": "string", "required": true}, {"name": "old_key_id", "type": "string", "required": true}], "executor": "gcp.rotate_service_account_key", "estimated_duration_seconds": 15}
  ]
}
```

- [ ] **Step 2: Create GCP _client.py**

```python
# backend/app/connectors/executors/gcp/_client.py
import json


def get_credentials(creds: dict):
    from google.oauth2 import service_account
    key_json = json.loads(creds["service_account_key_json"])
    scopes = [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/compute",
    ]
    return service_account.Credentials.from_service_account_info(key_json, scopes=scopes)


def get_project_id(creds: dict) -> str:
    return creds["project_id"]
```

- [ ] **Step 3: Create __init__.py**

```python
# backend/app/connectors/executors/gcp/__init__.py
```

- [ ] **Step 4: Create discover_compute_instances.py**

```python
# backend/app/connectors/executors/gcp/discover_compute_instances.py
import asyncio


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_compute_instances", "instances": [
            {"name": "mock-vm-1", "zone": "us-central1-a", "machine_type": "e2-medium", "status": "RUNNING",
             "internal_ip": "10.0.0.1", "external_ip": "34.1.2.3", "labels": {}}
        ], "count": 1}
    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = compute_v1.InstancesClient(credentials=credentials)
    instances = []
    agg = await loop.run_in_executor(None, lambda: client.aggregated_list(project=project))
    for zone_name, zone_data in agg:
        for inst in zone_data.instances:
            ni = inst.network_interfaces[0] if inst.network_interfaces else None
            instances.append({
                "name": inst.name, "zone": zone_name,
                "machine_type": inst.machine_type.split("/")[-1],
                "status": inst.status,
                "internal_ip": ni.network_i_p if ni else None,
                "external_ip": ni.access_configs[0].nat_i_p if ni and ni.access_configs else None,
                "labels": dict(inst.labels),
                "service_account": inst.service_accounts[0].email if inst.service_accounts else None,
            })
    return {"action": "discover_compute_instances", "instances": instances, "count": len(instances)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

- [ ] **Step 5: Create remaining GCP executors**

Create these files following the same pattern (mock-if-no-creds, real SDK otherwise):

`discover_iam_bindings.py` — use `google.cloud.resourcemanager_v3.ProjectsClient`
`discover_storage_buckets.py` — use `google.cloud.storage.Client`
`discover_firewall_rules.py` — use `google.cloud.compute_v1.FirewallsClient`
`discover_service_accounts.py` — use `google.cloud.iam_admin_v1.IAMClient`
`ingest_scc_findings.py` — use `google.cloud.securitycenter_v1.SecurityCenterClient`
`stop_instance.py` — use `compute_v1.InstancesClient().stop()`
`start_instance.py` — use `compute_v1.InstancesClient().start()`
`delete_instance.py` — use `compute_v1.InstancesClient().delete()`
`create_firewall_rule.py` — use `compute_v1.FirewallsClient().insert()`
`delete_firewall_rule.py` — use `compute_v1.FirewallsClient().delete()`
`block_public_bucket_access.py` — use `storage.Client().bucket(name).iam_configuration`
`disable_service_account.py` — use `iam_admin_v1.IAMClient().disable_service_account()`
`rotate_service_account_key.py` — create new key via IAM, return private key JSON, delete old key

Each uses `asyncio.get_event_loop().run_in_executor(None, lambda: ...)` for sync SDK calls.

- [ ] **Step 6: Commit GCP connector**

```bash
git add backend/app/connectors/catalog/gcp.json backend/app/connectors/executors/gcp/
git commit -m "feat: add GCP connector with 14 actions (discover instances/IAM/storage/firewall/SA, SCC findings, stop/start/delete, firewall management, bucket public access, SA management)"
```

---

### Task 3: RunZero Connector

**Files:**
- Create: `backend/app/connectors/catalog/runzero.json`
- Create: `backend/app/connectors/executors/runzero/_client.py`
- Create: `backend/app/connectors/executors/runzero/__init__.py`
- Create 5 executor files

- [ ] **Step 1: Create runzero.json catalog**

```json
{
  "connector_type": "runzero",
  "display_name": "RunZero",
  "credential_fields": [
    {"name": "api_token", "label": "API Token", "type": "password", "required": true},
    {"name": "org_id", "label": "Organization UUID", "type": "string", "required": true}
  ],
  "actions": [
    {"action_id": "discover_assets", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Assets", "description": "All network assets: IP, MAC, OS fingerprint, open ports, services, hostname, first/last seen, tags", "applicable_asset_types": ["server", "network_device"], "parameters": [], "executor": "runzero.discover_assets", "estimated_duration_seconds": 60},
    {"action_id": "discover_services", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Services", "description": "Detected network services per asset: port, protocol, service name, banner", "applicable_asset_types": ["server", "network_device"], "parameters": [], "executor": "runzero.discover_services", "estimated_duration_seconds": 30},
    {"action_id": "discover_wireless_networks", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Wireless Networks", "description": "Detected WiFi networks: SSID, BSSID, encryption, signal", "applicable_asset_types": ["network_device"], "parameters": [], "executor": "runzero.discover_wireless_networks", "estimated_duration_seconds": 15},
    {"action_id": "trigger_scan", "generic_action": "trigger_scan", "action_type": "change", "execution_tier": 2, "display_name": "Trigger Scan", "description": "Start a RunZero scan task against a target CIDR or asset group. Returns task ID.", "applicable_asset_types": ["network_device", "server"], "parameters": [{"name": "targets", "type": "string", "required": true, "description": "CIDR range or asset group name"}], "executor": "runzero.trigger_scan", "estimated_duration_seconds": 10},
    {"action_id": "get_scan_status", "generic_action": "get_status", "action_type": "change", "execution_tier": 1, "display_name": "Get Scan Status", "description": "Check status of a running scan task.", "applicable_asset_types": ["network_device", "server"], "parameters": [{"name": "task_id", "type": "string", "required": true}], "executor": "runzero.get_scan_status", "estimated_duration_seconds": 5}
  ]
}
```

- [ ] **Step 2: Create RunZero _client.py**

```python
# backend/app/connectors/executors/runzero/_client.py
import httpx

BASE_URL = "https://console.runzero.com/api/v1.0"


def get_client(creds: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {creds['api_token']}"},
        timeout=30.0,
    )
```

- [ ] **Step 3: Create __init__.py**

```python
# backend/app/connectors/executors/runzero/__init__.py
```

- [ ] **Step 4: Create executor files**

```python
# backend/app/connectors/executors/runzero/discover_assets.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_assets", "assets": [
            {"id": "mock-asset-1", "address": "10.0.0.1", "hostname": "mock-server", "os": "Linux 5.x",
             "type": "server", "first_seen": "2025-01-01T00:00:00Z", "last_seen": "2025-06-01T00:00:00Z"}
        ], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get(f"/org/assets", params={"_oid": creds["org_id"], "fields": "id,addresses,hostnames,os,type,first_seen,last_seen,tags"})
        resp.raise_for_status()
        data = resp.json()
        assets = [{"id": a.get("id"), "addresses": a.get("addresses", []), "hostname": a.get("names", [""])[0] if a.get("names") else None, "os": a.get("os"), "type": a.get("type")} for a in data]
    return {"action": "discover_assets", "assets": assets, "count": len(assets)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/runzero/discover_services.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_services", "services": [
            {"asset_id": "mock-asset-1", "port": 22, "protocol": "tcp", "service": "ssh", "banner": "OpenSSH 8.x"}
        ], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get(f"/org/services", params={"_oid": creds["org_id"]})
        resp.raise_for_status()
        services = resp.json()
    return {"action": "discover_services", "services": services[:500], "count": len(services)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/runzero/discover_wireless_networks.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_wireless_networks", "networks": [], "count": 0}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get(f"/org/wireless", params={"_oid": creds["org_id"]})
        resp.raise_for_status()
        networks = resp.json()
    return {"action": "discover_wireless_networks", "networks": networks, "count": len(networks)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/runzero/trigger_scan.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    targets = parameters["targets"]
    if not creds:
        return {"action": "trigger_scan", "task_id": "mock-task-id", "targets": targets, "status": "queued"}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post(f"/org/tasks/scan", params={"_oid": creds["org_id"]}, json={"targets": targets, "rate": 1000})
        resp.raise_for_status()
        task = resp.json()
    return {"action": "trigger_scan", "task_id": task.get("id"), "targets": targets, "status": task.get("status")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan tasks cannot be cancelled via API"}
```

```python
# backend/app/connectors/executors/runzero/get_scan_status.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    task_id = parameters["task_id"]
    if not creds:
        return {"action": "get_scan_status", "task_id": task_id, "status": "completed", "progress": 100}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get(f"/org/tasks/{task_id}", params={"_oid": creds["org_id"]})
        resp.raise_for_status()
        task = resp.json()
    return {"action": "get_scan_status", "task_id": task_id, "status": task.get("status"), "progress": task.get("progress")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "status check has no rollback"}
```

- [ ] **Step 5: Commit RunZero connector**

```bash
git add backend/app/connectors/catalog/runzero.json backend/app/connectors/executors/runzero/
git commit -m "feat: add RunZero connector with 5 actions (discover assets/services/wireless, trigger scan, get scan status)"
```

---

### Task 4: Wiz Connector

**Files:**
- Create: `backend/app/connectors/catalog/wiz.json`
- Create: `backend/app/connectors/executors/wiz/_client.py`
- Create: `backend/app/connectors/executors/wiz/__init__.py`
- Create 6 executor files

- [ ] **Step 1: Create wiz.json catalog**

```json
{
  "connector_type": "wiz",
  "display_name": "Wiz",
  "credential_fields": [
    {"name": "client_id", "label": "Client ID", "type": "string", "required": true},
    {"name": "client_secret", "label": "Client Secret", "type": "password", "required": true},
    {"name": "tenant_id", "label": "Tenant ID", "type": "string", "required": true}
  ],
  "actions": [
    {"action_id": "discover_cloud_resources", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Cloud Resources", "description": "All cloud resources indexed by Wiz across connected cloud accounts", "applicable_asset_types": ["cloud_account", "server"], "parameters": [], "executor": "wiz.discover_cloud_resources", "estimated_duration_seconds": 60},
    {"action_id": "ingest_issues", "generic_action": "ingest_findings", "action_type": "ingest", "execution_tier": 1, "display_name": "Ingest Wiz Issues", "description": "Wiz security issues (misconfigurations, vulnerabilities, secrets): severity, resource, rule, status", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "wiz.ingest_issues", "estimated_duration_seconds": 30},
    {"action_id": "ingest_vulnerabilities", "generic_action": "ingest_findings", "action_type": "ingest", "execution_tier": 1, "display_name": "Ingest Vulnerabilities", "description": "Wiz vulnerability findings from OS packages and container images", "applicable_asset_types": ["server", "cloud_account"], "parameters": [], "executor": "wiz.ingest_vulnerabilities", "estimated_duration_seconds": 30},
    {"action_id": "ingest_attack_paths", "generic_action": "ingest_findings", "action_type": "ingest", "execution_tier": 1, "display_name": "Ingest Attack Paths", "description": "Critical attack paths identified by Wiz — flags assets on a path to sensitive data", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "wiz.ingest_attack_paths", "estimated_duration_seconds": 30},
    {"action_id": "resolve_issue", "generic_action": "resolve_finding", "action_type": "change", "execution_tier": 2, "display_name": "Resolve Issue", "description": "Mark a Wiz issue as resolved with a reason note.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "issue_id", "type": "string", "required": true}, {"name": "reason", "type": "string", "required": true}], "executor": "wiz.resolve_issue", "estimated_duration_seconds": 5},
    {"action_id": "accept_risk", "generic_action": "accept_risk", "action_type": "change", "execution_tier": 2, "display_name": "Accept Risk", "description": "Accept risk on a Wiz issue for a specified duration.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "issue_id", "type": "string", "required": true}, {"name": "duration_days", "type": "integer", "required": true}, {"name": "reason", "type": "string", "required": true}], "executor": "wiz.accept_risk", "estimated_duration_seconds": 5}
  ]
}
```

- [ ] **Step 2: Create Wiz _client.py (OAuth2 + GraphQL)**

```python
# backend/app/connectors/executors/wiz/_client.py
import httpx

AUTH_URL = "https://auth.app.wiz.io/oauth/token"
GRAPHQL_URL = "https://api.us1.app.wiz.io/graphql"


async def get_access_token(creds: dict) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(AUTH_URL, data={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
            "audience": "wiz-api",
        })
        resp.raise_for_status()
        return resp.json()["access_token"]


async def graphql_query(token: str, query: str, variables: dict = None) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GRAPHQL_URL,
            headers={"Authorization": f"Bearer {token}"},
            json={"query": query, "variables": variables or {}},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 3: Create __init__.py**

```python
# backend/app/connectors/executors/wiz/__init__.py
```

- [ ] **Step 4: Create executor files**

```python
# backend/app/connectors/executors/wiz/discover_cloud_resources.py
QUERY = """
query CloudResources($first: Int) {
  cloudResources(first: $first) {
    nodes { id type name cloudAccount { id name } region tags { key value } }
  }
}
"""

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_cloud_resources", "resources": [
            {"id": "mock-res-1", "type": "virtual_machine", "name": "mock-vm", "cloud_account": "mock-account"}
        ], "count": 1}
    from ._client import get_access_token, graphql_query
    token = await get_access_token(creds)
    data = await graphql_query(token, QUERY, {"first": 500})
    resources = [{"id": r["id"], "type": r["type"], "name": r["name"]} for r in data.get("data", {}).get("cloudResources", {}).get("nodes", [])]
    return {"action": "discover_cloud_resources", "resources": resources, "count": len(resources)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/wiz/ingest_issues.py
QUERY = """
query Issues($first: Int) {
  issues(first: $first, filterBy: {status: [OPEN]}) {
    nodes { id type severity status sourceRule { name } resource { id name type } }
  }
}
"""

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "ingest_issues", "issues": [], "count": 0}
    from ._client import get_access_token, graphql_query
    token = await get_access_token(creds)
    data = await graphql_query(token, QUERY, {"first": 500})
    issues = data.get("data", {}).get("issues", {}).get("nodes", [])
    return {"action": "ingest_issues", "issues": issues, "count": len(issues)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ingest has no rollback"}
```

```python
# backend/app/connectors/executors/wiz/ingest_vulnerabilities.py
QUERY = """
query Vulnerabilities($first: Int) {
  vulnerabilityFindings(first: $first) {
    nodes { id name severity cvss cveId status affectedAsset { id name } }
  }
}
"""

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "ingest_vulnerabilities", "vulnerabilities": [], "count": 0}
    from ._client import get_access_token, graphql_query
    token = await get_access_token(creds)
    data = await graphql_query(token, QUERY, {"first": 500})
    vulns = data.get("data", {}).get("vulnerabilityFindings", {}).get("nodes", [])
    return {"action": "ingest_vulnerabilities", "vulnerabilities": vulns, "count": len(vulns)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ingest has no rollback"}
```

```python
# backend/app/connectors/executors/wiz/ingest_attack_paths.py
QUERY = """
query AttackPaths($first: Int) {
  attackPaths(first: $first, filterBy: {riskLevel: [CRITICAL, HIGH]}) {
    nodes { id riskLevel attackVector endpoints { id name type } }
  }
}
"""

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "ingest_attack_paths", "attack_paths": [], "count": 0}
    from ._client import get_access_token, graphql_query
    token = await get_access_token(creds)
    data = await graphql_query(token, QUERY, {"first": 200})
    paths = data.get("data", {}).get("attackPaths", {}).get("nodes", [])
    return {"action": "ingest_attack_paths", "attack_paths": paths, "count": len(paths)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ingest has no rollback"}
```

```python
# backend/app/connectors/executors/wiz/resolve_issue.py
MUTATION = """
mutation ResolveIssue($id: ID!, $note: String!) {
  updateIssue(input: {id: $id, status: RESOLVED, note: $note}) {
    issue { id status }
  }
}
"""

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    issue_id = parameters["issue_id"]
    if not creds:
        return {"action": "resolve_issue", "issue_id": issue_id, "status": "RESOLVED"}
    from ._client import get_access_token, graphql_query
    token = await get_access_token(creds)
    data = await graphql_query(token, MUTATION, {"id": issue_id, "note": parameters["reason"]})
    return {"action": "resolve_issue", "issue_id": issue_id, "result": data.get("data", {}).get("updateIssue", {})}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "issue resolution cannot be undone automatically"}
```

```python
# backend/app/connectors/executors/wiz/accept_risk.py
MUTATION = """
mutation AcceptRisk($id: ID!, $days: Int!, $note: String!) {
  updateIssue(input: {id: $id, status: IN_PROGRESS, dueAt: $days, note: $note}) {
    issue { id status }
  }
}
"""

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    issue_id = parameters["issue_id"]
    if not creds:
        return {"action": "accept_risk", "issue_id": issue_id, "duration_days": parameters.get("duration_days"), "accepted": True}
    from ._client import get_access_token, graphql_query
    token = await get_access_token(creds)
    data = await graphql_query(token, MUTATION, {"id": issue_id, "days": parameters["duration_days"], "note": parameters["reason"]})
    return {"action": "accept_risk", "issue_id": issue_id, "result": data.get("data", {})}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "risk acceptance cannot be undone automatically"}
```

- [ ] **Step 5: Commit Wiz connector**

```bash
git add backend/app/connectors/catalog/wiz.json backend/app/connectors/executors/wiz/
git commit -m "feat: add Wiz connector with 6 actions (discover cloud resources, ingest issues/vulns/attack paths, resolve issue, accept risk)"
```

---

### Task 5: Microsoft Entra ID (Standalone) Connector

**Files:**
- Create: `backend/app/connectors/catalog/entra_id.json`
- Create: `backend/app/connectors/executors/entra_id/_client.py`
- Create: `backend/app/connectors/executors/entra_id/__init__.py`
- Create 11 executor files

- [ ] **Step 1: Create entra_id.json catalog**

```json
{
  "connector_type": "entra_id",
  "display_name": "Microsoft Entra ID",
  "credential_fields": [
    {"name": "tenant_id", "label": "Tenant ID", "type": "string", "required": true},
    {"name": "client_id", "label": "Client ID", "type": "string", "required": true},
    {"name": "client_secret", "label": "Client Secret", "type": "password", "required": true}
  ],
  "actions": [
    {"action_id": "discover_users", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Users", "description": "All Entra users: UPN, MFA status, last sign-in, assigned roles, license, account enabled", "applicable_asset_types": ["identity", "cloud_account"], "parameters": [], "executor": "entra_id.discover_users", "estimated_duration_seconds": 30},
    {"action_id": "discover_groups", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Groups", "description": "All Entra groups: members, type, owners", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "entra_id.discover_groups", "estimated_duration_seconds": 15},
    {"action_id": "discover_applications", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Applications", "description": "Registered applications and service principals", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "entra_id.discover_applications", "estimated_duration_seconds": 15},
    {"action_id": "discover_conditional_access", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Conditional Access", "description": "Conditional Access policies: state, conditions, grant controls", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "entra_id.discover_conditional_access", "estimated_duration_seconds": 10},
    {"action_id": "discover_privileged_roles", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Privileged Roles", "description": "Users with privileged roles (Global Admin, Security Admin, etc.)", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "entra_id.discover_privileged_roles", "estimated_duration_seconds": 10},
    {"action_id": "disable_user", "generic_action": "disable_user", "action_type": "change", "execution_tier": 2, "display_name": "Disable User", "description": "Set accountEnabled=false for a user. Rollback: enable.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true, "description": "Entra user ID or UPN"}], "executor": "entra_id.disable_user", "estimated_duration_seconds": 5},
    {"action_id": "enable_user", "generic_action": "enable_user", "action_type": "change", "execution_tier": 2, "display_name": "Enable User", "description": "Re-enable a user.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true}], "executor": "entra_id.enable_user", "estimated_duration_seconds": 5},
    {"action_id": "reset_mfa", "generic_action": "reset_mfa", "action_type": "change", "execution_tier": 2, "display_name": "Reset MFA", "description": "Delete all MFA auth methods, forcing re-enrollment.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true}], "executor": "entra_id.reset_mfa", "estimated_duration_seconds": 10},
    {"action_id": "revoke_sessions", "generic_action": "revoke_sessions", "action_type": "change", "execution_tier": 2, "display_name": "Revoke Sessions", "description": "Revoke all active sign-in sessions via revokeSignInSessions.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true}], "executor": "entra_id.revoke_sessions", "estimated_duration_seconds": 5},
    {"action_id": "remove_from_role", "generic_action": "remove_role", "action_type": "change", "execution_tier": 2, "display_name": "Remove from Role", "description": "Remove a user from a directory role.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true}, {"name": "role_id", "type": "string", "required": true}], "executor": "entra_id.remove_from_role", "estimated_duration_seconds": 5},
    {"action_id": "block_sign_in", "generic_action": "block_sign_in", "action_type": "change", "execution_tier": 2, "display_name": "Block Sign-In", "description": "Disable sign-in + revoke sessions in one operation.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true}], "executor": "entra_id.block_sign_in", "estimated_duration_seconds": 10}
  ]
}
```

- [ ] **Step 2: Create Entra ID _client.py**

```python
# backend/app/connectors/executors/entra_id/_client.py
import msal
import httpx

GRAPH_URL = "https://graph.microsoft.com/v1.0"


async def get_access_token(creds: dict) -> str:
    app = msal.ConfidentialClientApplication(
        creds["client_id"],
        authority=f"https://login.microsoftonline.com/{creds['tenant_id']}",
        client_credential=creds["client_secret"],
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Failed to acquire token: {result.get('error_description')}")
    return result["access_token"]


def get_graph_client(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=GRAPH_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
```

- [ ] **Step 3: Create __init__.py**

```python
# backend/app/connectors/executors/entra_id/__init__.py
```

- [ ] **Step 4: Create all 11 executor files**

```python
# backend/app/connectors/executors/entra_id/discover_users.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_users", "users": [
            {"id": "mock-user-id", "upn": "alice@example.com", "display_name": "Alice", "account_enabled": True}
        ], "count": 1}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.get("/users", params={"$select": "id,userPrincipalName,displayName,accountEnabled,lastSignInDateTime", "$top": 999})
        resp.raise_for_status()
        users = [{"id": u["id"], "upn": u.get("userPrincipalName"), "display_name": u.get("displayName"), "account_enabled": u.get("accountEnabled")} for u in resp.json().get("value", [])]
    return {"action": "discover_users", "users": users, "count": len(users)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/entra_id/discover_groups.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_groups", "groups": [{"id": "mock-group", "name": "All Users", "type": "security"}], "count": 1}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.get("/groups", params={"$select": "id,displayName,groupTypes,membershipRule", "$top": 999})
        resp.raise_for_status()
        groups = [{"id": g["id"], "name": g.get("displayName"), "types": g.get("groupTypes", [])} for g in resp.json().get("value", [])]
    return {"action": "discover_groups", "groups": groups, "count": len(groups)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/entra_id/discover_applications.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_applications", "applications": [{"id": "mock-app", "name": "Mock App", "publisher": "ACME"}], "count": 1}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.get("/applications", params={"$select": "id,displayName,publisherDomain,createdDateTime", "$top": 999})
        resp.raise_for_status()
        apps = [{"id": a["id"], "name": a.get("displayName"), "publisher": a.get("publisherDomain")} for a in resp.json().get("value", [])]
    return {"action": "discover_applications", "applications": apps, "count": len(apps)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/entra_id/discover_conditional_access.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_conditional_access", "policies": [], "count": 0}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.get("/identity/conditionalAccess/policies")
        resp.raise_for_status()
        policies = [{"id": p["id"], "name": p.get("displayName"), "state": p.get("state")} for p in resp.json().get("value", [])]
    return {"action": "discover_conditional_access", "policies": policies, "count": len(policies)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/entra_id/discover_privileged_roles.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_privileged_roles", "role_assignments": [], "count": 0}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.get("/roleManagement/directory/roleAssignments", params={"$expand": "principal,roleDefinition", "$top": 999})
        resp.raise_for_status()
        assignments = [{"user_id": a.get("principalId"), "role_name": a.get("roleDefinition", {}).get("displayName"), "role_id": a.get("roleDefinitionId")} for a in resp.json().get("value", [])]
    return {"action": "discover_privileged_roles", "role_assignments": assignments, "count": len(assignments)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/entra_id/disable_user.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "disable_user", "user_id": user_id, "account_enabled": False}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.patch(f"/users/{user_id}", json={"accountEnabled": False})
        resp.raise_for_status()
    return {"action": "disable_user", "user_id": user_id, "account_enabled": False}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.entra_id.enable_user import execute as enable
    return await enable(parameters, [], connector)
```

```python
# backend/app/connectors/executors/entra_id/enable_user.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "enable_user", "user_id": user_id, "account_enabled": True}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.patch(f"/users/{user_id}", json={"accountEnabled": True})
        resp.raise_for_status()
    return {"action": "enable_user", "user_id": user_id, "account_enabled": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "enable rollback would disable — use disable_user explicitly"}
```

```python
# backend/app/connectors/executors/entra_id/reset_mfa.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "reset_mfa", "user_id": user_id, "methods_deleted": 0}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.get(f"/users/{user_id}/authentication/methods")
        resp.raise_for_status()
        methods = resp.json().get("value", [])
        deleted = 0
        for method in methods:
            method_type = method.get("@odata.type", "")
            method_id = method.get("id")
            if "password" not in method_type.lower() and method_id:
                try:
                    await client.delete(f"/users/{user_id}/authentication/methods/{method_id}")
                    deleted += 1
                except Exception:
                    pass
    return {"action": "reset_mfa", "user_id": user_id, "methods_deleted": deleted}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot restore deleted MFA methods"}
```

```python
# backend/app/connectors/executors/entra_id/revoke_sessions.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "revoke_sessions", "user_id": user_id, "sessions_revoked": True}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.post(f"/users/{user_id}/revokeSignInSessions")
        resp.raise_for_status()
    return {"action": "revoke_sessions", "user_id": user_id, "sessions_revoked": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot restore revoked sessions"}
```

```python
# backend/app/connectors/executors/entra_id/remove_from_role.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    role_id = parameters["role_id"]
    if not creds:
        return {"action": "remove_from_role", "user_id": user_id, "role_id": role_id, "removed": True}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        # Find the assignment ID first
        resp = await client.get(f"/roleManagement/directory/roleAssignments", params={"$filter": f"principalId eq '{user_id}' and roleDefinitionId eq '{role_id}'"})
        resp.raise_for_status()
        assignments = resp.json().get("value", [])
        for assignment in assignments:
            await client.delete(f"/roleManagement/directory/roleAssignments/{assignment['id']}")
    return {"action": "remove_from_role", "user_id": user_id, "role_id": role_id, "removed": len(assignments)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "re-adding to role requires explicit action"}
```

```python
# backend/app/connectors/executors/entra_id/block_sign_in.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "block_sign_in", "user_id": user_id, "account_enabled": False, "sessions_revoked": True}
    from app.connectors.executors.entra_id.disable_user import execute as disable
    from app.connectors.executors.entra_id.revoke_sessions import execute as revoke
    disable_result = await disable(parameters, asset_ids, connector)
    revoke_result = await revoke(parameters, asset_ids, connector)
    return {"action": "block_sign_in", "user_id": user_id, "disable_result": disable_result, "revoke_result": revoke_result}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.entra_id.enable_user import execute as enable
    return await enable(parameters, [], connector)
```

- [ ] **Step 5: Commit Entra ID connector**

```bash
git add backend/app/connectors/catalog/entra_id.json backend/app/connectors/executors/entra_id/
git commit -m "feat: add Microsoft Entra ID connector with 11 actions (discover users/groups/apps/CA policies/privileged roles, disable/enable user, reset MFA, revoke sessions, remove from role, block sign-in)"
```

---

### Task 6: Frontend Updates

- [ ] **Step 1: Update ConnectorType in api.ts**

Add to the ConnectorType union: `| 'gcp' | 'runzero' | 'wiz' | 'entra_id'`

- [ ] **Step 2: Update CONNECTOR_LABELS and CONNECTOR_ICONS in Connectors.tsx**

```typescript
gcp: 'Google Cloud Platform',
runzero: 'RunZero',
wiz: 'Wiz',
entra_id: 'Microsoft Entra ID',
```

Icons: use `Cloud` for gcp, `Network` for runzero, `ShieldCheck` for wiz, `Users` for entra_id (all from lucide-react).

- [ ] **Step 3: Update AddConnectorModal.tsx**

Add the 4 new connector types to the options list with their display names.

- [ ] **Step 4: Verify TypeScript build**

```bash
docker compose exec frontend npm run build 2>&1 | tail -10
```
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/
git commit -m "feat: add GCP, RunZero, Wiz, Entra ID to frontend connector type lists"
```

---

### Task 7: Integration Tests

- [ ] **Step 1: Write tests**

```python
# backend/app/tests/test_connector_6b.py
import pytest


@pytest.mark.asyncio
async def test_gcp_discover_compute_instances_mock():
    from app.connectors.executors.gcp.discover_compute_instances import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_compute_instances"
    assert isinstance(result["instances"], list)


@pytest.mark.asyncio
async def test_runzero_discover_assets_mock():
    from app.connectors.executors.runzero.discover_assets import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_assets"
    assert result["count"] >= 0


@pytest.mark.asyncio
async def test_wiz_discover_cloud_resources_mock():
    from app.connectors.executors.wiz.discover_cloud_resources import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_cloud_resources"


@pytest.mark.asyncio
async def test_entra_id_disable_user_mock():
    from app.connectors.executors.entra_id.disable_user import execute
    result = await execute({"user_id": "test-user"}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "disable_user"
    assert result["account_enabled"] is False


@pytest.mark.asyncio
async def test_entra_id_block_sign_in_rollback():
    from app.connectors.executors.entra_id.block_sign_in import rollback
    result = await rollback({"user_id": "test-user"}, {}, type("C", (), {"credentials": {}})())
    assert result["action"] == "enable_user"
```

- [ ] **Step 2: Run tests**

```bash
docker compose exec backend pytest app/tests/test_connector_6b.py -v
```
Expected: 5 tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/app/tests/test_connector_6b.py
git commit -m "test: add integration tests for 6b new cloud connectors"
```
