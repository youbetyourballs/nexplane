# Connectors 6d: IaC & Configuration Management Connectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 9 IaC and configuration management connectors: Terraform (HCP), Ansible (AWX), CloudFormation, Pulumi, Helm, Azure Bicep, Checkov, SaltStack, Chef InSpec.

**Architecture:** Single Alembic migration (012) adds 9 new enum values. All connectors use REST APIs or existing SDKs — no local CLI tools installed on server (except Checkov which runs as subprocess). Real-if-credentials/mock-if-not pattern.

**Tech Stack:** `requests` (Terraform, Ansible, Pulumi, SaltStack, Chef), `boto3` (CloudFormation), `kubernetes` (Helm), `azure-mgmt-resource` (Bicep), subprocess+`checkov` (Checkov)

---

## File Map

**Backend:**
- Create: `backend/alembic/versions/012_add_new_connector_types_6d.py`
- Modify: `backend/app/models/connector.py`
- Create 9 catalogs and 9 executor directories
- Modify: `backend/requirements.txt` (add `checkov>=3.2.0`)

**Frontend:**
- Modify: `frontend/src/types/api.ts`, `Connectors.tsx`, `AddConnectorModal.tsx`

---

### Task 1: Database Migration + Model Update

- [ ] **Step 1: Create migration 012**

```python
# backend/alembic/versions/012_add_new_connector_types_6d.py
"""add IaC connector types: terraform, ansible, cloudformation, pulumi, helm, bicep, checkov, saltstack, chef_inspec

Revision ID: 012
Revises: 011
Create Date: 2026-05-01
"""
from alembic import op

revision = '012'
down_revision = '011'
branch_labels = None
depends_on = None


def upgrade():
    for val in ['terraform', 'ansible', 'cloudformation', 'pulumi', 'helm', 'bicep', 'checkov', 'saltstack', 'chef_inspec']:
        op.execute(f"ALTER TYPE connector_type ADD VALUE IF NOT EXISTS '{val}'")


def downgrade():
    pass
```

- [ ] **Step 2: Run migration**

```bash
docker compose exec backend alembic upgrade 012
```

- [ ] **Step 3: Update ConnectorType in models/connector.py**

```python
terraform = "terraform"
ansible = "ansible"
cloudformation = "cloudformation"
pulumi = "pulumi"
helm = "helm"
bicep = "bicep"
checkov = "checkov"
saltstack = "saltstack"
chef_inspec = "chef_inspec"
```

- [ ] **Step 4: Add requirements**

