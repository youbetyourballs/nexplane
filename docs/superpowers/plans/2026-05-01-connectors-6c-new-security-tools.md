# Connectors 6c: New Security Tool Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 7 new security tool connectors: SentinelOne, Microsoft Defender for Endpoint, HashiCorp Vault, GitHub, Kubernetes, Snyk, and Qualys.

**Architecture:** Single Alembic migration (011) adds 7 new enum values. Each connector gets a catalog JSON, `_client.py`, `__init__.py`, and executor files. Real-if-credentials/mock-if-not pattern throughout.

**Tech Stack:** `requests`, `msal`, `hvac`, `PyGithub` or `requests`, `kubernetes` Python client

---

## File Map

**Backend:**
- Create: `backend/alembic/versions/011_add_new_connector_types_6c.py`
- Modify: `backend/app/models/connector.py`
- Create catalogs: `sentinelone.json`, `defender_endpoint.json`, `hashicorp_vault.json`, `github.json`, `kubernetes.json`, `snyk.json`, `qualys.json`
- Create executor directories: `sentinelone/`, `defender_endpoint/`, `hashicorp_vault/`, `github/`, `kubernetes/`, `snyk/`, `qualys/`
- Modify: `backend/requirements.txt`

**Frontend:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/pages/Connectors.tsx`
- Modify: `frontend/src/components/AddConnectorModal.tsx`

---

### Task 1: Database Migration + Model Update

- [ ] **Step 1: Create migration 011**

```python
# backend/alembic/versions/011_add_new_connector_types_6c.py
"""add new connector types for 6c: sentinelone, defender_endpoint, hashicorp_vault, github, kubernetes, snyk, qualys

Revision ID: 011
Revises: 010
Create Date: 2026-05-01
"""
from alembic import op

revision = '011'
down_revision = '010'
branch_labels = None
depends_on = None


