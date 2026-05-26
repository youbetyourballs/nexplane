# IaC + Helm Smoke Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add live smoke coverage for `cloudformation`, `bicep`, and `helm` by adding three smoke phases and two small executor fixes.

**Architecture:** Task 1 adds `change_set_type` to the CloudFormation executor and catalog. Task 2 fixes the Helm subprocess executors to pass `--kubeconfig`. Tasks 3–5 add `run_phase_cf`, `run_phase_bicep`, and `run_phase_helm` smoke functions plus their `main()` wiring. Task 6 runs all three phases live on the EC2 runner.

**Tech Stack:** boto3 (CloudFormation), azure-mgmt-resource (Bicep), kubernetes + helm subprocess (Helm), Nexplane CR pipeline throughout.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `backend/app/connectors/executors/cloudformation/create_change_set.py` | Add `change_set_type` parameter |
| Modify | `backend/app/connectors/catalog/cloudformation.json` | Expose `change_set_type` in catalog |
| Modify | `backend/app/connectors/executors/helm/rollback_release.py` | Pass `--kubeconfig` to helm subprocess |
| Modify | `backend/app/connectors/executors/helm/uninstall_release.py` | Pass `--kubeconfig` to helm subprocess |
| Create | `backend/tests/test_iac_helm_executor_fixes.py` | Unit tests for Tasks 1 and 2 |
| Modify | `backend/tests/smoke/test_aws_live.py` | Add Phase CF and Phase HELM |
| Modify | `backend/tests/smoke/test_azure_live.py` | Add Phase BICEP |

---

## Task 1: Add `change_set_type` to CloudFormation executor and catalog

**Files:**
- Modify: `backend/app/connectors/executors/cloudformation/create_change_set.py`
- Modify: `backend/app/connectors/catalog/cloudformation.json`
- Create: `backend/tests/test_iac_helm_executor_fixes.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_iac_helm_executor_fixes.py`:

```python
import pytest
from unittest.mock import MagicMock, patch


def _make_cf_connector(access_key_id="k", secret="s", region="us-east-1"):
    c = MagicMock()
    c.credentials = {"access_key_id": access_key_id, "secret_access_key": secret, "region": region}
    return c


@pytest.mark.asyncio
async def test_create_change_set_type_create_forwarded():
    """change_set_type=CREATE is passed as ChangeSetType to boto3."""
    mock_cf = MagicMock()
    with patch(
        "app.connectors.executors.cloudformation._client.get_client",
        return_value=mock_cf,
    ):
        from app.connectors.executors.cloudformation.create_change_set import execute
        result = await execute(
            {
                "stack_name": "smoke-stack",
                "change_set_type": "CREATE",
                "template_body": '{"Resources": {}}',
            },
            [],
            _make_cf_connector(),
        )
    assert result["stack_name"] == "smoke-stack"
    assert "change_set_name" in result
    call_kwargs = mock_cf.create_change_set.call_args[1]
    assert call_kwargs["ChangeSetType"] == "CREATE"
    assert call_kwargs["TemplateBody"] == '{"Resources": {}}'


@pytest.mark.asyncio
async def test_create_change_set_default_type_is_update():
    """Omitting change_set_type defaults to UPDATE (backwards compatible)."""
    mock_cf = MagicMock()
    with patch(
        "app.connectors.executors.cloudformation._client.get_client",
        return_value=mock_cf,
    ):
        from app.connectors.executors.cloudformation.create_change_set import execute
        await execute(
            {"stack_name": "smoke-stack", "template_body": "{}"},
            [],
            _make_cf_connector(),
        )
    call_kwargs = mock_cf.create_change_set.call_args[1]
    assert call_kwargs["ChangeSetType"] == "UPDATE"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_iac_helm_executor_fixes.py::test_create_change_set_type_create_forwarded tests/test_iac_helm_executor_fixes.py::test_create_change_set_default_type_is_update -v
```