Add to `backend/requirements.txt`:
```
checkov>=3.2.0
```

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/012_add_new_connector_types_6d.py backend/app/models/connector.py backend/requirements.txt
git commit -m "feat: add migration 012 for IaC connector types (terraform, ansible, cloudformation, pulumi, helm, bicep, checkov, saltstack, chef_inspec)"
```

---

### Task 2: Terraform (HCP) Connector

- [ ] **Step 1: Create catalog terraform.json**

```json
{
  "connector_type": "terraform",
  "display_name": "Terraform (HCP)",
  "credential_fields": [
    {"name": "api_token", "label": "HCP Terraform API Token", "type": "password", "required": true},
    {"name": "organization", "label": "Organization Name", "type": "string", "required": true},
    {"name": "base_url", "label": "Base URL (optional)", "type": "string", "required": false, "default": "https://app.terraform.io"}
  ],
  "actions": [
    {"action_id": "discover_workspaces", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Workspaces", "description": "All workspaces: name, environment, Terraform version, last run status, VCS repo, resource count", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "terraform.discover_workspaces", "estimated_duration_seconds": 15},
    {"action_id": "discover_runs", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Runs", "description": "Recent runs per workspace: status, triggered by, plan changes, apply time", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "workspace_id", "type": "string", "required": false, "description": "Specific workspace ID, or all if omitted"}], "executor": "terraform.discover_runs", "estimated_duration_seconds": 30},
    {"action_id": "discover_state_resources", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover State Resources", "description": "Resources in workspace state: type, name, provider, attributes", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "workspace_id", "type": "string", "required": true}], "executor": "terraform.discover_state_resources", "estimated_duration_seconds": 30},
    {"action_id": "discover_variables", "generic_action": "discover", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Variables", "description": "Workspace variables: name, category, sensitive flag", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "workspace_id", "type": "string", "required": true}], "executor": "terraform.discover_variables", "estimated_duration_seconds": 10},
    {"action_id": "plan_workspace", "generic_action": "plan", "action_type": "change", "execution_tier": 2, "display_name": "Plan Workspace", "description": "Queue a plan-only run. Returns run ID.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "workspace_id", "type": "string", "required": true}], "executor": "terraform.plan_workspace", "estimated_duration_seconds": 30},
    {"action_id": "apply_workspace", "generic_action": "apply", "action_type": "change", "execution_tier": 3, "display_name": "Apply Workspace", "description": "Queue an apply run with auto-approve. Returns run ID.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "workspace_id", "type": "string", "required": true}], "executor": "terraform.apply_workspace", "estimated_duration_seconds": 60, "blast_radius_hint": "infrastructure_change"},
    {"action_id": "destroy_workspace_resources", "generic_action": "destroy", "action_type": "change", "execution_tier": 3, "display_name": "Destroy Workspace Resources", "description": "Queue a destroy plan + apply. Requires explicit confirmation.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "workspace_id", "type": "string", "required": true}, {"name": "confirm_destroy", "type": "boolean", "required": true, "description": "Must be true to proceed"}], "executor": "terraform.destroy_workspace_resources", "estimated_duration_seconds": 120, "blast_radius_hint": "destructive"},
    {"action_id": "lock_workspace", "generic_action": "lock", "action_type": "change", "execution_tier": 2, "display_name": "Lock Workspace", "description": "Lock a workspace to prevent runs. Rollback: unlock.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "workspace_id", "type": "string", "required": true}, {"name": "reason", "type": "string", "required": false}], "executor": "terraform.lock_workspace", "estimated_duration_seconds": 5},
    {"action_id": "unlock_workspace", "generic_action": "unlock", "action_type": "change", "execution_tier": 2, "display_name": "Unlock Workspace", "description": "Unlock a workspace.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "workspace_id", "type": "string", "required": true}], "executor": "terraform.unlock_workspace", "estimated_duration_seconds": 5},
    {"action_id": "set_variable", "generic_action": "set_variable", "action_type": "change", "execution_tier": 2, "display_name": "Set Variable", "description": "Set or update a workspace variable. Rollback: restore previous value.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "workspace_id", "type": "string", "required": true}, {"name": "key", "type": "string", "required": true}, {"name": "value", "type": "string", "required": true}, {"name": "category", "type": "string", "required": false, "description": "terraform or env"}, {"name": "sensitive", "type": "boolean", "required": false}], "executor": "terraform.set_variable", "estimated_duration_seconds": 5}
  ]
}
```

- [ ] **Step 2: Create _client.py**

```python
# backend/app/connectors/executors/terraform/_client.py
import httpx


def get_client(creds: dict) -> httpx.AsyncClient:
    base = creds.get("base_url", "https://app.terraform.io").rstrip("/")
    return httpx.AsyncClient(
        base_url=f"{base}/api/v2",
        headers={"Authorization": f"Bearer {creds['api_token']}", "Content-Type": "application/vnd.api+json"},
        timeout=60.0,
    )
```

```python
# backend/app/connectors/executors/terraform/__init__.py
```

- [ ] **Step 3: Create executor files**

```python
# backend/app/connectors/executors/terraform/discover_workspaces.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_workspaces", "workspaces": [
            {"id": "ws-mock", "name": "production", "environment": "production", "resource_count": 42, "last_run_status": "applied"}
        ], "count": 1}
    from ._client import get_client
    org = creds["organization"]
    async with get_client(creds) as client:
        resp = await client.get(f"/organizations/{org}/workspaces", params={"page[size]": 100})
        resp.raise_for_status()
        items = resp.json().get("data", [])
        workspaces = [{"id": w["id"], "name": w["attributes"]["name"], "environment": w["attributes"].get("environment"), "resource_count": w["attributes"].get("resource-count", 0), "last_run_status": w["attributes"].get("latest-change-at")} for w in items]
    return {"action": "discover_workspaces", "workspaces": workspaces, "count": len(workspaces)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/terraform/discover_runs.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_runs", "runs": [
            {"id": "run-mock", "workspace_id": "ws-mock", "status": "applied", "triggered_by": "user@example.com"}
        ], "count": 1}
    from ._client import get_client
    workspace_id = parameters.get("workspace_id")
    async with get_client(creds) as client:
        if workspace_id:
            resp = await client.get(f"/workspaces/{workspace_id}/runs", params={"page[size]": 50})
        else:
            resp = await client.get(f"/organizations/{creds['organization']}/runs", params={"page[size]": 50})
        resp.raise_for_status()
        runs = [{"id": r["id"], "status": r["attributes"]["status"], "created": r["attributes"].get("created-at")} for r in resp.json().get("data", [])]
    return {"action": "discover_runs", "runs": runs, "count": len(runs)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/terraform/discover_state_resources.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    if not creds:
        return {"action": "discover_state_resources", "resources": [
            {"name": "aws_instance.web", "type": "aws_instance", "provider": "aws"}
        ], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get(f"/workspaces/{workspace_id}/current-state-version-outputs")
        resp.raise_for_status()
        resources = resp.json().get("data", [])
    return {"action": "discover_state_resources", "resources": resources, "count": len(resources)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/terraform/discover_variables.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    if not creds:
        return {"action": "discover_variables", "variables": [
            {"id": "var-mock", "key": "environment", "category": "terraform", "sensitive": False}
        ], "count": 1}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.get(f"/workspaces/{workspace_id}/vars")
        resp.raise_for_status()
        vars_data = [{"id": v["id"], "key": v["attributes"]["key"], "category": v["attributes"]["category"], "sensitive": v["attributes"]["sensitive"]} for v in resp.json().get("data", [])]
    return {"action": "discover_variables", "variables": vars_data, "count": len(vars_data)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/terraform/plan_workspace.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    if not creds:
        return {"action": "plan_workspace", "workspace_id": workspace_id, "run_id": "run-mock-plan", "status": "planning"}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/runs", json={"data": {"attributes": {"plan-only": True}, "relationships": {"workspace": {"data": {"type": "workspaces", "id": workspace_id}}}, "type": "runs"}})
        resp.raise_for_status()
        run = resp.json()["data"]
    return {"action": "plan_workspace", "workspace_id": workspace_id, "run_id": run["id"], "status": run["attributes"]["status"]}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "plan-only run has no rollback"}
```

```python
# backend/app/connectors/executors/terraform/apply_workspace.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    if not creds:
        return {"action": "apply_workspace", "workspace_id": workspace_id, "run_id": "run-mock-apply", "status": "applying"}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/runs", json={"data": {"attributes": {"auto-apply": True}, "relationships": {"workspace": {"data": {"type": "workspaces", "id": workspace_id}}}, "type": "runs"}})
        resp.raise_for_status()
        run = resp.json()["data"]
    return {"action": "apply_workspace", "workspace_id": workspace_id, "run_id": run["id"], "status": run["attributes"]["status"]}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "apply rollback requires a destroy run — use destroy_workspace_resources explicitly"}
```

```python
# backend/app/connectors/executors/terraform/destroy_workspace_resources.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    if not parameters.get("confirm_destroy"):
        return {"action": "destroy_workspace_resources", "error": "confirm_destroy must be true"}
    if not creds:
        return {"action": "destroy_workspace_resources", "workspace_id": workspace_id, "run_id": "run-mock-destroy", "status": "pending"}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post("/runs", json={"data": {"attributes": {"is-destroy": True, "auto-apply": True}, "relationships": {"workspace": {"data": {"type": "workspaces", "id": workspace_id}}}, "type": "runs"}})
        resp.raise_for_status()
        run = resp.json()["data"]
    return {"action": "destroy_workspace_resources", "workspace_id": workspace_id, "run_id": run["id"], "status": run["attributes"]["status"]}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "destroy is irreversible"}
```

```python
# backend/app/connectors/executors/terraform/lock_workspace.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    if not creds:
        return {"action": "lock_workspace", "workspace_id": workspace_id, "locked": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post(f"/workspaces/{workspace_id}/actions/lock", json={"reason": parameters.get("reason", "Locked by Nexplane")})
        resp.raise_for_status()
    return {"action": "lock_workspace", "workspace_id": workspace_id, "locked": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.terraform.unlock_workspace import execute as unlock
    return await unlock(parameters, [], connector)
```

```python
# backend/app/connectors/executors/terraform/unlock_workspace.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    if not creds:
        return {"action": "unlock_workspace", "workspace_id": workspace_id, "locked": False}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.post(f"/workspaces/{workspace_id}/actions/unlock")
        resp.raise_for_status()
    return {"action": "unlock_workspace", "workspace_id": workspace_id, "locked": False}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "unlock rollback would lock — use lock_workspace explicitly"}
```

```python
# backend/app/connectors/executors/terraform/set_variable.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    workspace_id = parameters["workspace_id"]
    key = parameters["key"]
    if not creds:
        return {"action": "set_variable", "workspace_id": workspace_id, "key": key, "set": True}
    from ._client import get_client
    async with get_client(creds) as client:
        # Check if variable already exists
        resp = await client.get(f"/workspaces/{workspace_id}/vars")
        resp.raise_for_status()
        existing = {v["attributes"]["key"]: v["id"] for v in resp.json().get("data", [])}
        var_data = {"data": {"type": "vars", "attributes": {"key": key, "value": parameters["value"], "category": parameters.get("category", "terraform"), "sensitive": parameters.get("sensitive", False)}}}
        if key in existing:
            resp2 = await client.patch(f"/workspaces/{workspace_id}/vars/{existing[key]}", json=var_data)
        else:
            var_data["data"]["relationships"] = {"workspace": {"data": {"type": "workspaces", "id": workspace_id}}}
            resp2 = await client.post("/vars", json=var_data)
        resp2.raise_for_status()
    return {"action": "set_variable", "workspace_id": workspace_id, "key": key, "set": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "previous variable value not captured — restore manually"}
```

- [ ] **Step 4: Commit Terraform connector**

```bash
git add backend/app/connectors/catalog/terraform.json backend/app/connectors/executors/terraform/
git commit -m "feat: add Terraform HCP connector with 10 actions"
```

---

### Task 3: Ansible (AWX), CloudFormation, Pulumi Connectors

- [ ] **Step 1: Create Ansible connector**

Catalog `ansible.json`: credential fields `controller_url` (string), `api_token` (password). Actions: `discover_inventories`, `discover_hosts`, `discover_job_templates`, `discover_jobs`, `launch_job`, `cancel_job`, `run_adhoc_command`, `sync_inventory`, `update_host_variables`.

`_client.py`: httpx with `Authorization: Bearer {api_token}` to `{controller_url}/api/v2/`.

```python
# backend/app/connectors/executors/ansible/_client.py
import httpx

def get_client(creds: dict) -> httpx.AsyncClient:
    url = creds["controller_url"].rstrip("/")
    return httpx.AsyncClient(base_url=f"{url}/api/v2", headers={"Authorization": f"Bearer {creds['api_token']}", "Content-Type": "application/json"}, timeout=60.0)
```

Each executor: GET /inventories, /hosts, /job_templates, /jobs for ingest; POST /job_templates/{id}/launch for `launch_job`; POST /jobs/{id}/cancel for `cancel_job`; etc.

- [ ] **Step 2: Create CloudFormation connector**

Catalog `cloudformation.json`: same credential fields as AWS connector (`access_key_id`, `secret_access_key`, `region`, `session_token`). Actions: `discover_stacks`, `discover_stack_resources`, `detect_stack_drift`, `get_drift_results`, `create_change_set`, `execute_change_set`, `delete_stack`, `update_termination_protection`.

`_client.py`: reuses boto3 pattern.

```python
# backend/app/connectors/executors/cloudformation/_client.py
import boto3

def get_client(creds: dict):
    kwargs = {"aws_access_key_id": creds.get("access_key_id"), "aws_secret_access_key": creds.get("secret_access_key"), "region_name": creds.get("region", "us-east-1")}
    if creds.get("session_token"):
        kwargs["aws_session_token"] = creds["session_token"]
    return boto3.client("cloudformation", **kwargs)
```

- [ ] **Step 3: Create Pulumi connector**

Catalog `pulumi.json`: credential fields `api_token` (password), `organization` (string). Actions: `discover_stacks`, `discover_stack_resources`, `discover_stack_history`, `cancel_update`, `import_resource`, `refresh_stack`.

`_client.py`: httpx with `Authorization: token {api_token}` to `https://api.pulumi.com/api/`.

```python
# backend/app/connectors/executors/pulumi/_client.py
import httpx

def get_client(creds: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="https://api.pulumi.com/api", headers={"Authorization": f"token {creds['api_token']}", "Accept": "application/vnd.pulumi+8", "Content-Type": "application/json"}, timeout=30.0)
```

- [ ] **Step 4: Create all executor files for Ansible, CloudFormation, Pulumi**

For each action, follow the real-if-creds/mock-if-not pattern with appropriate API calls.

- [ ] **Step 5: Commit Ansible, CloudFormation, Pulumi**

```bash
git add backend/app/connectors/catalog/ansible.json backend/app/connectors/catalog/cloudformation.json backend/app/connectors/catalog/pulumi.json
git add backend/app/connectors/executors/ansible/ backend/app/connectors/executors/cloudformation/ backend/app/connectors/executors/pulumi/
git commit -m "feat: add Ansible, CloudFormation, Pulumi connectors"
```

---

### Task 4: Helm, Azure Bicep Connectors

- [ ] **Step 1: Create Helm connector**

Catalog `helm.json`: same credential fields as Kubernetes connector (`kubeconfig` password, `context` string, `namespace` string). Actions: `discover_releases`, `discover_release_history`, `rollback_release`, `uninstall_release`.

Helm stores releases as Kubernetes secrets with type `helm.sh/release.v1`. Read them via the `kubernetes` Python client.

`_client.py`: reuses kubernetes pattern from 6c.

```python
# backend/app/connectors/executors/helm/_client.py
import base64
import yaml
from kubernetes import client, config

def get_k8s_core_client(creds: dict):
    kubeconfig_b64 = creds["kubeconfig"]
    kubeconfig_yaml = base64.b64decode(kubeconfig_b64).decode("utf-8")
    kubeconfig_dict = yaml.safe_load(kubeconfig_yaml)
    cfg = client.Configuration()
    config.load_kube_config_from_dict(config_dict=kubeconfig_dict, client_configuration=cfg, context=creds.get("context"))
    api_client = client.ApiClient(cfg)
    return client.CoreV1Api(api_client)
```

`discover_releases.py`: list secrets with `type=helm.sh/release.v1` across namespaces, decode from gzip+base64 JSON to extract release metadata.

`rollback_release.py`: runs `helm rollback {release} {revision}` as subprocess if helm binary exists; otherwise uses kubernetes secret manipulation.

- [ ] **Step 2: Create Azure Bicep connector**

Catalog `bicep.json`: same credential fields as Azure connector (`tenant_id`, `client_id`, `client_secret`, `subscription_id`). Actions: `discover_deployments`, `discover_deployment_operations`, `validate_template`, `create_deployment`, `delete_deployment`, `cancel_deployment`.

`_client.py`: uses `azure-mgmt-resource` already installed.

```python
# backend/app/connectors/executors/bicep/_client.py
from azure.identity import ClientSecretCredential
from azure.mgmt.resource import ResourceManagementClient

def get_client(creds: dict) -> ResourceManagementClient:
    credential = ClientSecretCredential(tenant_id=creds["tenant_id"], client_id=creds["client_id"], client_secret=creds["client_secret"])
    return ResourceManagementClient(credential, creds["subscription_id"])
```

- [ ] **Step 3: Create executor files for Helm and Bicep**

- [ ] **Step 4: Commit Helm and Bicep**

```bash
git add backend/app/connectors/catalog/helm.json backend/app/connectors/catalog/bicep.json
git add backend/app/connectors/executors/helm/ backend/app/connectors/executors/bicep/
git commit -m "feat: add Helm and Azure Bicep connectors"
```

---

### Task 5: Checkov Connector (Subprocess)

- [ ] **Step 1: Create catalog checkov.json**

```json
{
  "connector_type": "checkov",
  "display_name": "Checkov",
  "credential_fields": [
    {"name": "repo_path", "label": "IaC Repository Path", "type": "string", "required": true, "placeholder": "/app/iac-repos/myrepo"},
    {"name": "framework", "label": "Framework", "type": "string", "required": false, "default": "all", "description": "terraform, cloudformation, kubernetes, arm, or all"}
  ],
  "actions": [
    {"action_id": "scan_iac", "generic_action": "scan", "action_type": "ingest", "execution_tier": 1, "display_name": "Scan IaC", "description": "Run Checkov against repo_path, return findings: check ID, severity, resource, file, guideline", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "checkov.scan_iac", "estimated_duration_seconds": 120},
    {"action_id": "scan_secrets", "generic_action": "scan", "action_type": "ingest", "execution_tier": 1, "display_name": "Scan for Secrets", "description": "Run Checkov secrets detection across all files.", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "checkov.scan_secrets", "estimated_duration_seconds": 120},
    {"action_id": "get_compliance_summary", "generic_action": "compliance_check", "action_type": "ingest", "execution_tier": 1, "display_name": "Get Compliance Summary", "description": "Return pass/fail counts by compliance framework (CIS, NIST, PCI-DSS).", "applicable_asset_types": ["cloud_account"], "parameters": [], "executor": "checkov.get_compliance_summary", "estimated_duration_seconds": 120}
  ]
}
```

- [ ] **Step 2: Create executor files**

```python
# backend/app/connectors/executors/checkov/__init__.py
```

```python
# backend/app/connectors/executors/checkov/scan_iac.py
import asyncio
import json
import subprocess


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "scan_iac", "findings": [
            {"check_id": "CKV_AWS_1", "check_type": "terraform", "resource": "aws_s3_bucket.data", "file": "main.tf", "passed": False, "guideline": "Ensure S3 bucket has access control list (ACL) applied"}
        ], "passed": 5, "failed": 1}
    repo_path = creds["repo_path"]
    framework = creds.get("framework", "all")
    loop = asyncio.get_event_loop()

    def run_checkov():
        cmd = ["checkov", "-d", repo_path, "--framework", framework, "-o", "json", "--quiet"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"results": {"failed_checks": [], "passed_checks": []}}

    data = await loop.run_in_executor(None, run_checkov)
    results = data.get("results", data) if isinstance(data, dict) else {}
    failed = results.get("failed_checks", [])
    passed = results.get("passed_checks", [])
    findings = [{"check_id": c.get("check_id"), "check_type": c.get("check_type"), "resource": c.get("resource"), "file": c.get("file_path"), "passed": False, "guideline": c.get("check_result", {}).get("result", "")} for c in failed]
    return {"action": "scan_iac", "findings": findings, "passed": len(passed), "failed": len(failed)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan has no rollback"}
```

```python
# backend/app/connectors/executors/checkov/scan_secrets.py
import asyncio
import json
import subprocess


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "scan_secrets", "secrets_found": [], "count": 0}
    repo_path = creds["repo_path"]
    loop = asyncio.get_event_loop()

    def run_checkov():
        cmd = ["checkov", "-d", repo_path, "--enable-secret-scan-all-files", "-o", "json", "--quiet"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {}

    data = await loop.run_in_executor(None, run_checkov)
    failed = data.get("results", {}).get("failed_checks", [])
    secrets = [{"check_id": c.get("check_id"), "file": c.get("file_path"), "resource": c.get("resource")} for c in failed]
    return {"action": "scan_secrets", "secrets_found": secrets, "count": len(secrets)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan has no rollback"}
```

```python
# backend/app/connectors/executors/checkov/get_compliance_summary.py
import asyncio
import json
import subprocess


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "get_compliance_summary", "summary": {"CIS": {"passed": 10, "failed": 3}, "NIST": {"passed": 8, "failed": 5}}}
    repo_path = creds["repo_path"]
    framework = creds.get("framework", "all")
    loop = asyncio.get_event_loop()

    def run_checkov():
        cmd = ["checkov", "-d", repo_path, "--framework", framework, "-o", "json", "--quiet", "--compact"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {}

    data = await loop.run_in_executor(None, run_checkov)
    summary = data.get("summary", {})
    return {"action": "get_compliance_summary", "summary": summary, "passed": summary.get("passed", 0), "failed": summary.get("failed", 0)}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "scan has no rollback"}
```

- [ ] **Step 3: Commit Checkov connector**

```bash
git add backend/app/connectors/catalog/checkov.json backend/app/connectors/executors/checkov/
git commit -m "feat: add Checkov IaC scanner connector with 3 scan actions"
```

---

### Task 6: SaltStack and Chef InSpec Connectors

- [ ] **Step 1: Create SaltStack connector**

Catalog `saltstack.json`: credential fields `api_url` (string), `username` (string), `password` (password), `eauth` (string, default `pam`). Actions: `discover_minions`, `discover_jobs`, `run_state`, `run_function`, `sync_minion`, `accept_key`, `reject_key`.

`_client.py`: Session-based auth — POST `/login` to get token, then include token in headers.

```python
# backend/app/connectors/executors/saltstack/_client.py
import httpx


async def get_token(creds: dict) -> str:
    url = creds["api_url"].rstrip("/")
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(f"{url}/login", json={"username": creds["username"], "password": creds["password"], "eauth": creds.get("eauth", "pam")})
        resp.raise_for_status()
        return resp.json()["return"][0]["token"]


def get_client(creds: dict, token: str) -> httpx.AsyncClient:
    url = creds["api_url"].rstrip("/")
    return httpx.AsyncClient(base_url=url, headers={"X-Auth-Token": token, "Accept": "application/json"}, timeout=60.0, verify=False)
```

- [ ] **Step 2: Create SaltStack executor files**

```python
# backend/app/connectors/executors/saltstack/__init__.py
```

```python
# backend/app/connectors/executors/saltstack/discover_minions.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_minions", "minions": [
            {"id": "minion-001", "os": "CentOS 7", "ip": "10.0.0.5", "kernel": "3.10.0"}
        ], "count": 1}
    from ._client import get_token, get_client
    token = await get_token(creds)
    async with get_client(creds, token) as client:
        resp = await client.post("/", json=[{"client": "local", "tgt": "*", "fun": "grains.items"}])
        resp.raise_for_status()
        data = resp.json().get("return", [{}])[0]
        minions = [{"id": mid, "os": grains.get("os", ""), "ip": grains.get("ipv4", [""])[0], "kernel": grains.get("kernelrelease", "")} for mid, grains in data.items()]
    return {"action": "discover_minions", "minions": minions, "count": len(minions)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/saltstack/discover_jobs.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_jobs", "jobs": [], "count": 0}
    from ._client import get_token, get_client
    token = await get_token(creds)
    async with get_client(creds, token) as client:
        resp = await client.get("/jobs")
        resp.raise_for_status()
        jobs_data = resp.json().get("return", [{}])[0]
        jobs = [{"jid": jid, "function": info.get("Function"), "target": info.get("Target"), "started": info.get("StartTime")} for jid, info in jobs_data.items()]
    return {"action": "discover_jobs", "jobs": jobs[:100], "count": len(jobs)}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

```python
# backend/app/connectors/executors/saltstack/run_state.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    target = parameters.get("target", "*")
    state = parameters.get("state")
    if not creds:
        return {"action": "run_state", "target": target, "state": state, "job_id": "mock-jid-001"}
    from ._client import get_token, get_client
    token = await get_token(creds)
    async with get_client(creds, token) as client:
        resp = await client.post("/", json=[{"client": "local_async", "tgt": target, "fun": "state.apply", "arg": [state] if state else []}])
        resp.raise_for_status()
        result = resp.json().get("return", [{}])[0]
    return {"action": "run_state", "target": target, "state": state, "job_id": result.get("jid")}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "state runs cannot be automatically reversed"}
```

```python
# backend/app/connectors/executors/saltstack/run_function.py
ALLOWED_FUNCTIONS = {"sys.doc", "test.ping", "pkg.list_pkgs", "service.status"}

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    fun = parameters.get("function")
    target = parameters.get("target", "*")
    if fun not in ALLOWED_FUNCTIONS:
        return {"action": "run_function", "error": f"Function '{fun}' not in allowlist: {ALLOWED_FUNCTIONS}"}
    if not creds:
        return {"action": "run_function", "target": target, "function": fun, "result": {"mock-minion": True}}
    from ._client import get_token, get_client
    token = await get_token(creds)
    async with get_client(creds, token) as client:
        resp = await client.post("/", json=[{"client": "local", "tgt": target, "fun": fun}])
        resp.raise_for_status()
        result = resp.json().get("return", [{}])[0]
    return {"action": "run_function", "target": target, "function": fun, "result": result}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "function execution cannot be reversed"}
```

```python
# backend/app/connectors/executors/saltstack/sync_minion.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    target = parameters.get("target", "*")
    if not creds:
        return {"action": "sync_minion", "target": target, "synced": True}
    from ._client import get_token, get_client
    token = await get_token(creds)
    async with get_client(creds, token) as client:
        resp = await client.post("/", json=[{"client": "local_async", "tgt": target, "fun": "saltutil.sync_all"}])
        resp.raise_for_status()
    return {"action": "sync_minion", "target": target, "synced": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "sync cannot be reversed"}
```

```python
# backend/app/connectors/executors/saltstack/accept_key.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    minion_id = parameters["minion_id"]
    if not creds:
        return {"action": "accept_key", "minion_id": minion_id, "accepted": True}
    from ._client import get_token, get_client
    token = await get_token(creds)
    async with get_client(creds, token) as client:
        resp = await client.post("/keys", json={"id": minion_id, "include_accepted": True})
        resp.raise_for_status()
    return {"action": "accept_key", "minion_id": minion_id, "accepted": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.saltstack.reject_key import execute as reject
    return await reject(parameters, [], connector)
```

```python
# backend/app/connectors/executors/saltstack/reject_key.py
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    minion_id = parameters["minion_id"]
    if not creds:
        return {"action": "reject_key", "minion_id": minion_id, "rejected": True}
    from ._client import get_token, get_client
    token = await get_token(creds)
    async with get_client(creds, token) as client:
        resp = await client.delete(f"/keys/{minion_id}")
        resp.raise_for_status()
    return {"action": "reject_key", "minion_id": minion_id, "rejected": True}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "rejected key cannot be automatically re-accepted"}
```

- [ ] **Step 3: Create Chef InSpec connector**

Catalog `chef_inspec.json`: credential fields `automate_url` (string), `api_token` (password). Actions: `discover_nodes`, `discover_compliance_profiles`, `discover_compliance_results`, `run_compliance_scan`, `assign_profile`.

`_client.py`: httpx with `api-token: {api_token}` header.

```python
# backend/app/connectors/executors/chef_inspec/_client.py
import httpx

def get_client(creds: dict) -> httpx.AsyncClient:
    url = creds["automate_url"].rstrip("/")
    return httpx.AsyncClient(base_url=f"{url}/api/v0", headers={"api-token": creds["api_token"], "Content-Type": "application/json"}, timeout=30.0)
```

Create executor files: `discover_nodes.py`, `discover_compliance_profiles.py`, `discover_compliance_results.py`, `run_compliance_scan.py`, `assign_profile.py`.

- [ ] **Step 4: Commit SaltStack and Chef InSpec**

```bash
git add backend/app/connectors/catalog/saltstack.json backend/app/connectors/catalog/chef_inspec.json
git add backend/app/connectors/executors/saltstack/ backend/app/connectors/executors/chef_inspec/
git commit -m "feat: add SaltStack and Chef InSpec connectors"
```

---

### Task 7: Frontend Updates

- [ ] **Step 1: Update ConnectorType in api.ts**

Add: `| 'terraform' | 'ansible' | 'cloudformation' | 'pulumi' | 'helm' | 'bicep' | 'checkov' | 'saltstack' | 'chef_inspec'`

- [ ] **Step 2: Update CONNECTOR_LABELS in Connectors.tsx**

```typescript
terraform: 'Terraform (HCP)',
ansible: 'Ansible (AWX)',
cloudformation: 'AWS CloudFormation',
pulumi: 'Pulumi',
helm: 'Helm',
bicep: 'Azure Bicep',
checkov: 'Checkov',
saltstack: 'SaltStack',
chef_inspec: 'Chef InSpec',
```

- [ ] **Step 3: Update icons and AddConnectorModal**

Use `Code` or `Terminal` for IaC tools, `Server` for config management tools.

- [ ] **Step 4: Verify build**

```bash
docker compose exec frontend npm run build 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/
git commit -m "feat: add 6d IaC connectors to frontend type lists"
```

---

### Task 8: Integration Tests

- [ ] **Step 1: Write tests**

```python
# backend/app/tests/test_connector_6d.py
import pytest


@pytest.mark.asyncio
async def test_terraform_discover_workspaces_mock():
    from app.connectors.executors.terraform.discover_workspaces import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "discover_workspaces"
    assert isinstance(result["workspaces"], list)


@pytest.mark.asyncio
async def test_terraform_lock_rollback():
    from app.connectors.executors.terraform.lock_workspace import rollback
    result = await rollback({"workspace_id": "ws-test"}, {}, type("C", (), {"credentials": {}})())
    assert result["action"] == "unlock_workspace"


@pytest.mark.asyncio
async def test_checkov_scan_iac_mock():
    from app.connectors.executors.checkov.scan_iac import execute
    result = await execute({}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "scan_iac"
    assert "findings" in result


@pytest.mark.asyncio
async def test_saltstack_run_function_allowlist():
    from app.connectors.executors.saltstack.run_function import execute
    result = await execute({"function": "rm -rf /", "target": "*"}, [], type("C", (), {"credentials": {}})())
    assert "error" in result
    assert "allowlist" in result["error"]


@pytest.mark.asyncio
async def test_saltstack_run_function_allowed():
    from app.connectors.executors.saltstack.run_function import execute
    result = await execute({"function": "test.ping", "target": "*"}, [], type("C", (), {"credentials": {}})())
    assert result["action"] == "run_function"
    assert result["function"] == "test.ping"
```

- [ ] **Step 2: Run tests**

```bash
docker compose exec backend pytest app/tests/test_connector_6d.py -v
```
Expected: All 5 tests pass. Note: the allowlist test should pass with error message; the allowed test should return mock result.

- [ ] **Step 3: Commit**

```bash
git add backend/app/tests/test_connector_6d.py
git commit -m "test: add integration tests for 6d IaC connectors including SaltStack allowlist enforcement"
```