def upgrade():
    for val in ['sentinelone', 'defender_endpoint', 'hashicorp_vault', 'github', 'kubernetes', 'snyk', 'qualys']:
        op.execute(f"ALTER TYPE connector_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade():
    pass
```

- [ ] **Step 2: Run migration**

```bash
docker compose exec backend alembic upgrade 011
```

- [ ] **Step 3: Add to ConnectorType enum in models/connector.py**

```python
sentinelone = "sentinelone"
defender_endpoint = "defender_endpoint"
hashicorp_vault = "hashicorp_vault"
github = "github"
kubernetes = "kubernetes"
snyk = "snyk"
qualys = "qualys"
```

- [ ] **Step 4: Add requirements**

Add to `backend/requirements.txt`:
```
hvac>=2.1.0
PyGithub>=2.1.0
kubernetes>=28.1.0
```

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/011_add_new_connector_types_6c.py backend/app/models/connector.py backend/requirements.txt
git commit -m "feat: add migration 011 for sentinelone, defender_endpoint, hashicorp_vault, github, kubernetes, snyk, qualys connector types"
```

---

### Task 2: SentinelOne Connector

- [ ] **Step 1: Create catalog sentinelone.json**

```json
{
  "connector_type": "sentinelone",
  "display_name": "SentinelOne",
  "credential_fields": [
    {"name": "management_url", "label": "Management URL", "type": "string", "required": true, "placeholder": "https://usea1.sentinelone.net"},
    {"name": "api_token", "label": "API Token", "type": "password", "required": true}
  ],
  "actions": [
    {"action_id": "discover_agents", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Agents", "description": "All SentinelOne agents: hostname, OS, version, status, group, threat count, online status", "applicable_asset_types": ["server"], "parameters": [], "executor": "sentinelone.discover_agents", "estimated_duration_seconds": 30},
    {"action_id": "discover_threats", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Threats", "description": "Active threats: severity, classification, agent, file, status — enriches assets with threat tags", "applicable_asset_types": ["server"], "parameters": [], "executor": "sentinelone.discover_threats", "estimated_duration_seconds": 30},
    {"action_id": "discover_groups", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Groups", "description": "Agent groups and site hierarchy", "applicable_asset_types": ["server"], "parameters": [], "executor": "sentinelone.discover_groups", "estimated_duration_seconds": 15},
    {"action_id": "isolate_endpoint", "generic_action": "isolate_endpoint", "action_type": "change", "execution_tier": 2, "display_name": "Isolate Endpoint", "description": "Network-isolate an endpoint. Rollback: reconnect.", "applicable_asset_types": ["server"], "parameters": [{"name": "agent_id", "type": "string", "required": true}], "executor": "sentinelone.isolate_endpoint", "estimated_duration_seconds": 10},
    {"action_id": "reconnect_endpoint", "generic_action": "reconnect_endpoint", "action_type": "change", "execution_tier": 2, "display_name": "Reconnect Endpoint", "description": "Lift network isolation.", "applicable_asset_types": ["server"], "parameters": [{"name": "agent_id", "type": "string", "required": true}], "executor": "sentinelone.reconnect_endpoint", "estimated_duration_seconds": 10},
    {"action_id": "kill_process", "generic_action": "kill_process", "action_type": "change", "execution_tier": 2, "display_name": "Kill Process", "description": "Terminate a specific process on endpoint via SentinelOne RCE.", "applicable_asset_types": ["server"], "parameters": [{"name": "agent_id", "type": "string", "required": true}, {"name": "process_name", "type": "string", "required": true}], "executor": "sentinelone.kill_process", "estimated_duration_seconds": 10},
    {"action_id": "quarantine_file", "generic_action": "quarantine_file", "action_type": "change", "execution_tier": 2, "display_name": "Quarantine File", "description": "Quarantine a specific file hash on an endpoint.", "applicable_asset_types": ["server"], "parameters": [{"name": "agent_id", "type": "string", "required": true}, {"name": "file_hash", "type": "string", "required": true}], "executor": "sentinelone.quarantine_file", "estimated_duration_seconds": 10},
    {"action_id": "initiate_scan", "generic_action": "initiate_scan", "action_type": "change", "execution_tier": 2, "display_name": "Initiate Full Scan", "description": "Trigger a full disk scan on an endpoint.", "applicable_asset_types": ["server"], "parameters": [{"name": "agent_id", "type": "string", "required": true}], "executor": "sentinelone.initiate_scan", "estimated_duration_seconds": 10},
    {"action_id": "rollback_threat", "generic_action": "rollback_threat", "action_type": "change", "execution_tier": 2, "display_name": "Rollback Threat", "description": "Rollback threat-related changes using SentinelOne Storyline.", "applicable_asset_types": ["server"], "parameters": [{"name": "threat_id", "type": "string", "required": true}], "executor": "sentinelone.rollback_threat", "estimated_duration_seconds": 30},
    {"action_id": "update_policy", "generic_action": "update_policy", "action_type": "change", "execution_tier": 2, "display_name": "Update Policy", "description": "Update agent group policy settings (detection mode, protection level).", "applicable_asset_types": ["server"], "parameters": [{"name": "group_id", "type": "string", "required": true}, {"name": "detection_mode", "type": "string", "required": false, "description": "protect or detect"}, {"name": "protection_level", "type": "string", "required": false}], "executor": "sentinelone.update_policy", "estimated_duration_seconds": 10}
  ]
}
```

- [ ] **Step 2: Create _client.py and __init__.py**

```python
# backend/app/connectors/executors/sentinelone/_client.py
import httpx


def get_client(creds: dict) -> httpx.AsyncClient:
    url = creds["management_url"].rstrip("/")
    return httpx.AsyncClient(
        base_url=f"{url}/web/api/v2.1",
        headers={"Authorization": f"ApiToken {creds['api_token']}", "Content-Type": "application/json"},
        timeout=30.0,
    )
```

```python
# backend/app/connectors/executors/sentinelone/__init__.py
```

- [ ] **Step 3: Create executor files**

```python
# backend/app/connectors/executors/sentinelone/discover_agents.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_agents", "agents": [
            {"id": "mock-agent-1", "hostname": "mock-server", "os": "Linux", "version": "22.1.0", "status": "online", "threat_count": 0}
        ], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/agents", params={"limit": 1000})
        resp.raise_for_status()
        agents = [{"id": a["id"], "hostname": a.get("computerName"), "os": a.get("osName"), "version": a.get("agentVersion"), "status": a.get("isActive")} for a in resp.json().get("data", [])]
    return {"action": "discover_agents", "agents": agents, "count": len(agents)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/sentinelone/discover_threats.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_threats", "threats": [], "count": 0}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/threats", params={"limit": 1000, "resolved": "false"})
        resp.raise_for_status()
        threats = [{"id": t["id"], "name": t.get("threatInfo", {}).get("threatName"), "severity": t.get("threatInfo", {}).get("confidenceLevel"), "agent_id": t.get("agentDetectionInfo", {}).get("agentId")} for t in resp.json().get("data", [])]
    return {"action": "discover_threats", "threats": threats, "count": len(threats)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/sentinelone/discover_groups.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_groups", "groups": [{"id": "mock-group", "name": "Default", "type": "static"}], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get("/groups", params={"limit": 200})
        resp.raise_for_status()
        groups = [{"id": g["id"], "name": g.get("name"), "type": g.get("type")} for g in resp.json().get("data", [])]
    return {"action": "discover_groups", "groups": groups, "count": len(groups)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/sentinelone/isolate_endpoint.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    agent_id = parameters["agent_id"]
    if not creds:
        return {"action": "isolate_endpoint", "agent_id": agent_id, "isolated": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/agents/actions/disconnect", json={"filter": {"ids": [agent_id]}})
        resp.raise_for_status()
    return {"action": "isolate_endpoint", "agent_id": agent_id, "isolated": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.sentinelone.reconnect_endpoint import execute as reconnect
    return await reconnect(parameters, [], connector)
```

```python
# backend/app/connectors/executors/sentinelone/reconnect_endpoint.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    agent_id = parameters["agent_id"]
    if not creds:
        return {"action": "reconnect_endpoint", "agent_id": agent_id, "isolated": False}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/agents/actions/connect", json={"filter": {"ids": [agent_id]}})
        resp.raise_for_status()
    return {"action": "reconnect_endpoint", "agent_id": agent_id, "isolated": False}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "reconnect rollback would isolate — use isolate_endpoint explicitly"}
```

```python
# backend/app/connectors/executors/sentinelone/kill_process.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    agent_id = parameters["agent_id"]
    if not creds:
        return {"action": "kill_process", "agent_id": agent_id, "process": parameters.get("process_name"), "killed": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/agents/actions/kill-process", json={"filter": {"ids": [agent_id]}, "data": {"processName": parameters["process_name"]}})
        resp.raise_for_status()
    return {"action": "kill_process", "agent_id": agent_id, "process": parameters["process_name"], "killed": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "killed processes cannot be restarted automatically"}
```

```python
# backend/app/connectors/executors/sentinelone/quarantine_file.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    agent_id = parameters["agent_id"]
    file_hash = parameters["file_hash"]
    if not creds:
        return {"action": "quarantine_file", "agent_id": agent_id, "file_hash": file_hash, "quarantined": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/threats/actions/quarantine", json={"filter": {"agentIds": [agent_id]}, "data": {"hash": file_hash}})
        resp.raise_for_status()
    return {"action": "quarantine_file", "agent_id": agent_id, "file_hash": file_hash, "quarantined": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "unquarantine requires manual action"}
```

```python
# backend/app/connectors/executors/sentinelone/initiate_scan.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    agent_id = parameters["agent_id"]
    if not creds:
        return {"action": "initiate_scan", "agent_id": agent_id, "scan_started": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/agents/actions/initiate-scan", json={"filter": {"ids": [agent_id]}})
        resp.raise_for_status()
    return {"action": "initiate_scan", "agent_id": agent_id, "scan_started": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot cancel an initiated scan"}
```

```python
# backend/app/connectors/executors/sentinelone/rollback_threat.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    threat_id = parameters["threat_id"]
    if not creds:
        return {"action": "rollback_threat", "threat_id": threat_id, "rolled_back": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post(f"/threats/actions/rollback-remediation", json={"filter": {"ids": [threat_id]}})
        resp.raise_for_status()
    return {"action": "rollback_threat", "threat_id": threat_id, "rolled_back": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "threat rollback cannot be undone"}
```

```python
# backend/app/connectors/executors/sentinelone/update_policy.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    group_id = parameters["group_id"]
    if not creds:
        return {"action": "update_policy", "group_id": group_id, "updated": True}
    from ._client import get_client
    policy_data = {}
    if parameters.get("detection_mode"):
        policy_data["detectionMode"] = parameters["detection_mode"]
    async with get_client(creds) as client:
        resp = await client.put(f"/groups/{group_id}/policy", json=policy_data)
        resp.raise_for_status()
    return {"action": "update_policy", "group_id": group_id, "updated": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "previous policy state not captured — restore manually"}
```

- [ ] **Step 4: Commit SentinelOne connector**

```bash
git add backend/app/connectors/catalog/sentinelone.json backend/app/connectors/executors/sentinelone/
git commit -m "feat: add SentinelOne EDR connector with 10 actions"
```

---

### Task 3: Microsoft Defender for Endpoint Connector

- [ ] **Step 1: Create catalog defender_endpoint.json**

```json
{
  "connector_type": "defender_endpoint",
  "display_name": "Microsoft Defender for Endpoint",
  "credential_fields": [
    {"name": "tenant_id", "label": "Tenant ID", "type": "string", "required": true},
    {"name": "client_id", "label": "Client ID", "type": "string", "required": true},
    {"name": "client_secret", "label": "Client Secret", "type": "password", "required": true}
  ],
  "actions": [
    {"action_id": "discover_machines", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Machines", "description": "All managed machines: hostname, OS, health status, risk level, exposure level, last seen", "applicable_asset_types": ["server"], "parameters": [], "executor": "defender_endpoint.discover_machines", "estimated_duration_seconds": 30},
    {"action_id": "discover_alerts", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Alerts", "description": "Active security alerts: severity, title, machine, status, assigned to — enriches assets", "applicable_asset_types": ["server"], "parameters": [], "executor": "defender_endpoint.discover_alerts", "estimated_duration_seconds": 30},
    {"action_id": "discover_vulnerabilities", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Vulnerabilities", "description": "Vulnerability findings per machine via Defender TVM", "applicable_asset_types": ["server"], "parameters": [], "executor": "defender_endpoint.discover_vulnerabilities", "estimated_duration_seconds": 60},
    {"action_id": "discover_software", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Software", "description": "Installed software with vulnerability counts", "applicable_asset_types": ["server"], "parameters": [], "executor": "defender_endpoint.discover_software", "estimated_duration_seconds": 30},
    {"action_id": "isolate_machine", "generic_action": "isolate_endpoint", "action_type": "change", "execution_tier": 2, "display_name": "Isolate Machine", "description": "Network isolate a machine. Rollback: unisolate.", "applicable_asset_types": ["server"], "parameters": [{"name": "machine_id", "type": "string", "required": true}, {"name": "comment", "type": "string", "required": false}], "executor": "defender_endpoint.isolate_machine", "estimated_duration_seconds": 30},
    {"action_id": "unisolate_machine", "generic_action": "reconnect_endpoint", "action_type": "change", "execution_tier": 2, "display_name": "Unisolate Machine", "description": "Remove network isolation.", "applicable_asset_types": ["server"], "parameters": [{"name": "machine_id", "type": "string", "required": true}], "executor": "defender_endpoint.unisolate_machine", "estimated_duration_seconds": 30},
    {"action_id": "restrict_app_execution", "generic_action": "restrict_execution", "action_type": "change", "execution_tier": 2, "display_name": "Restrict App Execution", "description": "Restrict code execution to Microsoft-signed binaries only.", "applicable_asset_types": ["server"], "parameters": [{"name": "machine_id", "type": "string", "required": true}], "executor": "defender_endpoint.restrict_app_execution", "estimated_duration_seconds": 30},
    {"action_id": "run_antivirus_scan", "generic_action": "initiate_scan", "action_type": "change", "execution_tier": 2, "display_name": "Run Antivirus Scan", "description": "Trigger a full antivirus scan.", "applicable_asset_types": ["server"], "parameters": [{"name": "machine_id", "type": "string", "required": true}, {"name": "scan_type", "type": "string", "required": false, "description": "Full or Quick"}], "executor": "defender_endpoint.run_antivirus_scan", "estimated_duration_seconds": 10},
    {"action_id": "initiate_investigation", "generic_action": "initiate_investigation", "action_type": "change", "execution_tier": 2, "display_name": "Initiate Investigation", "description": "Create automated investigation on a machine.", "applicable_asset_types": ["server"], "parameters": [{"name": "machine_id", "type": "string", "required": true}], "executor": "defender_endpoint.initiate_investigation", "estimated_duration_seconds": 15},
    {"action_id": "get_machine_actions", "generic_action": "get_status", "action_type": "change", "execution_tier": 1, "display_name": "Get Machine Actions", "description": "Retrieve pending/running machine actions.", "applicable_asset_types": ["server"], "parameters": [{"name": "machine_id", "type": "string", "required": true}], "executor": "defender_endpoint.get_machine_actions", "estimated_duration_seconds": 5}
  ]
}
```

- [ ] **Step 2: Create _client.py**

```python
# backend/app/connectors/executors/defender_endpoint/_client.py
import msal
import httpx

API_BASE = "https://api.securitycenter.microsoft.com/api"


async def get_access_token(creds: dict) -> str:
    app = msal.ConfidentialClientApplication(
        creds["client_id"],
        authority=f"https://login.microsoftonline.com/{creds['tenant_id']}",
        client_credential=creds["client_secret"],
    )
    result = app.acquire_token_for_client(scopes=["https://api.securitycenter.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Token acquisition failed: {result.get('error_description')}")
    return result["access_token"]


def get_client(token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API_BASE, headers={"Authorization": f"Bearer {token}"}, timeout=30.0)
```

```python
# backend/app/connectors/executors/defender_endpoint/__init__.py
```

- [ ] **Step 3: Create executor files**

Create these files following the real-if-creds/mock-if-not pattern:

```python
# backend/app/connectors/executors/defender_endpoint/discover_machines.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_machines", "machines": [
            {"id": "mock-machine-1", "hostname": "mock-server", "os": "Windows 10", "health_status": "Active", "risk_level": "Medium", "exposure_level": "Medium"}
        ], "count": 1}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.get("/machines", params={"$top": 1000})
        resp.raise_for_status()
        machines = [{"id": m["id"], "hostname": m.get("computerDnsName"), "os": m.get("osPlatform"), "health_status": m.get("healthStatus"), "risk_level": m.get("riskScore"), "exposure_level": m.get("exposureLevel")} for m in resp.json().get("value", [])]
    return {"action": "discover_machines", "machines": machines, "count": len(machines)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/defender_endpoint/discover_alerts.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_alerts", "alerts": [], "count": 0}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.get("/alerts", params={"$filter": "status ne 'Resolved'", "$top": 1000})
        resp.raise_for_status()
        alerts = [{"id": a["id"], "title": a.get("title"), "severity": a.get("severity"), "machine_id": a.get("machineId"), "status": a.get("status")} for a in resp.json().get("value", [])]
    return {"action": "discover_alerts", "alerts": alerts, "count": len(alerts)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/defender_endpoint/discover_vulnerabilities.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_vulnerabilities", "vulnerabilities": [], "count": 0}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.get("/vulnerabilities/machinesVulnerabilities", params={"$top": 1000})
        resp.raise_for_status()
        vulns = [{"cve_id": v.get("cveId"), "machine_id": v.get("machineId"), "severity": v.get("severity")} for v in resp.json().get("value", [])]
    return {"action": "discover_vulnerabilities", "vulnerabilities": vulns, "count": len(vulns)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/defender_endpoint/discover_software.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_software", "software": [], "count": 0}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.get("/software", params={"$top": 1000})
        resp.raise_for_status()
        software = [{"id": s.get("id"), "name": s.get("name"), "vendor": s.get("vendor"), "vulnerabilities": s.get("vulnerabilitiesCount", 0)} for s in resp.json().get("value", [])]
    return {"action": "discover_software", "software": software, "count": len(software)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/defender_endpoint/isolate_machine.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    machine_id = parameters["machine_id"]
    if not creds:
        return {"action": "isolate_machine", "machine_id": machine_id, "status": "Succeeded"}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.post(f"/machines/{machine_id}/isolate", json={"Comment": parameters.get("comment", "Isolated by Nexplane"), "IsolationType": "Full"})
        resp.raise_for_status()
        action = resp.json()
    return {"action": "isolate_machine", "machine_id": machine_id, "action_id": action.get("id"), "status": action.get("status")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.defender_endpoint.unisolate_machine import execute as unisolate
    return await unisolate(parameters, [], connector)
```

```python
# backend/app/connectors/executors/defender_endpoint/unisolate_machine.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    machine_id = parameters["machine_id"]
    if not creds:
        return {"action": "unisolate_machine", "machine_id": machine_id, "status": "Succeeded"}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.post(f"/machines/{machine_id}/unisolate", json={"Comment": "Unisolated by Nexplane"})
        resp.raise_for_status()
        action = resp.json()
    return {"action": "unisolate_machine", "machine_id": machine_id, "status": action.get("status")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "unisolate rollback would isolate — use isolate_machine explicitly"}
```

```python
# backend/app/connectors/executors/defender_endpoint/restrict_app_execution.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    machine_id = parameters["machine_id"]
    if not creds:
        return {"action": "restrict_app_execution", "machine_id": machine_id, "status": "Succeeded"}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.post(f"/machines/{machine_id}/restrictCodeExecution", json={"Comment": "Restricted by Nexplane"})
        resp.raise_for_status()
    return {"action": "restrict_app_execution", "machine_id": machine_id, "status": "Succeeded"}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "remove restriction via removeCodeExecutionRestriction action manually"}
```

```python
# backend/app/connectors/executors/defender_endpoint/run_antivirus_scan.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    machine_id = parameters["machine_id"]
    scan_type = parameters.get("scan_type", "Full")
    if not creds:
        return {"action": "run_antivirus_scan", "machine_id": machine_id, "scan_type": scan_type, "started": True}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.post(f"/machines/{machine_id}/runAntiVirusScan", json={"Comment": "Scan triggered by Nexplane", "ScanType": scan_type})
        resp.raise_for_status()
    return {"action": "run_antivirus_scan", "machine_id": machine_id, "scan_type": scan_type, "started": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot cancel an initiated AV scan"}
```

```python
# backend/app/connectors/executors/defender_endpoint/initiate_investigation.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    machine_id = parameters["machine_id"]
    if not creds:
        return {"action": "initiate_investigation", "machine_id": machine_id, "investigation_id": "mock-inv-1"}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.post(f"/machines/{machine_id}/startInvestigation", json={"Comment": "Investigation triggered by Nexplane"})
        resp.raise_for_status()
        result = resp.json()
    return {"action": "initiate_investigation", "machine_id": machine_id, "investigation_id": result.get("investigationId")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "investigations cannot be cancelled"}
```

```python
# backend/app/connectors/executors/defender_endpoint/get_machine_actions.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    machine_id = parameters["machine_id"]
    if not creds:
        return {"action": "get_machine_actions", "machine_id": machine_id, "actions": [], "count": 0}
    from ._client import get_access_token, get_client
    token = await get_access_token(creds)
    async with get_client(token) as client:
        resp = await client.get("/machineactions", params={"$filter": f"machineId eq '{machine_id}'"})
        resp.raise_for_status()
        actions = [{"id": a["id"], "type": a.get("type"), "status": a.get("status"), "created": a.get("creationDateTimeUtc")} for a in resp.json().get("value", [])]
    return {"action": "get_machine_actions", "machine_id": machine_id, "actions": actions, "count": len(actions)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "status check has no rollback"}
```

- [ ] **Step 4: Commit Defender connector**

```bash
git add backend/app/connectors/catalog/defender_endpoint.json backend/app/connectors/executors/defender_endpoint/
git commit -m "feat: add Microsoft Defender for Endpoint connector with 10 actions"
```

---

### Task 4: HashiCorp Vault Connector

- [ ] **Step 1: Create catalog hashicorp_vault.json**

```json
{
  "connector_type": "hashicorp_vault",
  "display_name": "HashiCorp Vault",
  "credential_fields": [
    {"name": "vault_addr", "label": "Vault Address", "type": "string", "required": true, "placeholder": "https://vault.example.com:8200"},
    {"name": "token", "label": "Vault Token", "type": "password", "required": false},
    {"name": "role_id", "label": "AppRole Role ID", "type": "string", "required": false},
    {"name": "secret_id", "label": "AppRole Secret ID", "type": "password", "required": false},
    {"name": "namespace", "label": "Vault Namespace (Enterprise)", "type": "string", "required": false}
  ],
  "actions": [
    {"action_id": "discover_secret_engines", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Secret Engines", "description": "Enabled secret engines: path, type, config", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "hashicorp_vault.discover_secret_engines", "estimated_duration_seconds": 10},
    {"action_id": "discover_auth_methods", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Auth Methods", "description": "Enabled auth methods and their configs", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "hashicorp_vault.discover_auth_methods", "estimated_duration_seconds": 10},
    {"action_id": "discover_policies", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Policies", "description": "Vault ACL policies", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "hashicorp_vault.discover_policies", "estimated_duration_seconds": 10},
    {"action_id": "discover_leases", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Leases", "description": "Active leases: secret path, TTL, renewable", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "hashicorp_vault.discover_leases", "estimated_duration_seconds": 15},
    {"action_id": "rotate_secret", "generic_action": "rotate_credentials", "action_type": "change", "execution_tier": 2, "display_name": "Rotate Secret", "description": "Rotate a secret at a given path (write new value).", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "secret_path", "type": "string", "required": true}, {"name": "new_value", "type": "object", "required": true}], "executor": "hashicorp_vault.rotate_secret", "estimated_duration_seconds": 5},
    {"action_id": "revoke_lease", "generic_action": "revoke_credential", "action_type": "change", "execution_tier": 2, "display_name": "Revoke Lease", "description": "Revoke a specific secret lease.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "lease_id", "type": "string", "required": true}], "executor": "hashicorp_vault.revoke_lease", "estimated_duration_seconds": 5},
    {"action_id": "revoke_all_leases", "generic_action": "revoke_credential", "action_type": "change", "execution_tier": 3, "display_name": "Revoke All Leases", "description": "Revoke all leases for a given secret path prefix.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "secret_path_prefix", "type": "string", "required": true}], "executor": "hashicorp_vault.revoke_all_leases", "estimated_duration_seconds": 15, "blast_radius_hint": "mass_revoke"},
    {"action_id": "create_policy", "generic_action": "create_policy", "action_type": "change", "execution_tier": 2, "display_name": "Create Policy", "description": "Create or update a Vault ACL policy.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "policy_name", "type": "string", "required": true}, {"name": "policy_hcl", "type": "string", "required": true}], "executor": "hashicorp_vault.create_policy", "estimated_duration_seconds": 5},
    {"action_id": "delete_policy", "generic_action": "delete_policy", "action_type": "change", "execution_tier": 2, "display_name": "Delete Policy", "description": "Delete a Vault ACL policy.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "policy_name", "type": "string", "required": true}], "executor": "hashicorp_vault.delete_policy", "estimated_duration_seconds": 5},
    {"action_id": "seal_vault", "generic_action": "emergency_lockdown", "action_type": "change", "execution_tier": 3, "display_name": "Seal Vault", "description": "Seal the Vault (emergency lockdown). Rollback: unseal requires unseal keys out-of-band.", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "hashicorp_vault.seal_vault", "estimated_duration_seconds": 5, "blast_radius_hint": "vault_seal"},
    {"action_id": "enable_audit_device", "generic_action": "enable_logging", "action_type": "change", "execution_tier": 2, "display_name": "Enable Audit Device", "description": "Enable an audit log device (file or syslog).", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "path", "type": "string", "required": true}, {"name": "type", "type": "string", "required": true, "description": "file or syslog"}, {"name": "options", "type": "object", "required": false}], "executor": "hashicorp_vault.enable_audit_device", "estimated_duration_seconds": 5}
  ]
}
```

- [ ] **Step 2: Create _client.py**

```python
# backend/app/connectors/executors/hashicorp_vault/_client.py
import hvac


def get_vault_client(creds: dict) -> hvac.Client:
    kwargs = {"url": creds["vault_addr"]}
    if creds.get("namespace"):
        kwargs["namespace"] = creds["namespace"]

    client = hvac.Client(**kwargs)

    if creds.get("token"):
        client.token = creds["token"]
    elif creds.get("role_id") and creds.get("secret_id"):
        result = client.auth.approle.login(role_id=creds["role_id"], secret_id=creds["secret_id"])
        client.token = result["auth"]["client_token"]
    else:
        raise ValueError("Either token or role_id+secret_id must be provided")

    return client
```

```python
# backend/app/connectors/executors/hashicorp_vault/__init__.py
```

- [ ] **Step 3: Create executor files**

```python
# backend/app/connectors/executors/hashicorp_vault/discover_secret_engines.py
import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_secret_engines", "engines": [{"path": "secret/", "type": "kv", "version": 2}], "count": 1}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    mounts = await loop.run_in_executor(None, lambda: client.sys.list_mounted_secrets_engines())
    engines = [{"path": path, "type": info.get("type"), "description": info.get("description")} for path, info in mounts.items()]
    return {"action": "discover_secret_engines", "engines": engines, "count": len(engines)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/hashicorp_vault/discover_auth_methods.py
import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_auth_methods", "methods": [{"path": "token/", "type": "token"}], "count": 1}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    methods = await loop.run_in_executor(None, lambda: client.sys.list_auth_methods())
    result = [{"path": path, "type": info.get("type")} for path, info in methods.items()]
    return {"action": "discover_auth_methods", "methods": result, "count": len(result)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/hashicorp_vault/discover_policies.py
import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_policies", "policies": ["default", "root"], "count": 2}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    policies = await loop.run_in_executor(None, lambda: client.sys.list_policies()["data"]["policies"])
    return {"action": "discover_policies", "policies": policies, "count": len(policies)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/hashicorp_vault/discover_leases.py
import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_leases", "leases": [], "count": 0}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    try:
        result = await loop.run_in_executor(None, lambda: client.sys.list_leases(prefix=""))
        leases = result.get("data", {}).get("keys", [])
    except Exception:
        leases = []
    return {"action": "discover_leases", "leases": leases, "count": len(leases)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/hashicorp_vault/rotate_secret.py
import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    secret_path = parameters["secret_path"]
    if not creds:
        return {"action": "rotate_secret", "secret_path": secret_path, "rotated": True}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    await loop.run_in_executor(None, lambda: client.secrets.kv.v2.create_or_update_secret(path=secret_path, secret=parameters["new_value"]))
    return {"action": "rotate_secret", "secret_path": secret_path, "rotated": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore previous secret value manually"}
```

```python
# backend/app/connectors/executors/hashicorp_vault/revoke_lease.py
import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    lease_id = parameters["lease_id"]
    if not creds:
        return {"action": "revoke_lease", "lease_id": lease_id, "revoked": True}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    await loop.run_in_executor(None, lambda: client.sys.revoke_lease(lease_id=lease_id))
    return {"action": "revoke_lease", "lease_id": lease_id, "revoked": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "revoked leases cannot be restored"}
```

```python
# backend/app/connectors/executors/hashicorp_vault/revoke_all_leases.py
import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    prefix = parameters["secret_path_prefix"]
    if not creds:
        return {"action": "revoke_all_leases", "prefix": prefix, "revoked": True}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    await loop.run_in_executor(None, lambda: client.sys.revoke_prefix(prefix=prefix))
    return {"action": "revoke_all_leases", "prefix": prefix, "revoked": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "revoked leases cannot be restored"}
```

```python
# backend/app/connectors/executors/hashicorp_vault/create_policy.py
import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    policy_name = parameters["policy_name"]
    if not creds:
        return {"action": "create_policy", "policy_name": policy_name, "created": True}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    await loop.run_in_executor(None, lambda: client.sys.create_or_update_policy(name=policy_name, policy=parameters["policy_hcl"]))
    return {"action": "create_policy", "policy_name": policy_name, "created": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.hashicorp_vault.delete_policy import execute as delete
    return await delete(parameters, [], connector)
```

```python
# backend/app/connectors/executors/hashicorp_vault/delete_policy.py
import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    policy_name = parameters["policy_name"]
    if not creds:
        return {"action": "delete_policy", "policy_name": policy_name, "deleted": True}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    await loop.run_in_executor(None, lambda: client.sys.delete_policy(name=policy_name))
    return {"action": "delete_policy", "policy_name": policy_name, "deleted": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "re-create policy with original HCL manually"}
```

```python
# backend/app/connectors/executors/hashicorp_vault/seal_vault.py
import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "seal_vault", "sealed": True}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    await loop.run_in_executor(None, lambda: client.sys.seal())
    return {"action": "seal_vault", "sealed": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "unseal requires unseal keys — perform out-of-band"}
```

```python
# backend/app/connectors/executors/hashicorp_vault/enable_audit_device.py
import asyncio

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    path = parameters["path"]
    audit_type = parameters["type"]
    if not creds:
        return {"action": "enable_audit_device", "path": path, "type": audit_type, "enabled": True}
    from ._client import get_vault_client
    loop = asyncio.get_event_loop()
    client = get_vault_client(creds)
    await loop.run_in_executor(None, lambda: client.sys.enable_audit_device(device_type=audit_type, path=path, options=parameters.get("options", {})))
    return {"action": "enable_audit_device", "path": path, "type": audit_type, "enabled": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "disable audit device via Vault CLI to avoid losing audit trail"}
```

- [ ] **Step 4: Commit Vault connector**

```bash
git add backend/app/connectors/catalog/hashicorp_vault.json backend/app/connectors/executors/hashicorp_vault/
git commit -m "feat: add HashiCorp Vault connector with 11 actions"
```

---

### Task 5: GitHub, Kubernetes, Snyk, Qualys Connectors

These 4 connectors follow the same pattern. Create catalog JSON + `_client.py` + `__init__.py` + executor files for each.

- [ ] **Step 1: Create GitHub connector**

Catalog `github.json`: credential fields `token` (password), `org` (string). Actions: `discover_repositories`, `discover_secret_scanning_alerts`, `discover_code_scanning_alerts`, `discover_dependabot_alerts`, `discover_outside_collaborators`, `discover_org_members`, `enable_branch_protection`, `revoke_oauth_token`, `suspend_org_member`, `dismiss_secret_alert`, `enable_secret_scanning`, `enable_dependabot`.

`_client.py`: httpx client with `Authorization: Bearer {token}` to `https://api.github.com`.

Executor pattern:
```python
# backend/app/connectors/executors/github/_client.py
import httpx

def get_client(creds: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url="https://api.github.com",
        headers={"Authorization": f"Bearer {creds['token']}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
        timeout=30.0,
    )
```

Each executor hits the GitHub REST API. Mock returns sensible static data.

- [ ] **Step 2: Create Kubernetes connector**

Catalog `kubernetes.json`: credential fields `kubeconfig` (password, base64-encoded), `context` (string, optional), `namespace` (string, optional). Actions: `discover_nodes`, `discover_namespaces`, `discover_workloads`, `discover_pods`, `discover_rbac`, `discover_network_policies`, `discover_service_accounts`, `discover_secrets`, `delete_pod`, `cordon_node`, `uncordon_node`, `drain_node`, `create_network_policy`, `delete_network_policy`, `patch_deployment`.

`_client.py`: uses `kubernetes` Python client with `config.load_kube_config_from_dict()` from base64-decoded kubeconfig.

```python
# backend/app/connectors/executors/kubernetes/_client.py
import base64
import yaml
from kubernetes import client, config


def get_k8s_clients(creds: dict):
    kubeconfig_b64 = creds["kubeconfig"]
    kubeconfig_yaml = base64.b64decode(kubeconfig_b64).decode("utf-8")
    kubeconfig_dict = yaml.safe_load(kubeconfig_yaml)
    cfg = client.Configuration()
    config.load_kube_config_from_dict(config_dict=kubeconfig_dict, client_configuration=cfg, context=creds.get("context"))
    api_client = client.ApiClient(cfg)
    return {
        "core": client.CoreV1Api(api_client),
        "apps": client.AppsV1Api(api_client),
        "rbac": client.RbacAuthorizationV1Api(api_client),
        "networking": client.NetworkingV1Api(api_client),
    }
```

- [ ] **Step 3: Create Snyk connector**

Catalog `snyk.json`: credential fields `api_token` (password), `org_id` (string). Actions: `discover_projects`, `discover_issues`, `discover_dependencies`, `discover_container_images`, `trigger_test`, `ignore_issue`, `mark_fixed`.

`_client.py`: httpx with `Authorization: token {api_token}` to `https://api.snyk.io/rest/`.

- [ ] **Step 4: Create Qualys connector**

Catalog `qualys.json`: credential fields `api_url` (string), `username` (string), `password` (password). Actions: `discover_hosts`, `discover_vulnerabilities`, `discover_scan_schedules`, `discover_asset_groups`, `launch_scan`, `launch_compliance_scan`, `verify_remediation`, `patch_vulnerability`.

`_client.py`: httpx with Basic auth. Qualys API returns XML — use `xml.etree.ElementTree` to parse responses.

```python
# backend/app/connectors/executors/qualys/_client.py
import httpx
import base64


def get_client(creds: dict) -> httpx.AsyncClient:
    credentials = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
    return httpx.AsyncClient(
        base_url=creds["api_url"].rstrip("/"),
        headers={"Authorization": f"Basic {credentials}", "X-Requested-With": "Nexplane"},
        timeout=60.0,
    )
```

- [ ] **Step 5: Create all executor files for GitHub, K8s, Snyk, Qualys**

For each action in each connector, create an executor file with real-if-creds/mock-if-not pattern. For ingest actions, return mock data lists. For change actions, return mock success. Real implementations call the appropriate SDK/API.

- [ ] **Step 6: Commit remaining connectors**

```bash
git add backend/app/connectors/catalog/github.json backend/app/connectors/catalog/kubernetes.json backend/app/connectors/catalog/snyk.json backend/app/connectors/catalog/qualys.json
git add backend/app/connectors/executors/github/ backend/app/connectors/executors/kubernetes/ backend/app/connectors/executors/snyk/ backend/app/connectors/executors/qualys/
git commit -m "feat: add GitHub, Kubernetes, Snyk, Qualys connectors"
```

---

### Task 6: Frontend Updates

- [ ] **Step 1: Update ConnectorType in api.ts**

Add: `| 'sentinelone' | 'defender_endpoint' | 'hashicorp_vault' | 'github' | 'kubernetes' | 'snyk' | 'qualys'`

- [ ] **Step 2: Update CONNECTOR_LABELS in Connectors.tsx**

```typescript
sentinelone: 'SentinelOne',
defender_endpoint: 'Microsoft Defender for Endpoint',
hashicorp_vault: 'HashiCorp Vault',
github: 'GitHub',
kubernetes: 'Kubernetes',
snyk: 'Snyk',
qualys: 'Qualys',
```

- [ ] **Step 3: Update CONNECTOR_ICONS and AddConnectorModal**

Use appropriate icons from lucide-react: `Shield` for sentinelone/defender, `Lock` for vault, `Github` for github (if available, else `Code`), `Container` or `Box` for kubernetes, `Bug` for snyk/qualys.

- [ ] **Step 4: Verify build**

```bash
docker compose exec frontend npm run build 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/
git commit -m "feat: add 6c security tool connectors to frontend type lists"
```

---

### Task 7: Integration Tests

- [ ] **Step 1: Write tests**

```python
# backend/app/tests/test_connector_6c.py
import pytest


@pytest.mark.asyncio
async def test_sentinelone_isolate_endpoint_mock():
    from app.connectors.executors.sentinelone.isolate_endpoint import execute
    result = await execute({"agent_id": "mock-agent"}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "isolate_endpoint"
    assert result["isolated"] is True


@pytest.mark.asyncio
async def test_sentinelone_isolate_rollback():
    from app.connectors.executors.sentinelone.isolate_endpoint import rollback
    result = await rollback({"agent_id": "mock-agent"}, {}, type("C", (), {"credentials": {}})())
    assert result["action"] == "reconnect_endpoint"


@pytest.mark.asyncio
async def test_vault_discover_secret_engines_mock():
    from app.connectors.executors.hashicorp_vault.discover_secret_engines import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_secret_engines"
    assert isinstance(result["engines"], list)


@pytest.mark.asyncio
async def test_defender_discover_machines_mock():
    from app.connectors.executors.defender_endpoint.discover_machines import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_machines"
    assert result["count"] >= 0
```

- [ ] **Step 2: Run tests**

```bash
docker compose exec backend pytest app/tests/test_connector_6c.py -v
```
Expected: All 4 tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/app/tests/test_connector_6c.py
git commit -m "test: add integration tests for 6c security tool connectors"
```
