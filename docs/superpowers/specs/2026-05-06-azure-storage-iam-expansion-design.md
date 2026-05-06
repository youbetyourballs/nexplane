# Azure Storage Account + IAM Expansion Design

**Date:** 2026-05-06
**Status:** Approved for implementation

---

## Goals

1. Add Azure storage account and blob container CRUD as Nexplane change types (Phase V).
2. Add Azure managed identity and RBAC role assignment as Nexplane change types (Phase W).
3. Wire both into live smoke test phases in `test_azure_live.py`.

---

## Current State

- `backend/app/connectors/executors/azure/_client.py` provides `get_compute_client`, `get_storage_client`, `get_network_client` — needs two additions: `get_msi_client` and `get_authorization_client`.
- `disable_public_blob_access.py`, `enable_public_blob_access.py`, `rotate_storage_key.py` exist — storage account-level executors already present.
- No `create_storage_account.py`, `delete_storage_account.py`, `create_blob_container.py`, `delete_blob_container.py` executors exist.
- No managed identity or RBAC executors exist.
- Stub phases U–Z are defined in the docstring of `test_azure_live.py` but bodies are not implemented.
- Most recent migration: `031_add_alb_register_delete_types.py`.

---

## Architecture

Two sub-projects, each independent:

**Sub-project V — Storage CRUD:**
```
backend/app/connectors/executors/azure/
  create_storage_account.py
  delete_storage_account.py
  create_blob_container.py
  delete_blob_container.py
backend/app/connectors/change_type_definitions/
  azure_storage_account_create.json
  azure_storage_account_delete.json
  azure_blob_container_create.json
  azure_blob_container_delete.json
backend/alembic/versions/032_add_azure_storage_crud_types.py
backend/tests/smoke/test_azure_live.py   ← Phase V
```

**Sub-project W — Managed Identity + RBAC:**
```
backend/app/connectors/executors/azure/
  _client.py                             ← add get_msi_client, get_authorization_client
  create_managed_identity.py
  delete_managed_identity.py
  assign_role.py
  remove_role_assignment.py
backend/app/connectors/change_type_definitions/
  azure_managed_identity_create.json
  azure_managed_identity_delete.json
  azure_role_assignment_create.json
  azure_role_assignment_delete.json
backend/alembic/versions/033_add_azure_iam_types.py
backend/tests/smoke/test_azure_live.py   ← Phase W
```

Both also modify `backend/app/models/change_request.py` to add new `ChangeType` enum values.

---

## Sub-project V: Azure Storage Account + Blob Container CRUD

### New ChangeType enum values

```python
azure_storage_account_create = "azure_storage_account_create"
azure_storage_account_delete = "azure_storage_account_delete"
azure_blob_container_create = "azure_blob_container_create"
azure_blob_container_delete = "azure_blob_container_delete"
```

### Executor: `create_storage_account.py`

Input from `parameters`:
- `storage_account_name` (str) — must be globally unique, 3-24 chars, lowercase alphanumeric
- `resource_group` (str, falls back to `creds["resource_group"]`)
- `location` (str, default: `"eastus"`)
- `sku` (str, default: `"Standard_LRS"`)
- `kind` (str, default: `"StorageV2"`)

Real path uses `azure.mgmt.storage.StorageManagementClient.storage_accounts.begin_create_and_wait()`.

Returns:
```python
{
    "action": "create_storage_account",
    "storage_account_name": name,
    "resource_group": rg,
    "location": location,
    "executed_at": "...",
}
```

`rollback()` calls `delete_storage_account` logic inline.

### Executor: `delete_storage_account.py`

Input: `storage_account_name`, `resource_group`.
Calls `storage_accounts.delete(rg, name)`.
Returns `{"action": "delete_storage_account", "storage_account_name": name, "executed_at": "..."}`.
`rollback()` returns `{"rolled_back": False, "reason": "storage account deletion cannot be reversed"}`.

### Executor: `create_blob_container.py`

Input: `storage_account_name`, `container_name`, `resource_group`.
Calls `blob_containers.create(rg, account, container, {})`.
Returns `{"action": "create_blob_container", "storage_account_name": ..., "container_name": ..., "executed_at": ...}`.
`rollback()` calls `blob_containers.delete()` inline.

### Executor: `delete_blob_container.py`

Input: `storage_account_name`, `container_name`, `resource_group`.
Calls `blob_containers.delete(rg, account, container)`.
`rollback()` returns `{"rolled_back": False, "reason": "container deletion cannot be reversed"}`.

### CT definitions

`azure_storage_account_create.json`:
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

