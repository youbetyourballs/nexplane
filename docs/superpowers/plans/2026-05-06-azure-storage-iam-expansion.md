# Azure Storage + IAM Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Azure storage account/blob container CRUD (Phase V) and managed identity/RBAC role assignment (Phase W) as Nexplane change types, with live smoke test phases replacing the existing stubs.

**Architecture:** Two independent sub-projects sharing the same executor/CT definition/migration pattern. Sub-project V adds 4 storage executors + migration 032 + Phase V smoke test. Sub-project W adds 4 IAM executors + `_client.py` additions + migration 033 + Phase W smoke test. Stub functions `run_phase_v_stub` and `run_phase_w_stub` in `test_azure_live.py` are replaced with real implementations.

**Tech Stack:** Python 3.12, azure-mgmt-storage, azure-mgmt-msi, azure-mgmt-authorization, FastAPI, Alembic, pytest

---

## Files

**Sub-project V — Create:**
- `backend/app/connectors/executors/azure/create_storage_account.py`
- `backend/app/connectors/executors/azure/delete_storage_account.py`
- `backend/app/connectors/executors/azure/create_blob_container.py`
- `backend/app/connectors/executors/azure/delete_blob_container.py`
- `backend/app/connectors/change_type_definitions/azure_storage_account_create.json`
- `backend/app/connectors/change_type_definitions/azure_storage_account_delete.json`
- `backend/app/connectors/change_type_definitions/azure_blob_container_create.json`
- `backend/app/connectors/change_type_definitions/azure_blob_container_delete.json`
- `backend/alembic/versions/032_add_azure_storage_crud_types.py`

**Sub-project V — Modify:**
- `backend/app/models/change_request.py` — 4 new enum values
- `backend/tests/smoke/smoke_helpers.py` — add `_get_azure_storage_client()`
- `backend/tests/smoke/test_azure_live.py` — replace `run_phase_v_stub`

**Sub-project W — Create:**
- `backend/app/connectors/executors/azure/create_managed_identity.py`
- `backend/app/connectors/executors/azure/delete_managed_identity.py`
- `backend/app/connectors/executors/azure/assign_role.py`
- `backend/app/connectors/executors/azure/remove_role_assignment.py`
- `backend/app/connectors/change_type_definitions/azure_managed_identity_create.json`
- `backend/app/connectors/change_type_definitions/azure_managed_identity_delete.json`
- `backend/app/connectors/change_type_definitions/azure_role_assignment_create.json`
- `backend/app/connectors/change_type_definitions/azure_role_assignment_delete.json`
- `backend/alembic/versions/033_add_azure_iam_types.py`

**Sub-project W — Modify:**
- `backend/requirements.txt` — add azure-mgmt-msi, azure-mgmt-authorization
- `backend/app/connectors/executors/azure/_client.py` — add `get_msi_client`, `get_authorization_client`
- `backend/app/models/change_request.py` — 4 more enum values
- `backend/tests/smoke/smoke_helpers.py` — add `_get_azure_msi_client()`, `_get_azure_authorization_client()`
- `backend/tests/smoke/test_azure_live.py` — replace `run_phase_w_stub`, update imports, update help text

---

## Sub-project V

---

### Task 1: Storage CRUD executors

**Files:**
- Create: `backend/app/connectors/executors/azure/create_storage_account.py`
- Create: `backend/app/connectors/executors/azure/delete_storage_account.py`
- Create: `backend/app/connectors/executors/azure/create_blob_container.py`
- Create: `backend/app/connectors/executors/azure/delete_blob_container.py`

- [ ] **Step 1: Create `create_storage_account.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters.get("storage_account_name", "nxpsmoke")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    location = parameters.get("location", "eastus")
    sku = parameters.get("sku", "Standard_LRS")
    kind = parameters.get("kind", "StorageV2")

    if not creds:
        return {
            "action": "create_storage_account",
            "storage_account_name": name,
            "resource_group": rg,
            "location": location,
            "mock": True,
        }

    from ._client import get_storage_client
    from azure.mgmt.storage.models import StorageAccountCreateParameters, Sku, Kind
    storage = get_storage_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: storage.storage_accounts.begin_create(
            rg, name,
            StorageAccountCreateParameters(
                sku=Sku(name=sku),
                kind=Kind(kind),
                location=location,
            ),
        ).result(),
    )
    return {
        "action": "create_storage_account",
        "storage_account_name": name,
        "resource_group": rg,
        "location": location,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_storage_account import execute as delete
    return await delete(
        {
            "storage_account_name": execution_result.get("storage_account_name", parameters.get("storage_account_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
```