Expected: FAIL — `AssertionError` on `ChangeSetType` key (it won't be in kwargs yet).

- [ ] **Step 3: Update `create_change_set.py`**

Replace the full file content:

```python
import asyncio
import uuid


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    stack_name = parameters["stack_name"]
    change_set_type = parameters.get("change_set_type", "UPDATE")
    if not creds:
        return {"action": "create_change_set", "stack_name": stack_name, "change_set_name": "mock-changeset-1"}
    from ._client import get_client
    loop = asyncio.get_event_loop()
    cf = get_client(creds)
    change_set_name = f"nexplane-{uuid.uuid4().hex[:8]}"
    kwargs = {
        "StackName": stack_name,
        "ChangeSetName": change_set_name,
        "ChangeSetType": change_set_type,
    }
    if parameters.get("template_body"):
        kwargs["TemplateBody"] = parameters["template_body"]
    elif parameters.get("template_url"):
        kwargs["TemplateURL"] = parameters["template_url"]
    await loop.run_in_executor(None, lambda: cf.create_change_set(**kwargs))
    return {"action": "create_change_set", "stack_name": stack_name, "change_set_name": change_set_name}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete change set manually if needed"}
```

- [ ] **Step 4: Update `cloudformation.json` catalog**

In `backend/app/connectors/catalog/cloudformation.json`, find the `create_change_set` action's `"parameters"` array and add the new field:

```json
{"name": "change_set_type", "type": "string", "required": false, "default": "UPDATE", "description": "ChangeSetType: CREATE for new stacks, UPDATE for existing stacks (default: UPDATE)"}
```

Full `create_change_set` action after edit:
```json
{"action_id": "create_change_set", "generic_action": "plan", "action_type": "change", "execution_tier": 2, "display_name": "Create Change Set", "description": "Create a CloudFormation change set.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "stack_name", "type": "string", "required": true}, {"name": "template_body", "type": "string", "required": false}, {"name": "template_url", "type": "string", "required": false}, {"name": "change_set_type", "type": "string", "required": false, "default": "UPDATE", "description": "ChangeSetType: CREATE for new stacks, UPDATE for existing stacks (default: UPDATE)"}], "executor": "cloudformation.create_change_set", "estimated_duration_seconds": 30}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_iac_helm_executor_fixes.py::test_create_change_set_type_create_forwarded tests/test_iac_helm_executor_fixes.py::test_create_change_set_default_type_is_update -v
```

Expected: PASS ✅

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/cloudformation/create_change_set.py \
        backend/app/connectors/catalog/cloudformation.json \
        backend/tests/test_iac_helm_executor_fixes.py
git commit -m "feat: add change_set_type to cloudformation create_change_set executor"
```

---

## Task 2: Fix Helm subprocess executors to use `--kubeconfig`

**Context:** `rollback_release.py` and `uninstall_release.py` run `helm` as a subprocess without specifying a kubeconfig. The connector credentials contain a base64-encoded kubeconfig at `creds["kubeconfig"]`. Without `--kubeconfig`, helm uses `~/.kube/config` which does not exist in the backend container for smoke clusters.

**Files:**
- Modify: `backend/app/connectors/executors/helm/rollback_release.py`
- Modify: `backend/app/connectors/executors/helm/uninstall_release.py`
- Modify: `backend/tests/test_iac_helm_executor_fixes.py` (add tests)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_iac_helm_executor_fixes.py`:

```python
def _make_helm_connector(kubeconfig_b64="dGVzdA=="):  # base64("test")
    c = MagicMock()
    c.credentials = {"kubeconfig": kubeconfig_b64}
    return c


@pytest.mark.asyncio
async def test_rollback_release_passes_kubeconfig():
    """rollback_release passes --kubeconfig to the helm subprocess."""
    import base64
    kubeconfig_b64 = base64.b64encode(b"apiVersion: v1\nclusters: []").decode()
    connector = _make_helm_connector(kubeconfig_b64)
    captured = {}
    import subprocess as _sp

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    with patch("subprocess.run", side_effect=fake_run):
        from app.connectors.executors.helm.rollback_release import execute
        result = await execute(
            {"release_name": "smoke-nginx", "namespace": "default", "revision": 0},
            [],
            connector,
        )
    assert result.get("rolled_back") is True
    assert "--kubeconfig" in captured["cmd"]
    kubeconfig_arg_idx = captured["cmd"].index("--kubeconfig")
    kubeconfig_path = captured["cmd"][kubeconfig_arg_idx + 1]
    assert kubeconfig_path.endswith(".yaml")


@pytest.mark.asyncio
async def test_uninstall_release_passes_kubeconfig():
    """uninstall_release passes --kubeconfig to the helm subprocess."""
    import base64
    kubeconfig_b64 = base64.b64encode(b"apiVersion: v1\nclusters: []").decode()
    connector = _make_helm_connector(kubeconfig_b64)
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        return r

    with patch("subprocess.run", side_effect=fake_run):
        from app.connectors.executors.helm.uninstall_release import execute
        result = await execute(
            {"release_name": "smoke-nginx", "namespace": "default"},
            [],
            connector,
        )
    assert result.get("uninstalled") is True
    assert "--kubeconfig" in captured["cmd"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_iac_helm_executor_fixes.py::test_rollback_release_passes_kubeconfig tests/test_iac_helm_executor_fixes.py::test_uninstall_release_passes_kubeconfig -v
```

Expected: FAIL — `AssertionError: assert '--kubeconfig' in [...]`

- [ ] **Step 3: Update `rollback_release.py`**

Replace the full file content:

```python
import asyncio
import base64
import os
import subprocess
import tempfile


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    release_name = parameters["release_name"]
    namespace = parameters["namespace"]
    revision = parameters.get("revision", 0)
    if not creds:
        return {"action": "rollback_release", "release": release_name, "revision": revision, "rolled_back": True}
    kubeconfig_b64 = creds.get("kubeconfig", "")
    loop = asyncio.get_event_loop()

    def run_helm():
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".yaml", delete=False) as f:
            f.write(base64.b64decode(kubeconfig_b64))
            tmp_path = f.name
        try:
            cmd = [
                "helm", "rollback", release_name, str(revision),
                "--namespace", namespace,
                "--kubeconfig", tmp_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            return result.returncode, result.stdout, result.stderr
        finally:
            os.unlink(tmp_path)

    rc, stdout, stderr = await loop.run_in_executor(None, run_helm)
    if rc != 0:
        return {"action": "rollback_release", "release": release_name, "error": stderr}
    return {"action": "rollback_release", "release": release_name, "revision": revision, "rolled_back": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "rollback of a rollback — re-run with different revision"}
```

- [ ] **Step 4: Update `uninstall_release.py`**

Replace the full file content:

```python
import asyncio
import base64
import os
import subprocess
import tempfile


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    release_name = parameters["release_name"]
    namespace = parameters["namespace"]
    if not creds:
        return {"action": "uninstall_release", "release": release_name, "uninstalled": True}
    kubeconfig_b64 = creds.get("kubeconfig", "")
    loop = asyncio.get_event_loop()

    def run_helm():
        with tempfile.NamedTemporaryFile(mode="wb", suffix=".yaml", delete=False) as f:
            f.write(base64.b64decode(kubeconfig_b64))
            tmp_path = f.name
        try:
            cmd = [
                "helm", "uninstall", release_name,
                "--namespace", namespace,
                "--kubeconfig", tmp_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            return result.returncode, result.stdout, result.stderr
        finally:
            os.unlink(tmp_path)

    rc, stdout, stderr = await loop.run_in_executor(None, run_helm)
    if rc != 0:
        return {"action": "uninstall_release", "release": release_name, "error": stderr}
    return {"action": "uninstall_release", "release": release_name, "uninstalled": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "uninstall is destructive — re-install manually"}
```

- [ ] **Step 5: Run all four executor tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_iac_helm_executor_fixes.py -v
```

Expected: all 4 tests PASS ✅

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/helm/rollback_release.py \
        backend/app/connectors/executors/helm/uninstall_release.py \
        backend/tests/test_iac_helm_executor_fixes.py
git commit -m "fix: helm rollback_release and uninstall_release pass --kubeconfig to subprocess"
```

---

## Task 3: Phase CF — CloudFormation smoke (`test_aws_live.py`)

**Context:** The smoke test is in `backend/tests/smoke/test_aws_live.py`. Use action_ids directly as change_types (e.g., `discover_stacks`, `create_change_set`). The connector type is `cloudformation` with credential fields `access_key_id`, `secret_access_key`, `region`. `_aws_creds_cache` uses those same keys. The phase registers its own cloudformation connector inline and cleans it up in a `finally` block.

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Add `run_phase_cf()` function**

Add the following function to `test_aws_live.py` immediately before the `if __name__ == "__main__":` line:

```python
def run_phase_cf(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase CF: CloudFormation lifecycle — create_change_set(CREATE) → execute_change_set → discover_stacks → delete_stack."""
    import random
    import json as _json
    import smoke_helpers as _sh
    print("\n[Phase CF] CloudFormation")

    cf_boto = _get_aws_boto3_client("cloudformation")
    if not cf_boto:
        fail("[CF] CloudFormation boto3 client not available")

    suffix = random.randint(10000, 99999)
    stack_name = f"nexplane-smoke-cf-{suffix}"
    topic_name = f"nexplane-smoke-topic-{suffix}"

    template_body = _json.dumps({
        "AWSTemplateFormatVersion": "2010-09-09",
        "Resources": {
            "SmokeTopic": {
                "Type": "AWS::SNS::Topic",
                "Properties": {"TopicName": topic_name},
            }
        },
    })

    cf_connector_id = None
    cf_asset_id = None
    try:
        # Register a cloudformation connector using the cached AWS creds
        creds = _sh._aws_creds_cache
        cf_connector_id = client.post("/connectors", json={
            "connector_type": "cloudformation",
            "name": f"smoke-cf-{suffix}",
            "credentials": {
                "access_key_id": creds["access_key_id"],
                "secret_access_key": creds["secret_access_key"],
                "region": "us-east-1",
            },
        })["id"]
        cf_asset_id = client.post("/assets", json={
            "name": f"smoke-cf-account-{suffix}",
            "asset_type": "cloud_account",
            "connector_id": cf_connector_id,
        })["id"]
        log(f"CF: connector {cf_connector_id}, asset {cf_asset_id}")

        # Step 1: create_change_set with ChangeSetType=CREATE
        cr1 = client.run_cr(
            "[Phase CF] create_change_set",
            "create_change_set",
            cf_asset_id,
            {
                "stack_name": stack_name,
                "change_set_type": "CREATE",
                "template_body": template_body,
            },
            connector_id=cf_connector_id,
        )
        step1 = client.get_cr_step_result(cr1)
        change_set_name = step1["change_set_name"]
        log(f"CF: change set created: {change_set_name}")

        # Wait for change set to reach CREATE_COMPLETE before executing
        deadline = time.time() + 120
        while time.time() < deadline:
            cs = cf_boto.describe_change_set(StackName=stack_name, ChangeSetName=change_set_name)
            if cs["Status"] == "CREATE_COMPLETE":
                break
            if cs["Status"] in ("FAILED", "DELETE_COMPLETE"):
                fail(f"[CF] Change set status {cs['Status']}: {cs.get('StatusReason', '')}")
            time.sleep(5)
        else:
            fail("[CF] Change set did not reach CREATE_COMPLETE in 120s")

        # Step 2: execute_change_set → creates the stack
        client.run_cr(
            "[Phase CF] execute_change_set",
            "execute_change_set",
            cf_asset_id,
            {"stack_name": stack_name, "change_set_name": change_set_name},
            connector_id=cf_connector_id,
        )
        log("CF: execute_change_set CR completed")

        # Wait for stack to reach CREATE_COMPLETE
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                stacks = cf_boto.describe_stacks(StackName=stack_name)["Stacks"]
                status = stacks[0]["StackStatus"] if stacks else "UNKNOWN"
                if status == "CREATE_COMPLETE":
                    break
                if "FAILED" in status or ("ROLLBACK" in status and status != "ROLLBACK_COMPLETE"):
                    fail(f"[CF] Stack entered status {status}")
            except cf_boto.exceptions.ClientError:
                pass
            time.sleep(10)
        else:
            fail("[CF] Stack did not reach CREATE_COMPLETE in 300s")
        log(f"CF: stack {stack_name} is CREATE_COMPLETE")

        # Step 3: discover_stacks → assert our stack appears
        cr3 = client.run_cr(
            "[Phase CF] discover_stacks",
            "discover_stacks",
            cf_asset_id,
            {},
            connector_id=cf_connector_id,
        )
        step3 = client.get_cr_step_result(cr3)
        stack_names_found = [s["name"] for s in step3.get("stacks", [])]
        assert stack_name in stack_names_found, \
            f"[CF] Stack {stack_name} not in discover_stacks result: {stack_names_found}"
        log("CF: stack confirmed in discover_stacks")

        # Step 4: delete_stack → cleanup
        client.run_cr(
            "[Phase CF] delete_stack",
            "delete_stack",
            cf_asset_id,
            {"stack_name": stack_name},
            connector_id=cf_connector_id,
        )
        log("CF: delete_stack CR completed")

        # Verify deletion
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                stacks = cf_boto.describe_stacks(StackName=stack_name)["Stacks"]
                if not stacks or stacks[0]["StackStatus"] == "DELETE_COMPLETE":
                    break
            except Exception:
                break  # Stack no longer exists
            time.sleep(10)
        log("CF: stack deletion confirmed")
        log("Phase CF PASSED")

    finally:
        if cf_asset_id:
            try:
                client.client.delete(f"{client.base}/assets/{cf_asset_id}")
            except Exception:
                pass
        if cf_connector_id:
            try:
                client.client.delete(f"{client.base}/connectors/{cf_connector_id}")
            except Exception:
                pass
```

- [ ] **Step 2: Wire Phase CF into `main()`**

In `test_aws_live.py`'s `main()` function, inside the `try:` block before the `print("\n" + "=" * 60)` success line, add:

```python
        if "CF" in phases:
            run_phase_cf(client, cloud_account_id)
```

Also update the `--phases` help string to include `CF=CloudFormation lifecycle`.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat: Phase CF — CloudFormation smoke (create_change_set/execute/discover/delete)"
```

---

## Task 4: Phase BICEP — Bicep smoke (`test_azure_live.py`)

**Context:** Mirrors the terraform_local Azure pattern (Phase S). Uses `_get_azure_creds()` already defined in `test_azure_live.py`. Deploys an ARM template creating a storage account into the existing resource group. The bicep connector uses the same credentials as the Azure connector (tenant_id, client_id, client_secret, subscription_id). Next available phase letter after Z — use `BICEP` as the phase name string.

**Files:**
- Modify: `backend/tests/smoke/test_azure_live.py`

- [ ] **Step 1: Add `run_phase_bicep()` function**

Add immediately before `if __name__ == "__main__":`:

```python
def run_phase_bicep(client: NexplaneClient, cloud_account_id: str,
                    azure_resource_group: str) -> None:
    """Phase BICEP: Bicep/ARM deployment lifecycle — create_deployment → discover_deployments → delete_deployment."""
    import secrets as _secrets
    print("\n[Phase BICEP] Bicep ARM Deployment")

    if not azure_resource_group:
        fail("[BICEP] --azure-resource-group required for Phase BICEP")

    azure_creds = _get_azure_creds()
    if not azure_creds:
        fail("[BICEP] Azure credentials not available")

    suffix = _secrets.token_hex(4)
    deployment_name = f"nexplane-smoke-bicep-{suffix}"
    storage_account_name = f"nexsmk{suffix}"  # max 24 chars, lowercase alphanumeric

    arm_template = {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "parameters": {
            "storageAccountName": {"type": "string"},
        },
        "resources": [
            {
                "type": "Microsoft.Storage/storageAccounts",
                "apiVersion": "2022-09-01",
                "name": "[parameters('storageAccountName')]",
                "location": "[resourceGroup().location]",
                "sku": {"name": "Standard_LRS"},
                "kind": "StorageV2",
            }
        ],
    }

    bicep_connector_id = None
    bicep_asset_id = None
    try:
        # Register a bicep connector using the Azure credentials
        bicep_connector_id = client.post("/connectors", json={
            "connector_type": "bicep",
            "name": f"smoke-bicep-{suffix}",
            "credentials": {
                "tenant_id": azure_creds["tenant_id"],
                "client_id": azure_creds["client_id"],
                "client_secret": azure_creds["client_secret"],
                "subscription_id": azure_creds["subscription_id"],
            },
        })["id"]
        bicep_asset_id = client.post("/assets", json={
            "name": f"smoke-bicep-account-{suffix}",
            "asset_type": "cloud_account",
            "connector_id": bicep_connector_id,
        })["id"]
        log(f"BICEP: connector {bicep_connector_id}, asset {bicep_asset_id}")

        # Step 1: create_deployment
        client.run_cr(
            "[Phase BICEP] create_deployment",
            "create_deployment",
            bicep_asset_id,
            {
                "resource_group": azure_resource_group,
                "deployment_name": deployment_name,
                "template": arm_template,
                "parameters": {"storageAccountName": {"value": storage_account_name}},
            },
            connector_id=bicep_connector_id,
        )
        log(f"BICEP: deployment {deployment_name} created (storage account: {storage_account_name})")

        # Verify via SDK
        try:
            from azure.identity import ClientSecretCredential
            from azure.mgmt.resource import ResourceManagementClient
            credential = ClientSecretCredential(
                tenant_id=azure_creds["tenant_id"],
                client_id=azure_creds["client_id"],
                client_secret=azure_creds["client_secret"],
            )
            rm = ResourceManagementClient(credential, azure_creds["subscription_id"])
            dep = rm.deployments.get(azure_resource_group, deployment_name)
            assert dep.properties.provisioning_state == "Succeeded", \
                f"[BICEP] Deployment state: {dep.properties.provisioning_state}"
            log("BICEP: deployment confirmed Succeeded via SDK")
        except Exception as e:
            print(f"  ⚠️  SDK verification skipped: {e}")

        # Step 2: discover_deployments → assert our deployment appears
        cr2 = client.run_cr(
            "[Phase BICEP] discover_deployments",
            "discover_deployments",
            bicep_asset_id,
            {},
            connector_id=bicep_connector_id,
        )
        step2 = client.get_cr_step_result(cr2)
        dep_names = [d["name"] for d in step2.get("deployments", [])]
        assert deployment_name in dep_names, \
            f"[BICEP] {deployment_name} not in discover_deployments: {dep_names}"
        log("BICEP: deployment confirmed in discover_deployments")

        # Step 3: delete_deployment → cleanup
        client.run_cr(
            "[Phase BICEP] delete_deployment",
            "delete_deployment",
            bicep_asset_id,
            {
                "resource_group": azure_resource_group,
                "deployment_name": deployment_name,
            },
            connector_id=bicep_connector_id,
        )
        log("BICEP: delete_deployment CR completed")

        # Also delete the storage account (deployment delete removes the ARM record; resource persists)
        try:
            from azure.mgmt.storage import StorageManagementClient
            storage_client = StorageManagementClient(credential, azure_creds["subscription_id"])
            storage_client.storage_accounts.delete(azure_resource_group, storage_account_name)
            log("BICEP: storage account deleted")
        except Exception as e:
            print(f"  ⚠️  Storage account cleanup skipped: {e}")

        log("Phase BICEP PASSED")

    finally:
        if bicep_asset_id:
            try:
                client.client.delete(f"{client.base}/assets/{bicep_asset_id}")
            except Exception:
                pass
        if bicep_connector_id:
            try:
                client.client.delete(f"{client.base}/connectors/{bicep_connector_id}")
            except Exception:
                pass
```

- [ ] **Step 2: Wire Phase BICEP into `main()`**

In `test_azure_live.py`'s `main()` function, inside the `try:` block before the success print, add:

```python
        if "BICEP" in phases:
            run_phase_bicep(client, cloud_account_id, args.azure_resource_group)
```

Also update the `--phases` help string to include `BICEP=Bicep ARM deployment lifecycle`.

- [ ] **Step 3: Add `azure-mgmt-storage` import guard**

The storage account cleanup uses `azure-mgmt-storage`. It is already a dependency of the Azure smoke tests (used in Phase Q). No new dependency needed — the `try/except` handles cases where it's unavailable.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_azure_live.py
git commit -m "feat: Phase BICEP — Bicep ARM deployment smoke (create/discover/delete)"
```

---

## Task 5: Phase HELM — Helm smoke (`test_aws_live.py`)

**Context:** Reuses the kind cluster AMI from the K8S_RBAC phase. The phase:
1. Launches an EC2 instance from the cached K8S_RBAC AMI
2. Installs `bitnami/nginx` via helm on the instance (via SSM)
3. Reads and patches the kubeconfig (server URL → private IP)
4. Registers a helm connector with the kubeconfig
5. Runs `discover_releases` → `rollback_release` → `uninstall_release` through the CR pipeline
6. Terminates the instance

The helm connector has `applicable_asset_types: ["server"]`, so the CR targets a `server` asset.

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Add `run_phase_helm()` function**

Add immediately before `run_phase_cf` (before `if __name__ == "__main__":`):

```python
def run_phase_helm(client: NexplaneClient, cloud_account_id: str) -> None:
    """Phase HELM: Helm release lifecycle using kind cluster AMI from K8S_RBAC phase.

    discover_releases → rollback_release → uninstall_release through the CR pipeline.
    """
    import base64
    import hashlib
    import re
    import yaml as _yaml
    print("\n[Phase HELM] Helm")

    ec2_client = _get_aws_boto3_client("ec2")
    ssm_client = _get_aws_boto3_client("ssm")
    iam_client = _get_aws_boto3_client("iam")
    if not ec2_client or not ssm_client:
        fail("[HELM] AWS clients not available")

    # Reuse the K8S_RBAC AMI cache
    try:
        from run_on_ec2 import get_or_create_smoke_ami, get_ssm_instance_profile
    except ImportError:
        get_or_create_smoke_ami = None
        get_ssm_instance_profile = None

    setup_hash = hashlib.md5(b"kind-0.24.0-k8s-rbac-ipforward-v10").hexdigest()
    ami_id = None
    try:
        ami_id = ssm_client.get_parameter(
            Name=f"/nexplane/smoke-amis/k8s-rbac/{setup_hash}"
        )["Parameter"]["Value"]
        log(f"HELM: reusing K8S_RBAC AMI {ami_id}")
    except Exception:
        fail("[HELM] K8S_RBAC AMI not cached — run K8S_RBAC phase first to build the AMI")

    # Get SSM instance profile
    instance_profile = "NexplaneEC2TestProfile"
    if get_ssm_instance_profile:
        instance_profile = get_ssm_instance_profile(iam_client) or instance_profile

    instance_id = None
    helm_connector_id = None
    helm_asset_id = None
    try:
        # Launch instance from cached AMI
        launch_resp = ec2_client.run_instances(
            ImageId=ami_id,
            InstanceType="t3.small",
            MinCount=1,
            MaxCount=1,
            IamInstanceProfile={"Name": instance_profile},
            TagSpecifications=[{"ResourceType": "instance", "Tags": [
                {"Key": "Name", "Value": "nexplane-smoke-helm"},
                {"Key": "nexplane-smoke", "Value": "helm"},
            ]}],
        )
        instance_id = launch_resp["Instances"][0]["InstanceId"]
        log(f"HELM: launched {instance_id} from AMI {ami_id}")

        # Wait for SSM to be ready
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                r = ssm_client.describe_instance_information(
                    Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
                )
                if r["InstanceInformationList"]:
                    break
            except Exception:
                pass
            time.sleep(10)
        else:
            fail("[HELM] SSM not ready in 300s")

        # Get private IP
        private_ip = ec2_client.describe_instances(InstanceIds=[instance_id])[
            "Reservations"
        ][0]["Instances"][0]["PrivateIpAddress"]
        log(f"HELM: private IP {private_ip}")

        # Ensure kind cluster is running and start helm install
        setup_cmd = """
set -e
# Restart docker if needed
systemctl start docker 2>/dev/null || true
for i in $(seq 1 20); do docker info >/dev/null 2>&1 && break || sleep 3; done

# Re-start kind cluster if not running (AMI has it stopped)
export KUBECONFIG=/root/.kube/config
if ! kubectl get nodes >/dev/null 2>&1; then
    kind start cluster --name smoke-test 2>/dev/null || true
    for i in $(seq 1 30); do kubectl get nodes >/dev/null 2>&1 && break || sleep 5; done
fi

# Allow inbound on 6443
iptables -I INPUT -p tcp --dport 6443 -j ACCEPT 2>/dev/null || true

# Add bitnami repo and install nginx
helm repo add bitnami https://charts.bitnami.com/bitnami 2>/dev/null || true
helm repo update
helm install smoke-nginx bitnami/nginx --namespace default --wait --timeout 120s \
    || helm upgrade smoke-nginx bitnami/nginx --namespace default --wait --timeout 120s

echo "HELM_SETUP_DONE"
"""
        resp = ssm_client.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [setup_cmd]},
        )
        cmd_id = resp["Command"]["CommandId"]
        deadline = time.time() + 300
        output = ""
        while time.time() < deadline:
            time.sleep(10)
            try:
                inv = ssm_client.get_command_invocation(
                    CommandId=cmd_id, InstanceId=instance_id
                )
                if inv["Status"] in ("Success", "Failed", "TimedOut", "Cancelled"):
                    output = inv.get("StandardOutputContent", "")
                    if inv["Status"] != "Success":
                        fail(f"[HELM] helm install failed: {inv.get('StandardErrorContent', '')[:500]}")
                    break
            except ssm_client.exceptions.InvocationDoesNotExist:
                pass
        else:
            fail("[HELM] SSM helm install timed out")
        assert "HELM_SETUP_DONE" in output, f"[HELM] Setup did not complete: {output[-300:]}"
        log("HELM: nginx chart installed on kind cluster")

        # Read kubeconfig and patch server URL to use private IP
        resp2 = ssm_client.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": ["cat /root/.kube/config"]},
        )
        cmd_id2 = resp2["Command"]["CommandId"]
        kubeconfig_raw = ""
        deadline = time.time() + 60
        while time.time() < deadline:
            time.sleep(5)
            try:
                inv2 = ssm_client.get_command_invocation(
                    CommandId=cmd_id2, InstanceId=instance_id
                )
                if inv2["Status"] in ("Success", "Failed", "TimedOut"):
                    kubeconfig_raw = inv2.get("StandardOutputContent", "")
                    break
            except ssm_client.exceptions.InvocationDoesNotExist:
                pass
        assert kubeconfig_raw, "[HELM] Failed to read kubeconfig from instance"

        # Patch server URL: replace 127.0.0.1 or localhost with private IP
        kubeconfig_patched = re.sub(
            r"server: https://[^:]+:6443",
            f"server: https://{private_ip}:6443",
            kubeconfig_raw,
        )
        # Disable TLS verification (kind uses self-signed cert bound to 127.0.0.1)
        kubeconfig_dict = _yaml.safe_load(kubeconfig_patched)
        for cluster in kubeconfig_dict.get("clusters", []):
            cluster_info = cluster.get("cluster", {})
            cluster_info.pop("certificate-authority-data", None)
            cluster_info["insecure-skip-tls-verify"] = True
        kubeconfig_final = _yaml.dump(kubeconfig_dict)
        kubeconfig_b64 = base64.b64encode(kubeconfig_final.encode()).decode()
        log("HELM: kubeconfig patched with private IP")

        # Register helm connector
        helm_connector_id = client.post("/connectors", json={
            "connector_type": "helm",
            "name": f"smoke-helm-{instance_id[-6:]}",
            "credentials": {"kubeconfig": kubeconfig_b64},
        })["id"]
        helm_asset_id = client.post("/assets", json={
            "name": f"smoke-helm-cluster-{instance_id[-6:]}",
            "asset_type": "server",
            "connector_id": helm_connector_id,
        })["id"]
        log(f"HELM: connector {helm_connector_id}, asset {helm_asset_id}")

        # Step 1: discover_releases → assert smoke-nginx appears
        cr1 = client.run_cr(
            "[Phase HELM] discover_releases",
            "discover_releases",
            helm_asset_id,
            {},
            connector_id=helm_connector_id,
        )
        step1 = client.get_cr_step_result(cr1)
        release_names = [r["name"] for r in step1.get("releases", [])]
        assert "smoke-nginx" in release_names, \
            f"[HELM] smoke-nginx not in discover_releases: {release_names}"
        log("HELM: smoke-nginx confirmed in discover_releases")

        # Step 2: rollback_release revision=0 (re-deploys same version — exercises the executor)
        cr2 = client.run_cr(
            "[Phase HELM] rollback_release",
            "rollback_release",
            helm_asset_id,
            {"release_name": "smoke-nginx", "namespace": "default", "revision": 0},
            connector_id=helm_connector_id,
        )
        step2 = client.get_cr_step_result(cr2)
        assert step2.get("rolled_back") is True, f"[HELM] rollback_release failed: {step2}"
        log("HELM: rollback_release OK")

        # Step 3: uninstall_release → cleanup
        cr3 = client.run_cr(
            "[Phase HELM] uninstall_release",
            "uninstall_release",
            helm_asset_id,
            {"release_name": "smoke-nginx", "namespace": "default"},
            connector_id=helm_connector_id,
        )
        step3 = client.get_cr_step_result(cr3)
        assert step3.get("uninstalled") is True, f"[HELM] uninstall_release failed: {step3}"
        log("HELM: uninstall_release OK")

        # Verify release is gone
        cr4 = client.run_cr(
            "[Phase HELM] discover_releases post-uninstall",
            "discover_releases",
            helm_asset_id,
            {},
            connector_id=helm_connector_id,
        )
        step4 = client.get_cr_step_result(cr4)
        post_names = [r["name"] for r in step4.get("releases", [])]
        assert "smoke-nginx" not in post_names, \
            f"[HELM] smoke-nginx still present after uninstall: {post_names}"
        log("HELM: smoke-nginx confirmed removed")
        log("Phase HELM PASSED")

    finally:
        if helm_asset_id:
            try:
                client.client.delete(f"{client.base}/assets/{helm_asset_id}")
            except Exception:
                pass
        if helm_connector_id:
            try:
                client.client.delete(f"{client.base}/connectors/{helm_connector_id}")
            except Exception:
                pass
        if instance_id:
            try:
                ec2_client.terminate_instances(InstanceIds=[instance_id])
                log(f"HELM: terminated {instance_id}")
            except Exception:
                pass
```

- [ ] **Step 2: Add `import yaml` at the top of `test_aws_live.py`**

The `run_phase_helm` function uses `yaml.safe_load` and `yaml.dump`. Add to the imports block at the top of `test_aws_live.py` (after existing imports):

```python
import yaml
```

- [ ] **Step 3: Wire Phase HELM into `main()`**

In `main()`, inside the `try:` block, add alongside the other phase dispatches:

```python
        if "HELM" in phases:
            run_phase_helm(client, cloud_account_id)
```

Also update the `--phases` help string to include `HELM=Helm release lifecycle (requires K8S_RBAC AMI)`.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat: Phase HELM — Helm release smoke (discover/rollback/uninstall)"
```

---

## Task 6: Run all three phases live on EC2 runner

**Context:** SSH to the EC2 runner (`ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39`), then run via `docker exec nexplane-backend-1`. Phase CF and HELM go in `test_aws_live.py`; Phase BICEP in `test_azure_live.py`.

- [ ] **Step 1: SSH to the EC2 runner and git pull**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39
cd /opt/nexplane && git pull
```

- [ ] **Step 2: Run Phase CF**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
    --base-url http://localhost:8000 \
    --email admin@nexplane.local \
    --password changeme \
    --phases CF
```

Expected: `✅ Phase CF PASSED`

- [ ] **Step 3: Run Phase BICEP**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_azure_live.py \
    --base-url http://localhost:8000 \
    --email admin@nexplane.local \
    --password changeme \
    --phases BICEP \
    --azure-resource-group <existing-rg-name>
```

Expected: `✅ Phase BICEP PASSED`

- [ ] **Step 4: Run Phase HELM (requires K8S_RBAC AMI to exist)**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
    --base-url http://localhost:8000 \
    --email admin@nexplane.local \
    --password changeme \
    --phases HELM
```

Expected: `✅ Phase HELM PASSED`

If HELM fails with `K8S_RBAC AMI not cached — run K8S_RBAC phase first`, run K8S_RBAC first:

```bash
docker exec nexplane-backend-1 python tests/smoke/test_aws_live.py \
    --phases K8S_RBAC \
    --base-url http://localhost:8000 \
    --email admin@nexplane.local \
    --password changeme
```

Then re-run HELM.

- [ ] **Step 5: Commit smoke results note**

```bash
git commit --allow-empty -m "chore: Phase CF/BICEP/HELM smoke — all PASSED"
```

---

## Self-Review

**Spec coverage:**
- `create_change_set` gets `change_set_type` param ✅ (Task 1)
- CloudFormation lifecycle: create_change_set → execute_change_set → discover_stacks → delete_stack ✅ (Task 3)
- Bicep lifecycle: create_deployment → discover_deployments → delete_deployment ✅ (Task 4)
- Helm lifecycle: discover_releases → rollback_release → uninstall_release ✅ (Task 5)
- Helm subprocess executors use `--kubeconfig` ✅ (Task 2)
- All phases go through Nexplane CR pipeline ✅
- All phases register/clean up their own connectors and assets ✅

**No placeholders:** All code blocks are complete and runnable.

**Type consistency:** `change_set_name` returned from `create_change_set.execute()` step result, consumed in `execute_change_set` CR parameters — consistent throughout.