`azure_storage_account_delete.json`:
```json
{
  "change_type": "azure_storage_account_delete",
  "display_name": "Delete Azure Storage Account",
  "steps": [{"generic_action": "delete_storage_account", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

`azure_blob_container_create.json`:
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

`azure_blob_container_delete.json`:
```json
{
  "change_type": "azure_blob_container_delete",
  "display_name": "Delete Azure Blob Container",
  "steps": [{"generic_action": "delete_blob_container", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

### Migration 032

```python
new_types = [
    'azure_storage_account_create', 'azure_storage_account_delete',
    'azure_blob_container_create', 'azure_blob_container_delete',
]
for t in new_types:
    op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")
```

### Phase V smoke test

```python
def run_phase_v(client, cloud_account_id, azure_resource_group):
    """Phase V: Azure Storage account + blob container CRUD with rollback stack."""
    account_name = f"nxpsmoke{secrets.token_hex(4)}"  # ≤24 chars, lowercase
    container_name = "nexplane-smoke-container"
    rollback_stack = []

    try:
        # 1. Create storage account
        cr = client.run_cr("[Phase V] create storage account",
            "azure_storage_account_create", cloud_account_id,
            {"storage_account_name": account_name,
             "resource_group": azure_resource_group, "location": "eastus"})
        rollback_stack.append((cr["id"], "azure_storage_account_create"))
        # SDK verify: account exists, kind=StorageV2
        storage = _get_azure_storage_client()
        acct = storage.storage_accounts.get_properties(azure_resource_group, account_name)
        assert acct.kind == "StorageV2"
        log(f"Storage account verified: {account_name}")

        # 2. Create blob container
        cr = client.run_cr("[Phase V] create blob container",
            "azure_blob_container_create", cloud_account_id,
            {"storage_account_name": account_name, "container_name": container_name,
             "resource_group": azure_resource_group})
        rollback_stack.append((cr["id"], "azure_blob_container_create"))
        container = storage.blob_containers.get(azure_resource_group, account_name, container_name)
        assert container.name == container_name
        log(f"Blob container verified: {container_name}")

        # 3. Explicit container delete via CR
        client.run_cr("[Phase V] delete blob container",
            "azure_blob_container_delete", cloud_account_id,
            {"storage_account_name": account_name, "container_name": container_name,
             "resource_group": azure_resource_group})
        rollback_stack.pop()  # container already deleted
        # SDK verify: container gone
        containers = list(storage.blob_containers.list(azure_resource_group, account_name))
        assert not any(c.name == container_name for c in containers)
        log("Blob container deleted and verified")

        log("Phase V complete")

    except Exception as e:
        print(f"\n❌ Phase V failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete storage account via SDK
        try:
            storage = _get_azure_storage_client()
            if storage:
                try:
                    storage.storage_accounts.delete(azure_resource_group, account_name)
                    print(f"  Safety net: deleted storage account {account_name}")
                except Exception:
                    pass
        except Exception:
            pass
```

`_get_azure_storage_client()` is a new helper in `smoke_helpers.py` following the same pattern as `_get_azure_compute_client()`.

---

## Sub-project W: Azure Managed Identity + RBAC Role Assignments

### `_client.py` additions

```python
def get_msi_client(creds: dict):
    from azure.mgmt.msi import ManagedServiceIdentityClient
    return ManagedServiceIdentityClient(get_credential(creds), creds['subscription_id'])

def get_authorization_client(creds: dict):
    from azure.mgmt.authorization import AuthorizationManagementClient
    return AuthorizationManagementClient(get_credential(creds), creds['subscription_id'])
```

SDK packages required (must be present in backend container):
- `azure-mgmt-msi`
- `azure-mgmt-authorization`

### New ChangeType enum values

```python
azure_managed_identity_create = "azure_managed_identity_create"
azure_managed_identity_delete = "azure_managed_identity_delete"
azure_role_assignment_create = "azure_role_assignment_create"
azure_role_assignment_delete = "azure_role_assignment_delete"
```

### Executor: `create_managed_identity.py`

Input: `identity_name`, `resource_group`, `location` (default `"eastus"`).
Calls `msi_client.user_assigned_identities.create_or_update(rg, name, {"location": location})`.

Returns:
```python
{
    "action": "create_managed_identity",
    "identity_name": name,
    "resource_group": rg,
    "principal_id": identity.principal_id,
    "client_id": identity.client_id,
    "executed_at": "...",
}
```

`rollback()` calls `user_assigned_identities.delete(rg, name)`.

### Executor: `delete_managed_identity.py`

Input: `identity_name`, `resource_group`.
Calls `user_assigned_identities.delete(rg, name)`.
`rollback()` returns `{"rolled_back": False, "reason": "managed identity deletion cannot be reversed"}`.

### Executor: `assign_role.py`

Input:
- `principal_id` (str) — the identity's principal_id
- `role_definition_name` (str, default `"Reader"`) — built-in role name
- `scope` (str) — e.g. `/subscriptions/{subscription_id}/resourceGroups/{rg}`

Logic:
1. Look up role definition ID by name via `authorization_client.role_definitions.list(scope, filter=f"roleName eq '{role_definition_name}'")`
2. Generate a UUID for the assignment
3. Call `role_assignments.create(scope, assignment_id, {"role_definition_id": ..., "principal_id": ...})`

Returns:
```python
{
    "action": "assign_role",
    "assignment_id": assignment_id,
    "principal_id": principal_id,
    "role_definition_name": role_definition_name,
    "scope": scope,
    "executed_at": "...",
}
```

`rollback()` calls `role_assignments.delete(scope, assignment_id)`.

### Executor: `remove_role_assignment.py`

Input: `assignment_id`, `scope`.
Calls `role_assignments.delete(scope, assignment_id)`.
`rollback()` returns `{"rolled_back": False, "reason": "role assignment deletion cannot be reversed"}`.

### CT definitions

`azure_managed_identity_create.json`:
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

`azure_managed_identity_delete.json`:
```json
{
  "change_type": "azure_managed_identity_delete",
  "display_name": "Delete Azure Managed Identity",
  "steps": [{"generic_action": "delete_managed_identity", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

`azure_role_assignment_create.json`:
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

`azure_role_assignment_delete.json`:
```json
{
  "change_type": "azure_role_assignment_delete",
  "display_name": "Delete Azure RBAC Role Assignment",
  "steps": [{"generic_action": "remove_role_assignment", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable"],
  "verification_methods": ["api_check"]
}
```

### Migration 033

```python
new_types = [
    'azure_managed_identity_create', 'azure_managed_identity_delete',
    'azure_role_assignment_create', 'azure_role_assignment_delete',
]
for t in new_types:
    op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")
```

### Phase W smoke test

```python
def run_phase_w(client, cloud_account_id, azure_resource_group, azure_subscription_id):
    """Phase W: Azure managed identity + RBAC role assignment lifecycle."""
    identity_name = f"nexplane-smoke-id-{secrets.token_hex(4)}"
    scope = f"/subscriptions/{azure_subscription_id}/resourceGroups/{azure_resource_group}"
    rollback_stack = []

    try:
        # 1. Create managed identity
        cr = client.run_cr("[Phase W] create managed identity",
            "azure_managed_identity_create", cloud_account_id,
            {"identity_name": identity_name, "resource_group": azure_resource_group,
             "location": "eastus"})
        rollback_stack.append((cr["id"], "azure_managed_identity_create"))
        # SDK verify: identity exists, has principal_id
        msi = _get_azure_msi_client()
        identity = msi.user_assigned_identities.get(azure_resource_group, identity_name)
        assert identity.principal_id
        principal_id = identity.principal_id
        log(f"Managed identity created: {identity_name} ({principal_id})")

        # 2. Assign Reader role at resource group scope
        cr = client.run_cr("[Phase W] assign Reader role",
            "azure_role_assignment_create", cloud_account_id,
            {"principal_id": principal_id, "role_definition_name": "Reader", "scope": scope})
        rollback_stack.append((cr["id"], "azure_role_assignment_create"))
        # SDK verify: assignment exists
        auth = _get_azure_authorization_client()
        assignments = list(auth.role_assignments.list_for_scope(
            scope, filter=f"principalId eq '{principal_id}'"))
        assert len(assignments) > 0
        assignment_id = assignments[0].name
        log(f"Role assignment verified: {assignment_id}")

        # 3. Explicit role assignment delete
        client.run_cr("[Phase W] delete role assignment",
            "azure_role_assignment_delete", cloud_account_id,
            {"assignment_id": assignment_id, "scope": scope})
        rollback_stack.pop()  # role assignment already deleted
        # SDK verify: assignment gone
        remaining = list(auth.role_assignments.list_for_scope(
            scope, filter=f"principalId eq '{principal_id}'"))
        assert len(remaining) == 0
        log("Role assignment deleted and verified")

        log("Phase W complete")

    except Exception as e:
        print(f"\n❌ Phase W failed: {e}")
        raise
    finally:
        for cr_id, label in reversed(rollback_stack):
            client.rollback_cr(cr_id, label)
        # Safety net: delete managed identity via SDK
        try:
            msi = _get_azure_msi_client()
            if msi:
                try:
                    msi.user_assigned_identities.delete(azure_resource_group, identity_name)
                    print(f"  Safety net: deleted managed identity {identity_name}")
                except Exception:
                    pass
        except Exception:
            pass
```

New smoke_helpers additions:
- `_get_azure_storage_client()` — mirrors `_get_azure_compute_client()`, returns `StorageManagementClient`
- `_get_azure_msi_client()` — returns `ManagedServiceIdentityClient`
- `_get_azure_authorization_client()` — returns `AuthorizationManagementClient`

Phase W also needs `azure_subscription_id` from the connector credentials (already available in `creds['subscription_id']`). The smoke test CLI needs a `--azure-subscription-id` arg or reads it from the connector credentials dynamically. Since credentials already contain `subscription_id`, use `_get_azure_creds()["subscription_id"]` in the test — no new CLI arg needed.

---

## Out of Scope

- Azure AD / service principal CRUD (requires Azure AD Graph permissions — separate connector)
- Phases X (DNS), Y (SQL), Z (Monitor) — deferred
- Frontend UI changes — executors and smoke tests only

---

## Execution Order

1. Sub-project V (migration 032, 4 executors, Phase V) — self-contained, no dependencies
2. Sub-project W (migration 033, 4 executors + _client.py update, Phase W) — depends on 032 for migration chain only