- [ ] **Step 2: Create `delete_storage_account.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters.get("storage_account_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {"action": "delete_storage_account", "storage_account_name": name, "mock": True}

    from ._client import get_storage_client
    storage = get_storage_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: storage.storage_accounts.delete(rg, name))
    return {
        "action": "delete_storage_account",
        "storage_account_name": name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "storage account deletion cannot be reversed automatically"}
```

- [ ] **Step 3: Create `create_blob_container.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    account = parameters.get("storage_account_name", "")
    container = parameters.get("container_name", "nexplane-container")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {
            "action": "create_blob_container",
            "storage_account_name": account,
            "container_name": container,
            "resource_group": rg,
            "mock": True,
        }

    from ._client import get_storage_client
    storage = get_storage_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: storage.blob_containers.create(rg, account, container, {}),
    )
    return {
        "action": "create_blob_container",
        "storage_account_name": account,
        "container_name": container,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_blob_container import execute as delete
    return await delete(
        {
            "storage_account_name": execution_result.get("storage_account_name", parameters.get("storage_account_name")),
            "container_name": execution_result.get("container_name", parameters.get("container_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
```

- [ ] **Step 4: Create `delete_blob_container.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    account = parameters.get("storage_account_name", "")
    container = parameters.get("container_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {
            "action": "delete_blob_container",
            "storage_account_name": account,
            "container_name": container,
            "mock": True,
        }

    from ._client import get_storage_client
    storage = get_storage_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: storage.blob_containers.delete(rg, account, container))
    return {
        "action": "delete_blob_container",
        "storage_account_name": account,
        "container_name": container,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "blob container deletion cannot be reversed automatically"}
```

- [ ] **Step 5: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/azure/create_storage_account.py \
        backend/app/connectors/executors/azure/delete_storage_account.py \
        backend/app/connectors/executors/azure/create_blob_container.py \
        backend/app/connectors/executors/azure/delete_blob_container.py
git commit -m "feat(azure): add storage account + blob container CRUD executors"
```

---

### Task 2: Storage CRUD change types + migration 032

**Files:**
- Create: 4 CT definition JSONs
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/032_add_azure_storage_crud_types.py`

- [ ] **Step 1: Create the 4 CT definition JSONs**

Create `backend/app/connectors/change_type_definitions/azure_storage_account_create.json`:
```json
{
  "change_type": "azure_storage_account_create",
  "display_name": "Create Azure Storage Account",
  "steps": [{"generic_action": "create_storage_account", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_storage_account",
  "rollback_connector_type": "azure"
}
```

Create `backend/app/connectors/change_type_definitions/azure_storage_account_delete.json`:
```json
{
  "change_type": "azure_storage_account_delete",
  "display_name": "Delete Azure Storage Account",
  "steps": [{"generic_action": "delete_storage_account", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

Create `backend/app/connectors/change_type_definitions/azure_blob_container_create.json`:
```json
{
  "change_type": "azure_blob_container_create",
  "display_name": "Create Azure Blob Container",
  "steps": [{"generic_action": "create_blob_container", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_blob_container",
  "rollback_connector_type": "azure"
}
```

Create `backend/app/connectors/change_type_definitions/azure_blob_container_delete.json`:
```json
{
  "change_type": "azure_blob_container_delete",
  "display_name": "Delete Azure Blob Container",
  "steps": [{"generic_action": "delete_blob_container", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 2: Add 4 ChangeType enum values**

In `backend/app/models/change_request.py`, find the line:
```python
    azure_rotate_storage_key = "azure_rotate_storage_key"
```

Add immediately after:
```python
    azure_storage_account_create = "azure_storage_account_create"
    azure_storage_account_delete = "azure_storage_account_delete"
    azure_blob_container_create = "azure_blob_container_create"
    azure_blob_container_delete = "azure_blob_container_delete"
```

- [ ] **Step 3: Create migration 032**

Create `backend/alembic/versions/032_add_azure_storage_crud_types.py`:

```python
"""add Azure storage CRUD change types

Revision ID: 032
Revises: 031
Create Date: 2026-05-06
"""
from alembic import op

revision = '032'
down_revision = '031'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'azure_storage_account_create', 'azure_storage_account_delete',
        'azure_blob_container_create', 'azure_blob_container_delete',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
```

- [ ] **Step 4: Run migration**

```bash
docker exec nexplane-backend-1 alembic upgrade head 2>&1 | tail -5
```

Expected output contains: `Running upgrade 031 -> 032`

- [ ] **Step 5: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/change_type_definitions/azure_storage_account_create.json \
        backend/app/connectors/change_type_definitions/azure_storage_account_delete.json \
        backend/app/connectors/change_type_definitions/azure_blob_container_create.json \
        backend/app/connectors/change_type_definitions/azure_blob_container_delete.json \
        backend/app/models/change_request.py \
        backend/alembic/versions/032_add_azure_storage_crud_types.py
git commit -m "feat(azure): add storage account + blob container change types + migration 032"
```

---

### Task 3: Phase V smoke test

**Files:**
- Modify: `backend/tests/smoke/smoke_helpers.py`
- Modify: `backend/tests/smoke/test_azure_live.py`

- [ ] **Step 1: Add `_get_azure_storage_client()` to `smoke_helpers.py`**

In `backend/tests/smoke/smoke_helpers.py`, after the `_get_azure_compute_client()` function (around line 375), add:

```python
def _get_azure_storage_client():
    """Return an Azure StorageManagementClient using cached credentials."""
    _get_azure_compute_client()  # populates _azure_creds_cache
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.storage import StorageManagementClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return StorageManagementClient(credential, creds['subscription_id'])
```

- [ ] **Step 2: Replace `run_phase_v_stub` with `run_phase_v` in `test_azure_live.py`**

In `backend/tests/smoke/test_azure_live.py`, update the import line to include `_get_azure_storage_client`:
```python
from smoke_helpers import (
    AZURE_SMOKE_VM, TIMEOUT_SECONDS,
    NexplaneClient, log, fail,
    _azure_creds_cache, _get_azure_compute_client, _get_azure_storage_client,
    make_base_parser,
)
```

Replace the entire `run_phase_v_stub` function with:

```python
def run_phase_v(client: NexplaneClient, cloud_account_id: str, azure_resource_group: str) -> None:
    """Phase V: Azure storage account + blob container CRUD with rollback stack."""
    print("\n[Phase V] Azure Storage Account + Blob Container CRUD")

    if not azure_resource_group:
        fail("Phase V requires --azure-resource-group")

    import secrets as _secrets
    account_name = f"nxpsmoke{_secrets.token_hex(4)}"  # ≤24 chars, globally unique
    container_name = "nexplane-smoke-container"
    rollback_stack: list[tuple[str, str]] = []

    storage = _get_azure_storage_client()

    try:
        # 1. Create storage account via CR
        cr = client.run_cr(
            "[Phase V] create storage account", "azure_storage_account_create", cloud_account_id,
            {"storage_account_name": account_name, "resource_group": azure_resource_group,
             "location": "eastus"},
        )
        rollback_stack.append((cr["id"], "azure_storage_account_create"))

        # SDK verify: account exists with kind StorageV2
        if storage:
            acct = storage.storage_accounts.get_properties(azure_resource_group, account_name)
            assert str(acct.kind).lower() in ("storagev2", "storagev2"), \
                f"Unexpected account kind: {acct.kind}"
            log(f"Storage account verified: {account_name} (kind={acct.kind})")
        else:
            log(f"Storage account created (SDK verification skipped — no credentials)")

        # 2. Create blob container via CR
        cr = client.run_cr(
            "[Phase V] create blob container", "azure_blob_container_create", cloud_account_id,
            {"storage_account_name": account_name, "container_name": container_name,
             "resource_group": azure_resource_group},
        )
        rollback_stack.append((cr["id"], "azure_blob_container_create"))

        # SDK verify: container exists
        if storage:
            container = storage.blob_containers.get(azure_resource_group, account_name, container_name)
            assert container.name == container_name, \
                f"Container name mismatch: {container.name}"
            log(f"Blob container verified: {container_name}")

        # 3. Explicit blob container delete via CR
        client.run_cr(
            "[Phase V] delete blob container", "azure_blob_container_delete", cloud_account_id,
            {"storage_account_name": account_name, "container_name": container_name,
             "resource_group": azure_resource_group},
        )
        rollback_stack.pop()  # container already deleted

        # SDK verify: container gone
        if storage:
            containers = list(storage.blob_containers.list(azure_resource_group, account_name))
            assert not any(c.name == container_name for c in containers), \
                f"Container {container_name} still exists after delete"
            log("Blob container deleted and verified gone")

        log("Phase V complete")

    except Exception as e:
        print(f"\n❌ Phase V failed: {e}")
        raise
    finally:
        print("  [Phase V cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete storage account via SDK
        if storage:
            try:
                storage.storage_accounts.delete(azure_resource_group, account_name)
                print(f"  Safety net: deleted storage account {account_name}")
            except Exception:
                pass
```

- [ ] **Step 3: Update `main()` to call `run_phase_v` instead of `run_phase_v_stub`**

In `main()`, find:
```python
        if "V" in phases:
            run_phase_v_stub(client, cloud_account_id, args.azure_resource_group)
```

Replace with:
```python
        if "V" in phases:
            run_phase_v(client, cloud_account_id, args.azure_resource_group)
```

- [ ] **Step 4: Update the module docstring**

Find the line in the docstring:
```
    U-Z Sub-project stubs (not yet implemented)
```

Replace with:
```
    V  Azure Storage account + blob container CRUD with rollback stack
    U,X,Y,Z Sub-project stubs (not yet implemented)
```

- [ ] **Step 5: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 6: Verify file parses**

```bash
docker exec nexplane-backend-1 python -c "
import sys
sys.path.insert(0, '/app/tests/smoke')
import test_azure_live
print('Phase V:', test_azure_live.run_phase_v)
print('OK')
"
```

Expected: prints function reference and OK.

- [ ] **Step 7: Commit**

```bash
git add backend/tests/smoke/smoke_helpers.py backend/tests/smoke/test_azure_live.py
git commit -m "feat(azure): implement Phase V — storage account + blob container CRUD smoke test"
```

---

## Sub-project W

---

### Task 4: Add azure-mgmt-msi + azure-mgmt-authorization + _client.py + IAM executors

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/connectors/executors/azure/_client.py`
- Create: `backend/app/connectors/executors/azure/create_managed_identity.py`
- Create: `backend/app/connectors/executors/azure/delete_managed_identity.py`
- Create: `backend/app/connectors/executors/azure/assign_role.py`
- Create: `backend/app/connectors/executors/azure/remove_role_assignment.py`

- [ ] **Step 1: Add SDK packages to `requirements.txt`**

In `backend/requirements.txt`, after the line `azure-mgmt-storage>=22.0.0`, add:
```
azure-mgmt-msi>=7.0.0
azure-mgmt-authorization>=4.0.0
```

Install in the running container:
```bash
docker exec nexplane-backend-1 pip install azure-mgmt-msi azure-mgmt-authorization 2>&1 | tail -3
```

Expected: Successfully installed (or already satisfied).

- [ ] **Step 2: Add `get_msi_client` and `get_authorization_client` to `_client.py`**

In `backend/app/connectors/executors/azure/_client.py`, after the `get_storage_client` function, add:

```python
def get_msi_client(creds: dict):
    from azure.mgmt.msi import ManagedServiceIdentityClient
    return ManagedServiceIdentityClient(get_credential(creds), creds['subscription_id'])


def get_authorization_client(creds: dict):
    from azure.mgmt.authorization import AuthorizationManagementClient
    return AuthorizationManagementClient(get_credential(creds), creds['subscription_id'])
```

- [ ] **Step 3: Create `create_managed_identity.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters.get("identity_name", "nexplane-identity")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))
    location = parameters.get("location", "eastus")

    if not creds:
        return {
            "action": "create_managed_identity",
            "identity_name": name,
            "resource_group": rg,
            "principal_id": "mock-principal-id",
            "client_id": "mock-client-id",
            "mock": True,
        }

    from ._client import get_msi_client
    msi = get_msi_client(creds)
    loop = asyncio.get_running_loop()
    identity = await loop.run_in_executor(
        None,
        lambda: msi.user_assigned_identities.create_or_update(rg, name, {"location": location}),
    )
    return {
        "action": "create_managed_identity",
        "identity_name": name,
        "resource_group": rg,
        "principal_id": str(identity.principal_id),
        "client_id": str(identity.client_id),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.delete_managed_identity import execute as delete
    return await delete(
        {
            "identity_name": execution_result.get("identity_name", parameters.get("identity_name")),
            "resource_group": execution_result.get("resource_group", parameters.get("resource_group")),
        },
        [], connector,
    )
```

- [ ] **Step 4: Create `delete_managed_identity.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters.get("identity_name", "")
    rg = parameters.get("resource_group", creds.get("resource_group", "default"))

    if not creds:
        return {"action": "delete_managed_identity", "identity_name": name, "mock": True}

    from ._client import get_msi_client
    msi = get_msi_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: msi.user_assigned_identities.delete(rg, name))
    return {
        "action": "delete_managed_identity",
        "identity_name": name,
        "resource_group": rg,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "managed identity deletion cannot be reversed automatically"}
```

- [ ] **Step 5: Create `assign_role.py`**

```python
import asyncio
import uuid
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    principal_id = parameters.get("principal_id", "")
    role_name = parameters.get("role_definition_name", "Reader")
    scope = parameters.get("scope", f"/subscriptions/{creds.get('subscription_id', 'mock')}")

    if not creds:
        return {
            "action": "assign_role",
            "assignment_id": "mock-assignment-id",
            "principal_id": principal_id,
            "role_definition_name": role_name,
            "scope": scope,
            "mock": True,
        }

    from ._client import get_authorization_client
    auth = get_authorization_client(creds)
    loop = asyncio.get_running_loop()

    # Look up built-in role definition ID by name
    roles = await loop.run_in_executor(
        None,
        lambda: list(auth.role_definitions.list(scope, filter=f"roleName eq '{role_name}'")),
    )
    if not roles:
        raise ValueError(f"Role '{role_name}' not found at scope '{scope}'")
    role_definition_id = roles[0].id

    assignment_id = str(uuid.uuid4())
    await loop.run_in_executor(
        None,
        lambda: auth.role_assignments.create(
            scope, assignment_id,
            {"role_definition_id": role_definition_id, "principal_id": principal_id},
        ),
    )
    return {
        "action": "assign_role",
        "assignment_id": assignment_id,
        "principal_id": principal_id,
        "role_definition_name": role_name,
        "scope": scope,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.azure.remove_role_assignment import execute as remove
    return await remove(
        {
            "assignment_id": execution_result.get("assignment_id"),
            "scope": execution_result.get("scope", parameters.get("scope")),
        },
        [], connector,
    )
```

- [ ] **Step 6: Create `remove_role_assignment.py`**

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    assignment_id = parameters.get("assignment_id", "")
    scope = parameters.get("scope", f"/subscriptions/{creds.get('subscription_id', 'mock')}")

    if not creds:
        return {"action": "remove_role_assignment", "assignment_id": assignment_id, "mock": True}

    from ._client import get_authorization_client
    auth = get_authorization_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: auth.role_assignments.delete(scope, assignment_id))
    return {
        "action": "remove_role_assignment",
        "assignment_id": assignment_id,
        "scope": scope,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "role assignment deletion cannot be reversed automatically"}
```

- [ ] **Step 7: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/requirements.txt \
        backend/app/connectors/executors/azure/_client.py \
        backend/app/connectors/executors/azure/create_managed_identity.py \
        backend/app/connectors/executors/azure/delete_managed_identity.py \
        backend/app/connectors/executors/azure/assign_role.py \
        backend/app/connectors/executors/azure/remove_role_assignment.py
git commit -m "feat(azure): add managed identity + RBAC role assignment executors; add azure-mgmt-msi/authorization deps"
```

---

### Task 5: IAM change types + migration 033

**Files:**
- Create: 4 CT definition JSONs
- Modify: `backend/app/models/change_request.py`
- Create: `backend/alembic/versions/033_add_azure_iam_types.py`

- [ ] **Step 1: Create the 4 CT definition JSONs**

Create `backend/app/connectors/change_type_definitions/azure_managed_identity_create.json`:
```json
{
  "change_type": "azure_managed_identity_create",
  "display_name": "Create Azure Managed Identity",
  "steps": [{"generic_action": "create_managed_identity", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "delete_managed_identity",
  "rollback_connector_type": "azure"
}
```

Create `backend/app/connectors/change_type_definitions/azure_managed_identity_delete.json`:
```json
{
  "change_type": "azure_managed_identity_delete",
  "display_name": "Delete Azure Managed Identity",
  "steps": [{"generic_action": "delete_managed_identity", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

Create `backend/app/connectors/change_type_definitions/azure_role_assignment_create.json`:
```json
{
  "change_type": "azure_role_assignment_create",
  "display_name": "Create Azure RBAC Role Assignment",
  "steps": [{"generic_action": "assign_role", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "remove_role_assignment",
  "rollback_connector_type": "azure"
}
```

Create `backend/app/connectors/change_type_definitions/azure_role_assignment_delete.json`:
```json
{
  "change_type": "azure_role_assignment_delete",
  "display_name": "Delete Azure RBAC Role Assignment",
  "steps": [{"generic_action": "remove_role_assignment", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

- [ ] **Step 2: Add 4 ChangeType enum values**

In `backend/app/models/change_request.py`, find:
```python
    azure_blob_container_delete = "azure_blob_container_delete"
```

Add immediately after:
```python
    azure_managed_identity_create = "azure_managed_identity_create"
    azure_managed_identity_delete = "azure_managed_identity_delete"
    azure_role_assignment_create = "azure_role_assignment_create"
    azure_role_assignment_delete = "azure_role_assignment_delete"
```

- [ ] **Step 3: Create migration 033**

Create `backend/alembic/versions/033_add_azure_iam_types.py`:

```python
"""add Azure IAM change types

Revision ID: 033
Revises: 032
Create Date: 2026-05-06
"""
from alembic import op

revision = '033'
down_revision = '032'
branch_labels = None
depends_on = None


def upgrade():
    new_types = [
        'azure_managed_identity_create', 'azure_managed_identity_delete',
        'azure_role_assignment_create', 'azure_role_assignment_delete',
    ]
    for t in new_types:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
```

- [ ] **Step 4: Run migration**

```bash
docker exec nexplane-backend-1 alembic upgrade head 2>&1 | tail -5
```

Expected: `Running upgrade 032 -> 033`

- [ ] **Step 5: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/change_type_definitions/azure_managed_identity_create.json \
        backend/app/connectors/change_type_definitions/azure_managed_identity_delete.json \
        backend/app/connectors/change_type_definitions/azure_role_assignment_create.json \
        backend/app/connectors/change_type_definitions/azure_role_assignment_delete.json \
        backend/app/models/change_request.py \
        backend/alembic/versions/033_add_azure_iam_types.py
git commit -m "feat(azure): add managed identity + RBAC role assignment change types + migration 033"
```

---

### Task 6: Phase W smoke test

**Files:**
- Modify: `backend/tests/smoke/smoke_helpers.py`
- Modify: `backend/tests/smoke/test_azure_live.py`

- [ ] **Step 1: Add `_get_azure_msi_client()` and `_get_azure_authorization_client()` to `smoke_helpers.py`**

In `backend/tests/smoke/smoke_helpers.py`, after `_get_azure_storage_client()`, add:

```python
def _get_azure_msi_client():
    """Return an Azure ManagedServiceIdentityClient using cached credentials."""
    _get_azure_compute_client()  # populates _azure_creds_cache
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.msi import ManagedServiceIdentityClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return ManagedServiceIdentityClient(credential, creds['subscription_id'])


def _get_azure_authorization_client():
    """Return an Azure AuthorizationManagementClient using cached credentials."""
    _get_azure_compute_client()  # populates _azure_creds_cache
    creds = _azure_creds_cache
    if not creds:
        return None
    from azure.identity import ClientSecretCredential
    from azure.mgmt.authorization import AuthorizationManagementClient
    credential = ClientSecretCredential(
        tenant_id=creds['tenant_id'],
        client_id=creds['client_id'],
        client_secret=creds['client_secret'],
    )
    return AuthorizationManagementClient(credential, creds['subscription_id'])
```

- [ ] **Step 2: Replace `run_phase_w_stub` with `run_phase_w` in `test_azure_live.py`**

Update the import line at the top of `test_azure_live.py` to include the new helpers:
```python
from smoke_helpers import (
    AZURE_SMOKE_VM, TIMEOUT_SECONDS,
    NexplaneClient, log, fail,
    _azure_creds_cache, _get_azure_compute_client,
    _get_azure_storage_client, _get_azure_msi_client, _get_azure_authorization_client,
    make_base_parser,
)
```

Replace the entire `run_phase_w_stub` function with:

```python
def run_phase_w(client: NexplaneClient, cloud_account_id: str, azure_resource_group: str) -> None:
    """Phase W: Azure managed identity + RBAC role assignment lifecycle."""
    print("\n[Phase W] Azure Managed Identity + RBAC Role Assignment")

    if not azure_resource_group:
        fail("Phase W requires --azure-resource-group")

    creds = _get_azure_creds()
    subscription_id = creds.get("subscription_id", "")
    if not subscription_id:
        fail("Phase W requires Azure credentials with subscription_id")

    import secrets as _secrets
    identity_name = f"nexplane-smoke-id-{_secrets.token_hex(4)}"
    scope = f"/subscriptions/{subscription_id}/resourceGroups/{azure_resource_group}"
    rollback_stack: list[tuple[str, str]] = []

    msi = _get_azure_msi_client()
    auth = _get_azure_authorization_client()

    try:
        # 1. Create managed identity via CR
        cr = client.run_cr(
            "[Phase W] create managed identity", "azure_managed_identity_create", cloud_account_id,
            {"identity_name": identity_name, "resource_group": azure_resource_group,
             "location": "eastus"},
        )
        rollback_stack.append((cr["id"], "azure_managed_identity_create"))

        # SDK verify: identity exists with a principal_id
        principal_id = None
        if msi:
            identity = msi.user_assigned_identities.get(azure_resource_group, identity_name)
            assert identity.principal_id, "Managed identity has no principal_id"
            principal_id = str(identity.principal_id)
            log(f"Managed identity verified: {identity_name} ({principal_id})")
        else:
            log("Managed identity created (SDK verification skipped — no credentials)")
            principal_id = "mock-principal-id"

        # 2. Assign Reader role at resource group scope via CR
        cr = client.run_cr(
            "[Phase W] assign Reader role", "azure_role_assignment_create", cloud_account_id,
            {"principal_id": principal_id, "role_definition_name": "Reader", "scope": scope},
        )
        rollback_stack.append((cr["id"], "azure_role_assignment_create"))

        # SDK verify: role assignment exists for this principal
        assignment_id = None
        if auth:
            assignments = list(auth.role_assignments.list_for_scope(
                scope, filter=f"principalId eq '{principal_id}'"
            ))
            assert len(assignments) > 0, \
                f"No role assignments found for principal {principal_id} at scope {scope}"
            assignment_id = assignments[0].name
            log(f"Role assignment verified: {assignment_id}")

        # 3. Explicit role assignment delete via CR
        client.run_cr(
            "[Phase W] delete role assignment", "azure_role_assignment_delete", cloud_account_id,
            {"assignment_id": assignment_id or cr["id"], "scope": scope},
        )
        rollback_stack.pop()  # role assignment already deleted

        # SDK verify: assignment gone
        if auth and principal_id and principal_id != "mock-principal-id":
            remaining = list(auth.role_assignments.list_for_scope(
                scope, filter=f"principalId eq '{principal_id}'"
            ))
            assert len(remaining) == 0, \
                f"Role assignment still present after delete: {remaining}"
            log("Role assignment deleted and verified gone")

        log("Phase W complete")

    except Exception as e:
        print(f"\n❌ Phase W failed: {e}")
        raise
    finally:
        print("  [Phase W cleanup — rollback stack]")
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete managed identity via SDK
        if msi:
            try:
                msi.user_assigned_identities.delete(azure_resource_group, identity_name)
                print(f"  Safety net: deleted managed identity {identity_name}")
            except Exception:
                pass
```

- [ ] **Step 3: Update `main()` to call `run_phase_w` instead of `run_phase_w_stub`**

Find:
```python
        if "W" in phases:
            run_phase_w_stub(client, cloud_account_id, args.azure_resource_group)
```

Replace with:
```python
        if "W" in phases:
            run_phase_w(client, cloud_account_id, args.azure_resource_group)
```

- [ ] **Step 4: Update module docstring**

Find the line:
```
    V  Azure Storage account + blob container CRUD with rollback stack
    U,X,Y,Z Sub-project stubs (not yet implemented)
```

Replace with:
```
    V  Azure Storage account + blob container CRUD with rollback stack
    W  Azure managed identity + RBAC role assignment lifecycle
    U,X,Y,Z Sub-project stubs (not yet implemented)
```

Also update the `--phases` help string in `main()`:
```python
        help=(
            "Comma-separated phases to run. "
            "N-O: VM lifecycle. P=NSG, Q=Storage, R=Tagging, S=Terraform, T=Ansible. "
            "V=Storage-CRUD, W=IAM-RBAC. U,X,Y,Z=stubs. Default: N,O."
        ),
```

- [ ] **Step 5: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all pass.

- [ ] **Step 6: Verify file parses**

```bash
docker exec nexplane-backend-1 python -c "
import sys
sys.path.insert(0, '/app/tests/smoke')
import test_azure_live
print('Phase V:', test_azure_live.run_phase_v)
print('Phase W:', test_azure_live.run_phase_w)
print('OK')
"
```

Expected: prints both function references and OK.

- [ ] **Step 7: Commit**

```bash
git add backend/tests/smoke/smoke_helpers.py backend/tests/smoke/test_azure_live.py
git commit -m "feat(azure): implement Phase W — managed identity + RBAC role assignment smoke test"
```

---

## Self-review

**Spec coverage:**
- ✅ `create_storage_account.py` with `rollback()` — Task 1
- ✅ `delete_storage_account.py` — Task 1
- ✅ `create_blob_container.py` with `rollback()` — Task 1
- ✅ `delete_blob_container.py` — Task 1
- ✅ 4 storage CT definitions with correct rollback_action — Task 2
- ✅ 4 storage ChangeType enum values — Task 2
- ✅ Migration 032 — Task 2
- ✅ `_get_azure_storage_client()` in smoke_helpers — Task 3
- ✅ Phase V: create account → verify → create container → verify → explicit delete → verify gone → rollback account — Task 3
- ✅ azure-mgmt-msi + azure-mgmt-authorization in requirements.txt — Task 4
- ✅ `get_msi_client`, `get_authorization_client` in `_client.py` — Task 4
- ✅ 4 IAM executors with `rollback()` — Task 4
- ✅ 4 IAM CT definitions — Task 5
- ✅ 4 IAM ChangeType enum values — Task 5
- ✅ Migration 033 — Task 5
- ✅ `_get_azure_msi_client()`, `_get_azure_authorization_client()` in smoke_helpers — Task 6
- ✅ Phase W: create identity → verify → assign Reader role → verify → explicit delete assignment → verify gone → rollback identity — Task 6
- ✅ `run_phase_v_stub` and `run_phase_w_stub` replaced in main() — Tasks 3, 6
- ✅ Module docstring and --phases help text updated — Tasks 3, 6

**Placeholder scan:** None. All steps have complete code.

**Type consistency:**
- `storage_account_name` used consistently across create/delete/container executors
- `assignment_id` returned by `assign_role` and consumed by `remove_role_assignment`
- `principal_id` flows from `create_managed_identity` result → Phase W smoke test → `assign_role` desired_outcome
- `rollback_stack: list[tuple[str, str]]` — consistent with all other Azure phases
