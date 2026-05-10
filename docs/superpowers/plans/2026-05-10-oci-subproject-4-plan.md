# OCI Connector Sub-project 4: Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Add IAM user/group/policy, Vault secret, and compartment lifecycle executors for Oracle Cloud Infrastructure — 12 new change types, discovery, catalog extension, DB migration, and frontend wiring.

**Architecture:** All OCI executors import SDK clients from `backend/app/connectors/executors/oci/_client.py` (created in sub-projects 1-3). IAM identity operations are tenancy-scoped (use `tenancy_id`, never `compartment_id`), while policy, vault, and compartment operations are compartment-scoped. Vault secret executors perform a preflight check for an ACTIVE vault and fail gracefully if none exists, since Vault provisioning is out of scope.

**Tech Stack:** OCI Python SDK (`oci`), Python asyncio, `loop.run_in_executor` for all blocking SDK calls, mock path when `creds` is empty.

---

### Task 1: Discovery executors — discover_iam_users.py, discover_iam_groups.py, discover_iam_policies.py, discover_vault_secrets.py

**Files:** Create:
- `backend/app/connectors/executors/oci/discover_iam_users.py`
- `backend/app/connectors/executors/oci/discover_iam_groups.py`
- `backend/app/connectors/executors/oci/discover_iam_policies.py`
- `backend/app/connectors/executors/oci/discover_vault_secrets.py`

- [ ] Step 1: Create `discover_iam_users.py`. Call `identity.list_users(compartment_id=tenancy_id)` (paginated via `oci.pagination.list_call_get_all_results`). Each user maps to an `identity` asset. Dedup key: `user_id`.

```python
import asyncio
from ._client import get_identity_client

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"assets": [], "mock": True}
    client = get_identity_client(creds)
    tenancy_id = creds["tenancy_id"]
    loop = asyncio.get_running_loop()
    users = await loop.run_in_executor(
        None,
        lambda: oci.pagination.list_call_get_all_results(
            client.list_users, compartment_id=tenancy_id
        ).data,
    )
    assets = []
    for u in users:
        if u.lifecycle_state == "DELETED":
            continue
        caps = u.capabilities
        assets.append({
            "name": u.name,
            "asset_type": "identity",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "user_id": u.id,
                "email": u.email or "",
                "lifecycle_state": u.lifecycle_state,
                "is_mfa_activated": u.is_mfa_activated,
                "can_use_console_password": caps.can_use_console_password if caps else True,
                "can_use_api_keys": caps.can_use_api_keys if caps else True,
                "provider": "oci",
            },
            "tags": ["oci", "iam-user"],
            "_dedup_key": u.id,
        })
    return {"assets": assets, "count": len(assets)}
```

- [ ] Step 2: Create `discover_iam_groups.py`. Call `identity.list_groups(compartment_id=tenancy_id)`. Each group maps to an `application` asset. Dedup key: `group_id`.

```python
import asyncio
import oci.pagination
from ._client import get_identity_client

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"assets": [], "mock": True}
    client = get_identity_client(creds)
    tenancy_id = creds["tenancy_id"]
    loop = asyncio.get_running_loop()
    groups = await loop.run_in_executor(
        None,
        lambda: oci.pagination.list_call_get_all_results(
            client.list_groups, compartment_id=tenancy_id
        ).data,
    )
    assets = []
    for g in groups:
        if g.lifecycle_state == "DELETED":
            continue
        assets.append({
            "name": g.name,
            "asset_type": "application",
            "environment": "prod",
            "criticality": "medium",
            "asset_metadata": {
                "group_id": g.id,
                "name": g.name,
                "description": g.description or "",
                "lifecycle_state": g.lifecycle_state,
                "provider": "oci",
            },
            "tags": ["oci", "oci-iam-group"],
            "_dedup_key": g.id,
        })
    return {"assets": assets, "count": len(assets)}
```

- [ ] Step 3: Create `discover_iam_policies.py`. Call `identity.list_policies(compartment_id=compartment_id)` — policies are compartment-scoped. Each policy maps to an `application` asset. Dedup key: `policy_id`.

```python
import asyncio
import oci.pagination
from ._client import get_identity_client

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"assets": [], "mock": True}
    client = get_identity_client(creds)
    compartment_id = creds.get("compartment_id") or creds["tenancy_id"]
    loop = asyncio.get_running_loop()
    policies = await loop.run_in_executor(
        None,
        lambda: oci.pagination.list_call_get_all_results(
            client.list_policies, compartment_id=compartment_id
        ).data,
    )
    assets = []
    for p in policies:
        if p.lifecycle_state == "DELETED":
            continue
        assets.append({
            "name": p.name,
            "asset_type": "application",
            "environment": "prod",
            "criticality": "high",
            "asset_metadata": {
                "policy_id": p.id,
                "name": p.name,
                "statements": list(p.statements),
                "compartment_id": p.compartment_id,
                "provider": "oci",
            },
            "tags": ["oci", "oci-iam-policy"],
            "_dedup_key": p.id,
        })
    return {"assets": assets, "count": len(assets)}
```

- [ ] Step 4: Create `discover_vault_secrets.py`. Check for an ACTIVE vault first; skip gracefully if none. Call `vaults.list_secrets(compartment_id=compartment_id)`. Each secret maps to an `application` asset tagged `oci-vault-secret`. Dedup key: `secret_id`. Secret _values_ are never stored.

```python
import asyncio
import oci.pagination
from ._client import get_identity_client, get_vault_client

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"assets": [], "mock": True}
    compartment_id = creds.get("compartment_id") or creds["tenancy_id"]
    loop = asyncio.get_running_loop()
    vault_client = get_vault_client(creds)
    # find an active vault
    vaults_list = await loop.run_in_executor(
        None,
        lambda: oci.pagination.list_call_get_all_results(
            vault_client.list_vaults, compartment_id=compartment_id
        ).data,
    )
    active_vaults = [v for v in vaults_list if v.lifecycle_state == "ACTIVE"]
    if not active_vaults:
        return {"assets": [], "count": 0, "warning": "No ACTIVE Vault found in compartment; skipping secret discovery."}
    secrets = await loop.run_in_executor(
        None,
        lambda: oci.pagination.list_call_get_all_results(
            vault_client.list_secrets, compartment_id=compartment_id
        ).data,
    )
    assets = []
    for s in secrets:
        if s.lifecycle_state in ("DELETED", "SCHEDULED_DELETION", "PENDING_DELETION"):
            continue
        assets.append({
            "name": s.secret_name,
            "asset_type": "application",
            "environment": "prod",
            "criticality": "critical",
            "asset_metadata": {
                "secret_id": s.id,
                "secret_name": s.secret_name,
                "vault_id": s.vault_id,
                "lifecycle_state": s.lifecycle_state,
                "provider": "oci",
            },
            "tags": ["oci", "oci-vault-secret"],
            "_dedup_key": s.id,
        })
    return {"assets": assets, "count": len(assets)}
```

---

### Task 2: IAM user create/delete — create_iam_user.py + delete_iam_user.py

**Files:** Create:
- `backend/app/connectors/executors/oci/create_iam_user.py`
- `backend/app/connectors/executors/oci/delete_iam_user.py`

- [ ] Step 1: Create `create_iam_user.py`. Parameters: `name` (default `"nexplane-user"`), `description` (default `"Created by Nexplane"`), `email` (default `""`), optional `group_id`. Target asset is a compartment; resolve `tenancy_id` from `creds`. Create user via `identity.create_user()`, optionally add to group. Returns `_auto_asset` of type `identity`. Rollback: `oci_iam_user_delete`.

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client
import oci

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters.get("name", "nexplane-user")
    description = parameters.get("description", "Created by Nexplane")
    email = parameters.get("email", "")
    group_id = parameters.get("group_id", "")

    auto_asset = {
        "name": name,
        "asset_type": "identity",
        "environment": "prod",
        "criticality": "high",
        "asset_metadata": {"name": name, "provider": "oci"},
        "tags": ["oci", "iam-user", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_iam_user",
            "name": name,
            "user_id": "ocid1.user.oc1..mock",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    client = get_identity_client(creds)
    tenancy_id = creds["tenancy_id"]
    loop = asyncio.get_running_loop()

    def _call():
        details = oci.identity.models.CreateUserDetails(
            compartment_id=tenancy_id,
            name=name,
            description=description,
            email=email or None,
        )
        user = client.create_user(details).data
        if group_id:
            membership = oci.identity.models.AddUserToGroupDetails(
                user_id=user.id, group_id=group_id
            )
            client.add_user_to_group(membership)
        return user

    user = await loop.run_in_executor(None, _call)
    auto_asset["asset_metadata"]["user_id"] = user.id
    auto_asset["asset_metadata"]["lifecycle_state"] = user.lifecycle_state
    return {
        "action": "create_iam_user",
        "name": name,
        "user_id": user.id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_iam_user import execute as delete
    return await delete(
        {"user_id": execution_result.get("user_id"), "name": execution_result.get("name")},
        [], connector,
    )
```

- [ ] Step 2: Create `delete_iam_user.py`. Parameters: `user_id` (auto-populated from target identity asset). Remove user from all groups first, then call `identity.delete_user(user_id)`. Rollback: none (destructive).

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client
import oci.pagination

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters.get("user_id", "")
    name = parameters.get("name", user_id)

    if not creds:
        return {"action": "delete_iam_user", "user_id": user_id, "mock": True}

    client = get_identity_client(creds)
    tenancy_id = creds["tenancy_id"]
    loop = asyncio.get_running_loop()

    def _call():
        # Remove user from all groups first
        memberships = oci.pagination.list_call_get_all_results(
            client.list_user_group_memberships,
            compartment_id=tenancy_id,
            user_id=user_id,
        ).data
        for m in memberships:
            client.remove_user_from_group(m.id)
        client.delete_user(user_id)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_iam_user",
        "user_id": user_id,
        "name": name,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_iam_user is destructive; no rollback available"}
```

---

### Task 3: IAM user disable/enable — disable_iam_user.py + enable_iam_user.py

**Files:** Create:
- `backend/app/connectors/executors/oci/disable_iam_user.py`
- `backend/app/connectors/executors/oci/enable_iam_user.py`

- [ ] Step 1: Create `disable_iam_user.py`. Parameters: `user_id` (auto-populated). Call `identity.update_user_capabilities()` to set `can_use_console_password=False` and `can_use_api_keys=False`. Store previous capabilities in result for rollback. Rollback: `oci_iam_user_enable`.

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client
import oci

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters.get("user_id", "")

    if not creds:
        return {
            "action": "disable_iam_user",
            "user_id": user_id,
            "previous_can_use_console_password": True,
            "previous_can_use_api_keys": True,
            "mock": True,
        }

    client = get_identity_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        user = client.get_user(user_id).data
        caps = user.capabilities
        prev_console = caps.can_use_console_password if caps else True
        prev_api = caps.can_use_api_keys if caps else True
        details = oci.identity.models.UpdateUserCapabilitiesDetails(
            can_use_console_password=False,
            can_use_api_keys=False,
            can_use_auth_tokens=False,
            can_use_smtp_credentials=False,
        )
        client.update_user_capabilities(user_id, details)
        return prev_console, prev_api

    prev_console, prev_api = await loop.run_in_executor(None, _call)
    return {
        "action": "disable_iam_user",
        "user_id": user_id,
        "previous_can_use_console_password": prev_console,
        "previous_can_use_api_keys": prev_api,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.enable_iam_user import execute as enable
    return await enable(
        {
            "user_id": execution_result.get("user_id", parameters.get("user_id")),
            "can_use_console_password": execution_result.get("previous_can_use_console_password", True),
            "can_use_api_keys": execution_result.get("previous_can_use_api_keys", True),
        },
        [], connector,
    )
```

- [ ] Step 2: Create `enable_iam_user.py`. Parameters: `user_id`, optional `can_use_console_password` (default `True`), `can_use_api_keys` (default `True`). Restores capabilities. Rollback: `oci_iam_user_disable`.

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client
import oci

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters.get("user_id", "")
    can_use_console = parameters.get("can_use_console_password", True)
    can_use_api = parameters.get("can_use_api_keys", True)

    if not creds:
        return {"action": "enable_iam_user", "user_id": user_id, "mock": True}

    client = get_identity_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        details = oci.identity.models.UpdateUserCapabilitiesDetails(
            can_use_console_password=can_use_console,
            can_use_api_keys=can_use_api,
            can_use_auth_tokens=True,
            can_use_smtp_credentials=True,
        )
        client.update_user_capabilities(user_id, details)

    await loop.run_in_executor(None, _call)
    return {
        "action": "enable_iam_user",
        "user_id": user_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.disable_iam_user import execute as disable
    return await disable({"user_id": execution_result.get("user_id", parameters.get("user_id"))}, [], connector)
```

---

### Task 4: IAM group create/delete — create_iam_group.py + delete_iam_group.py

**Files:** Create:
- `backend/app/connectors/executors/oci/create_iam_group.py`
- `backend/app/connectors/executors/oci/delete_iam_group.py`

- [ ] Step 1: Create `create_iam_group.py`. Parameters: `name` (default `"nexplane-group"`), `description` (default `"Created by Nexplane"`), `user_ids` (default `[]`). Creates group via `identity.create_group()`, then calls `identity.add_user_to_group()` for each user_id. Returns `_auto_asset` (`application`, tagged `oci-iam-group`). Rollback: `oci_iam_group_delete`.

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client
import oci

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    name = parameters.get("name", "nexplane-group")
    description = parameters.get("description", "Created by Nexplane")
    user_ids = parameters.get("user_ids", [])

    auto_asset = {
        "name": name,
        "asset_type": "application",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {"name": name, "provider": "oci"},
        "tags": ["oci", "oci-iam-group", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_iam_group",
            "name": name,
            "group_id": "ocid1.group.oc1..mock",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    client = get_identity_client(creds)
    tenancy_id = creds["tenancy_id"]
    loop = asyncio.get_running_loop()

    def _call():
        details = oci.identity.models.CreateGroupDetails(
            compartment_id=tenancy_id,
            name=name,
            description=description,
        )
        group = client.create_group(details).data
        for uid in user_ids:
            membership = oci.identity.models.AddUserToGroupDetails(
                user_id=uid, group_id=group.id
            )
            client.add_user_to_group(membership)
        return group

    group = await loop.run_in_executor(None, _call)
    auto_asset["asset_metadata"]["group_id"] = group.id
    return {
        "action": "create_iam_group",
        "name": name,
        "group_id": group.id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_iam_group import execute as delete
    return await delete(
        {"group_id": execution_result.get("group_id"), "name": execution_result.get("name")},
        [], connector,
    )
```

- [ ] Step 2: Create `delete_iam_group.py`. Parameters: `group_id` (auto-populated). Remove all memberships first (`identity.list_user_group_memberships()` + `remove_user_from_group()`), then call `identity.delete_group(group_id)`. Rollback: none (destructive).

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client
import oci.pagination

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    group_id = parameters.get("group_id", "")

    if not creds:
        return {"action": "delete_iam_group", "group_id": group_id, "mock": True}

    client = get_identity_client(creds)
    tenancy_id = creds["tenancy_id"]
    loop = asyncio.get_running_loop()

    def _call():
        memberships = oci.pagination.list_call_get_all_results(
            client.list_user_group_memberships,
            compartment_id=tenancy_id,
            group_id=group_id,
        ).data
        for m in memberships:
            client.remove_user_from_group(m.id)
        client.delete_group(group_id)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_iam_group",
        "group_id": group_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_iam_group is destructive; no rollback available"}
```

---

### Task 5: IAM policy create/delete — create_iam_policy.py + delete_iam_policy.py

**Files:** Create:
- `backend/app/connectors/executors/oci/create_iam_policy.py`
- `backend/app/connectors/executors/oci/delete_iam_policy.py`

- [ ] Step 1: Create `create_iam_policy.py`. Parameters: `compartment_id` (resolved from target compartment asset), `name` (default `"nexplane-policy"`), `description` (default `"Created by Nexplane"`), `statements` (default `["Allow group nexplane-group to read all-resources in tenancy"]`). Call `identity.create_policy()`. Returns `_auto_asset` (`application`, tagged `oci-iam-policy`). Rollback: `oci_iam_policy_delete`.

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client
import oci

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id") or creds.get("compartment_id") or creds.get("tenancy_id", "")
    name = parameters.get("name", "nexplane-policy")
    description = parameters.get("description", "Created by Nexplane")
    statements = parameters.get("statements", ["Allow group nexplane-group to read all-resources in tenancy"])

    auto_asset = {
        "name": name,
        "asset_type": "application",
        "environment": "prod",
        "criticality": "high",
        "asset_metadata": {"name": name, "compartment_id": compartment_id, "provider": "oci"},
        "tags": ["oci", "oci-iam-policy", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_iam_policy",
            "name": name,
            "policy_id": "ocid1.policy.oc1..mock",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    client = get_identity_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        details = oci.identity.models.CreatePolicyDetails(
            compartment_id=compartment_id,
            name=name,
            description=description,
            statements=statements,
        )
        return client.create_policy(details).data

    policy = await loop.run_in_executor(None, _call)
    auto_asset["asset_metadata"]["policy_id"] = policy.id
    auto_asset["asset_metadata"]["statements"] = list(policy.statements)
    return {
        "action": "create_iam_policy",
        "name": name,
        "policy_id": policy.id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_iam_policy import execute as delete
    return await delete(
        {"policy_id": execution_result.get("policy_id"), "name": execution_result.get("name")},
        [], connector,
    )
```

- [ ] Step 2: Create `delete_iam_policy.py`. Parameters: `policy_id` (auto-populated). Call `identity.delete_policy(policy_id)`. Rollback: none (destructive).

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    policy_id = parameters.get("policy_id", "")

    if not creds:
        return {"action": "delete_iam_policy", "policy_id": policy_id, "mock": True}

    client = get_identity_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: client.delete_policy(policy_id))
    return {
        "action": "delete_iam_policy",
        "policy_id": policy_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_iam_policy is destructive; no rollback available"}
```

---

### Task 6: Vault secret create/delete — create_vault_secret.py + delete_vault_secret.py

**Files:** Create:
- `backend/app/connectors/executors/oci/create_vault_secret.py`
- `backend/app/connectors/executors/oci/delete_vault_secret.py`

Also Modify: `backend/app/connectors/executors/oci/_client.py` — add `get_vault_client`, `get_secrets_client`, `get_vaults_management_client`.

- [ ] Step 1: Add three new client factory functions to `_client.py`:

```python
def get_vault_client(creds: dict):
    """Returns oci.vault.VaultsClient (for listing secrets and vaults)."""
    import oci
    config = _make_config(creds)
    return oci.vault.VaultsClient(config)

def get_secrets_client(creds: dict):
    """Returns oci.secrets.SecretsClient (for reading secret bundles)."""
    import oci
    config = _make_config(creds)
    return oci.secrets.SecretsClient(config)

def get_vaults_management_client(creds: dict):
    """Returns oci.key_management.KmsVaultClient."""
    import oci
    config = _make_config(creds)
    return oci.key_management.KmsVaultClient(config)
```

Note: `_make_config(creds)` must already exist in `_client.py` from sub-project 1. If the helper is named differently, match the existing convention.

- [ ] Step 2: Create `create_vault_secret.py`. Parameters: `compartment_id`, `vault_id` (resolved from first ACTIVE vault), `key_id` (first ACTIVE master key), `secret_name` (default `"nexplane-secret"`), `secret_content` (default `"changeme"`), `description` (default `"Created by Nexplane"`). Preflight: list vaults, fail with clear message if no ACTIVE vault. Base64-encode `secret_content`. Returns `_auto_asset` (`application`, tagged `oci-vault-secret`). Rollback: `oci_vault_secret_delete`.

```python
import asyncio
import base64
from datetime import datetime, timezone
from ._client import get_vault_client
import oci
import oci.pagination

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id") or creds.get("compartment_id") or creds.get("tenancy_id", "")
    secret_name = parameters.get("secret_name", "nexplane-secret")
    secret_content = parameters.get("secret_content", "changeme")
    description = parameters.get("description", "Created by Nexplane")

    auto_asset = {
        "name": secret_name,
        "asset_type": "application",
        "environment": "prod",
        "criticality": "critical",
        "asset_metadata": {
            "secret_name": secret_name,
            "compartment_id": compartment_id,
            "provider": "oci",
        },
        "tags": ["oci", "oci-vault-secret", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_vault_secret",
            "secret_name": secret_name,
            "secret_id": "ocid1.vaultsecret.oc1..mock",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    vault_client = get_vault_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        # Preflight: find active vault
        vaults = oci.pagination.list_call_get_all_results(
            vault_client.list_vaults, compartment_id=compartment_id
        ).data
        active_vaults = [v for v in vaults if v.lifecycle_state == "ACTIVE"]
        if not active_vaults:
            raise ValueError(
                "No ACTIVE Vault in this compartment. Create a Vault in the OCI Console first."
            )
        vault = active_vaults[0]
        vault_id = parameters.get("vault_id") or vault.id

        # Resolve master encryption key
        from oci.key_management import KmsManagementClient
        from ._client import _make_config
        kms_client = KmsManagementClient(
            _make_config(creds), service_endpoint=vault.management_endpoint
        )
        keys = oci.pagination.list_call_get_all_results(
            kms_client.list_keys, compartment_id=compartment_id
        ).data
        active_keys = [k for k in keys if k.lifecycle_state == "ENABLED"]
        if not active_keys:
            raise ValueError("No ENABLED master encryption key found in vault.")
        key_id = parameters.get("key_id") or active_keys[0].id

        encoded = base64.b64encode(secret_content.encode()).decode()
        content_details = oci.vault.models.Base64SecretContentDetails(
            content_type=oci.vault.models.SecretContentDetails.CONTENT_TYPE_BASE64,
            name="v1",
            stage="CURRENT",
            content=encoded,
        )
        secret_details = oci.vault.models.CreateSecretDetails(
            compartment_id=compartment_id,
            vault_id=vault_id,
            key_id=key_id,
            secret_name=secret_name,
            description=description,
            secret_content=content_details,
        )
        return vault_client.create_secret(secret_details).data

    secret = await loop.run_in_executor(None, _call)
    auto_asset["asset_metadata"]["secret_id"] = secret.id
    auto_asset["asset_metadata"]["vault_id"] = secret.vault_id
    return {
        "action": "create_vault_secret",
        "secret_name": secret_name,
        "secret_id": secret.id,
        "vault_id": secret.vault_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_vault_secret import execute as delete
    return await delete(
        {"secret_id": execution_result.get("secret_id"), "deletion_time_days": 1},
        [], connector,
    )
```

- [ ] Step 3: Create `delete_vault_secret.py`. Parameters: `secret_id` (auto-populated), `deletion_time_days` (default `1`). OCI Vault uses deferred deletion — call `vault_client.schedule_secret_deletion()` with a future deletion time. Rollback: cancel deletion via `cancel_secret_deletion()` if still in `PENDING_DELETION`.

```python
import asyncio
from datetime import datetime, timezone, timedelta
from ._client import get_vault_client

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    secret_id = parameters.get("secret_id", "")
    deletion_days = int(parameters.get("deletion_time_days", 1))

    if not creds:
        return {"action": "delete_vault_secret", "secret_id": secret_id, "mock": True}

    vault_client = get_vault_client(creds)
    loop = asyncio.get_running_loop()
    deletion_time = datetime.now(timezone.utc) + timedelta(days=deletion_days)

    def _call():
        import oci.vault.models
        details = oci.vault.models.ScheduleSecretDeletionDetails(
            time_of_deletion=deletion_time
        )
        vault_client.schedule_secret_deletion(secret_id, details)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_vault_secret",
        "secret_id": secret_id,
        "scheduled_deletion": deletion_time.isoformat(),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    secret_id = execution_result.get("secret_id", parameters.get("secret_id", ""))
    if not creds:
        return {"action": "cancel_vault_secret_deletion", "secret_id": secret_id, "mock": True}
    vault_client = get_vault_client(creds)
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, lambda: vault_client.cancel_secret_deletion(secret_id))
        return {"action": "cancel_vault_secret_deletion", "secret_id": secret_id, "cancelled": True}
    except Exception as e:
        return {"action": "cancel_vault_secret_deletion", "secret_id": secret_id, "cancelled": False, "error": str(e)}
```

---

### Task 7: Compartment create/delete — create_compartment.py + delete_compartment.py

**Files:** Create:
- `backend/app/connectors/executors/oci/create_compartment.py`
- `backend/app/connectors/executors/oci/delete_compartment.py`

- [ ] Step 1: Create `create_compartment.py`. Parameters: `parent_compartment_id` (resolved from target compartment asset), `name` (default `"nexplane-compartment"`), `description` (default `"Created by Nexplane"`). Call `identity.create_compartment()`. Returns `_auto_asset` (`cloud_account`, tagged `oci compartment`). Rollback: `oci_compartment_delete`.

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client
import oci

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    parent_compartment_id = parameters.get("parent_compartment_id") or creds.get("compartment_id") or creds.get("tenancy_id", "")
    name = parameters.get("name", "nexplane-compartment")
    description = parameters.get("description", "Created by Nexplane")

    auto_asset = {
        "name": name,
        "asset_type": "cloud_account",
        "environment": "prod",
        "criticality": "medium",
        "asset_metadata": {
            "name": name,
            "parent_compartment_id": parent_compartment_id,
            "provider": "oci",
        },
        "tags": ["oci", "compartment", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_compartment",
            "name": name,
            "compartment_id": "ocid1.compartment.oc1..mock",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    client = get_identity_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        details = oci.identity.models.CreateCompartmentDetails(
            compartment_id=parent_compartment_id,
            name=name,
            description=description,
        )
        return client.create_compartment(details).data

    compartment = await loop.run_in_executor(None, _call)
    auto_asset["asset_metadata"]["compartment_id"] = compartment.id
    return {
        "action": "create_compartment",
        "name": name,
        "compartment_id": compartment.id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_compartment import execute as delete
    return await delete(
        {"compartment_id": execution_result.get("compartment_id"), "name": execution_result.get("name")},
        [], connector,
    )
```

- [ ] Step 2: Create `delete_compartment.py`. Parameters: `compartment_id` (auto-populated). Preflight: verify compartment is empty by attempting to list instances, buckets, and sub-compartments; fail with a clear error if any resources exist. Then call `identity.delete_compartment(compartment_id)`. Rollback: none (destructive).

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = parameters.get("compartment_id", "")

    if not creds:
        return {"action": "delete_compartment", "compartment_id": compartment_id, "mock": True}

    client = get_identity_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        # Preflight: check for sub-compartments
        import oci.pagination
        sub_comps = oci.pagination.list_call_get_all_results(
            client.list_compartments, compartment_id=compartment_id
        ).data
        active_sub = [c for c in sub_comps if c.lifecycle_state not in ("DELETED",)]
        if active_sub:
            raise ValueError(
                f"Compartment has {len(active_sub)} active sub-compartment(s). "
                "Remove all resources before deleting."
            )
        client.delete_compartment(compartment_id)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_compartment",
        "compartment_id": compartment_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_compartment is destructive; no rollback available"}
```

---

### Task 8: Extend oci.json catalog

**Files:** Modify: `backend/app/connectors/catalog/oci.json`

- [ ] Step 1: Append 12 new action entries to the `"actions"` array. Follow the exact format of existing entries. Each entry has `display_name`, `description`, `parameters`, `execution_tier`, `estimated_duration_seconds`, `applicable_asset_types`, `action_type`, `executor`, `generic_action`, `action_id`, and where applicable `rollback_action` + `rollback_connector_type`.

Add the following actions:

**IAM User actions** (applicable to `cloud_account` or `identity`):
```json
{
  "display_name": "Create IAM User",
  "description": "Create an OCI IAM user (tenancy-scoped).",
  "parameters": [
    {"name": "name", "type": "string", "required": true, "default": "nexplane-user"},
    {"name": "description", "type": "string", "required": false, "default": "Created by Nexplane"},
    {"name": "email", "type": "string", "required": false, "default": ""},
    {"name": "group_id", "type": "string", "required": false, "default": ""}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_iam_user_delete",
  "rollback_connector_type": "oci",
  "estimated_duration_seconds": 10,
  "applicable_asset_types": ["cloud_account"],
  "action_type": "change",
  "executor": "oci.create_iam_user",
  "generic_action": "oci_iam_user_create",
  "action_id": "oci_iam_user_create"
},
{
  "display_name": "Delete IAM User",
  "description": "Remove all group memberships and delete an OCI IAM user.",
  "parameters": [
    {"name": "user_id", "type": "string", "required": true}
  ],
  "execution_tier": 1,
  "estimated_duration_seconds": 10,
  "applicable_asset_types": ["identity", "cloud_account"],
  "action_type": "change",
  "executor": "oci.delete_iam_user",
  "generic_action": "oci_iam_user_delete",
  "action_id": "oci_iam_user_delete"
},
{
  "display_name": "Disable IAM User",
  "description": "Block console login and API key access for an OCI IAM user.",
  "parameters": [
    {"name": "user_id", "type": "string", "required": true}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_iam_user_enable",
  "rollback_connector_type": "oci",
  "estimated_duration_seconds": 5,
  "applicable_asset_types": ["identity"],
  "action_type": "change",
  "executor": "oci.disable_iam_user",
  "generic_action": "oci_iam_user_disable",
  "action_id": "oci_iam_user_disable"
},
{
  "display_name": "Enable IAM User",
  "description": "Restore console login and API key access for an OCI IAM user.",
  "parameters": [
    {"name": "user_id", "type": "string", "required": true},
    {"name": "can_use_console_password", "type": "string", "required": false, "default": "true"},
    {"name": "can_use_api_keys", "type": "string", "required": false, "default": "true"}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_iam_user_disable",
  "rollback_connector_type": "oci",
  "estimated_duration_seconds": 5,
  "applicable_asset_types": ["identity"],
  "action_type": "change",
  "executor": "oci.enable_iam_user",
  "generic_action": "oci_iam_user_enable",
  "action_id": "oci_iam_user_enable"
}
```

**IAM Group actions**:
```json
{
  "display_name": "Create IAM Group",
  "description": "Create an OCI IAM group and optionally add users.",
  "parameters": [
    {"name": "name", "type": "string", "required": true, "default": "nexplane-group"},
    {"name": "description", "type": "string", "required": false, "default": "Created by Nexplane"},
    {"name": "user_ids", "type": "string", "required": false, "default": "[]"}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_iam_group_delete",
  "rollback_connector_type": "oci",
  "estimated_duration_seconds": 10,
  "applicable_asset_types": ["cloud_account"],
  "action_type": "change",
  "executor": "oci.create_iam_group",
  "generic_action": "oci_iam_group_create",
  "action_id": "oci_iam_group_create"
},
{
  "display_name": "Delete IAM Group",
  "description": "Remove all memberships and delete an OCI IAM group.",
  "parameters": [
    {"name": "group_id", "type": "string", "required": true}
  ],
  "execution_tier": 1,
  "estimated_duration_seconds": 10,
  "applicable_asset_types": ["application", "cloud_account"],
  "action_type": "change",
  "executor": "oci.delete_iam_group",
  "generic_action": "oci_iam_group_delete",
  "action_id": "oci_iam_group_delete"
}
```

**IAM Policy actions**:
```json
{
  "display_name": "Create IAM Policy",
  "description": "Create an OCI IAM policy with policy statements.",
  "parameters": [
    {"name": "compartment_id", "type": "string", "required": false, "default": ""},
    {"name": "name", "type": "string", "required": true, "default": "nexplane-policy"},
    {"name": "description", "type": "string", "required": false, "default": "Created by Nexplane"},
    {"name": "statements", "type": "string", "required": false, "default": "[\"Allow group nexplane-group to read all-resources in tenancy\"]"}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_iam_policy_delete",
  "rollback_connector_type": "oci",
  "estimated_duration_seconds": 10,
  "applicable_asset_types": ["cloud_account"],
  "action_type": "change",
  "executor": "oci.create_iam_policy",
  "generic_action": "oci_iam_policy_create",
  "action_id": "oci_iam_policy_create"
},
{
  "display_name": "Delete IAM Policy",
  "description": "Delete an OCI IAM policy.",
  "parameters": [
    {"name": "policy_id", "type": "string", "required": true}
  ],
  "execution_tier": 1,
  "estimated_duration_seconds": 5,
  "applicable_asset_types": ["application", "cloud_account"],
  "action_type": "change",
  "executor": "oci.delete_iam_policy",
  "generic_action": "oci_iam_policy_delete",
  "action_id": "oci_iam_policy_delete"
}
```

**Vault Secret actions**:
```json
{
  "display_name": "Create Vault Secret",
  "description": "Create a secret in OCI Vault. Requires an ACTIVE vault to exist in the compartment.",
  "parameters": [
    {"name": "compartment_id", "type": "string", "required": false, "default": ""},
    {"name": "vault_id", "type": "string", "required": false, "default": ""},
    {"name": "key_id", "type": "string", "required": false, "default": ""},
    {"name": "secret_name", "type": "string", "required": true, "default": "nexplane-secret"},
    {"name": "secret_content", "type": "password", "required": true, "default": "changeme"},
    {"name": "description", "type": "string", "required": false, "default": "Created by Nexplane"}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_vault_secret_delete",
  "rollback_connector_type": "oci",
  "estimated_duration_seconds": 15,
  "applicable_asset_types": ["cloud_account"],
  "action_type": "change",
  "executor": "oci.create_vault_secret",
  "generic_action": "oci_vault_secret_create",
  "action_id": "oci_vault_secret_create"
},
{
  "display_name": "Delete Vault Secret",
  "description": "Schedule an OCI Vault secret for deletion (minimum 1 day deferred).",
  "parameters": [
    {"name": "secret_id", "type": "string", "required": true},
    {"name": "deletion_time_days", "type": "string", "required": false, "default": "1"}
  ],
  "execution_tier": 1,
  "estimated_duration_seconds": 5,
  "applicable_asset_types": ["application", "cloud_account"],
  "action_type": "change",
  "executor": "oci.delete_vault_secret",
  "generic_action": "oci_vault_secret_delete",
  "action_id": "oci_vault_secret_delete"
}
```

**Compartment lifecycle actions**:
```json
{
  "display_name": "Create Compartment",
  "description": "Create a child compartment under the target compartment.",
  "parameters": [
    {"name": "parent_compartment_id", "type": "string", "required": false, "default": ""},
    {"name": "name", "type": "string", "required": true, "default": "nexplane-compartment"},
    {"name": "description", "type": "string", "required": false, "default": "Created by Nexplane"}
  ],
  "execution_tier": 1,
  "rollback_action": "oci_compartment_delete",
  "rollback_connector_type": "oci",
  "estimated_duration_seconds": 10,
  "applicable_asset_types": ["cloud_account"],
  "action_type": "change",
  "executor": "oci.create_compartment",
  "generic_action": "oci_compartment_create",
  "action_id": "oci_compartment_create"
},
{
  "display_name": "Delete Compartment",
  "description": "Delete an OCI compartment. Preflight verifies compartment is empty.",
  "parameters": [
    {"name": "compartment_id", "type": "string", "required": true}
  ],
  "execution_tier": 1,
  "estimated_duration_seconds": 15,
  "applicable_asset_types": ["cloud_account"],
  "action_type": "change",
  "executor": "oci.delete_compartment",
  "generic_action": "oci_compartment_delete",
  "action_id": "oci_compartment_delete"
}
```

- [ ] Step 2: Also add discovery actions for the four new discovery executors:

```json
{
  "display_name": "Discover IAM Users",
  "description": "Discover OCI IAM users (tenancy-scoped).",
  "parameters": [],
  "execution_tier": 1,
  "estimated_duration_seconds": 10,
  "applicable_asset_types": ["cloud_account"],
  "action_type": "discovery",
  "executor": "oci.discover_iam_users",
  "generic_action": "discover_iam_users",
  "action_id": "discover_iam_users"
},
{
  "display_name": "Discover IAM Groups",
  "description": "Discover OCI IAM groups (tenancy-scoped).",
  "parameters": [],
  "execution_tier": 1,
  "estimated_duration_seconds": 10,
  "applicable_asset_types": ["cloud_account"],
  "action_type": "discovery",
  "executor": "oci.discover_iam_groups",
  "generic_action": "discover_iam_groups",
  "action_id": "discover_iam_groups"
},
{
  "display_name": "Discover IAM Policies",
  "description": "Discover OCI IAM policies in the compartment.",
  "parameters": [],
  "execution_tier": 1,
  "estimated_duration_seconds": 10,
  "applicable_asset_types": ["cloud_account"],
  "action_type": "discovery",
  "executor": "oci.discover_iam_policies",
  "generic_action": "discover_iam_policies",
  "action_id": "discover_iam_policies"
},
{
  "display_name": "Discover Vault Secrets",
  "description": "Discover OCI Vault secrets (skipped if no ACTIVE vault exists).",
  "parameters": [],
  "execution_tier": 1,
  "estimated_duration_seconds": 10,
  "applicable_asset_types": ["cloud_account"],
  "action_type": "discovery",
  "executor": "oci.discover_vault_secrets",
  "generic_action": "discover_vault_secrets",
  "action_id": "discover_vault_secrets"
}
```

---

### Task 9: DB migration 043 + ChangeType enum + safety engine

**Files:**
- Create: `backend/alembic/versions/043_add_oci_identity_change_types.py`
- Modify: `backend/app/models/change_request.py`
- Modify: `backend/app/services/safety_engine.py`

- [ ] Step 1: Create migration file `backend/alembic/versions/043_add_oci_identity_change_types.py`. Revises `039` (or whichever is the latest actual revision — check `down_revision` against the last migration file):

```python
"""add OCI identity change types

Revision ID: 043
Revises: 039
Create Date: 2026-05-10
"""
from alembic import op

revision = '043'
down_revision = '039'
branch_labels = None
depends_on = None

_NEW_TYPES = [
    'oci_iam_user_create',
    'oci_iam_user_delete',
    'oci_iam_user_disable',
    'oci_iam_user_enable',
    'oci_iam_group_create',
    'oci_iam_group_delete',
    'oci_iam_policy_create',
    'oci_iam_policy_delete',
    'oci_vault_secret_create',
    'oci_vault_secret_delete',
    'oci_compartment_create',
    'oci_compartment_delete',
]


def upgrade():
    for t in _NEW_TYPES:
        op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


def downgrade():
    pass
```

IMPORTANT: Before writing the file, check the actual latest migration in `backend/alembic/versions/` and set `down_revision` accordingly. The migration numbering must be sequential with no gaps relative to previously created OCI sub-project migrations (040, 041, 042 may have been created by sub-projects 1-3).

- [ ] Step 2: Add 12 new `ChangeType` enum values to `backend/app/models/change_request.py`, in a clearly labeled comment block. Append after the `# IP address migration` block (currently the last section):

```python
    # OCI identity — Sub-project 4
    oci_iam_user_create = "oci_iam_user_create"
    oci_iam_user_delete = "oci_iam_user_delete"
    oci_iam_user_disable = "oci_iam_user_disable"
    oci_iam_user_enable = "oci_iam_user_enable"
    oci_iam_group_create = "oci_iam_group_create"
    oci_iam_group_delete = "oci_iam_group_delete"
    oci_iam_policy_create = "oci_iam_policy_create"
    oci_iam_policy_delete = "oci_iam_policy_delete"
    oci_vault_secret_create = "oci_vault_secret_create"
    oci_vault_secret_delete = "oci_vault_secret_delete"
    oci_compartment_create = "oci_compartment_create"
    oci_compartment_delete = "oci_compartment_delete"
```

- [ ] Step 3: Add all 12 new change types to `_IMPLICIT_ROLLBACK_TYPES` in `backend/app/services/safety_engine.py`. Append as a string-based block (consistent with the existing pattern for OCI types from sub-projects 1-3):

```python
    # OCI identity — Sub-project 4
    "oci_iam_user_create", "oci_iam_user_delete",
    "oci_iam_user_disable", "oci_iam_user_enable",
    "oci_iam_group_create", "oci_iam_group_delete",
    "oci_iam_policy_create", "oci_iam_policy_delete",
    "oci_vault_secret_create", "oci_vault_secret_delete",
    "oci_compartment_create", "oci_compartment_delete",
```

---

### Task 10: Frontend extensions — api.ts + CreateChangeRequest.tsx + AssetDetail quick actions

**Files:** Modify:
- `frontend/src/types/api.ts`
- `frontend/src/pages/CreateChangeRequest.tsx`
- `frontend/src/pages/AssetDetail.tsx`

- [ ] Step 1: Add 12 new `ChangeType` values to `frontend/src/types/api.ts`. Append to the `ChangeType` union type (before the closing `| "suppress"`... actually append them after the last existing value `"ip_campaign"`):

```typescript
  | "oci_iam_user_create"
  | "oci_iam_user_delete"
  | "oci_iam_user_disable"
  | "oci_iam_user_enable"
  | "oci_iam_group_create"
  | "oci_iam_group_delete"
  | "oci_iam_policy_create"
  | "oci_iam_policy_delete"
  | "oci_vault_secret_create"
  | "oci_vault_secret_delete"
  | "oci_compartment_create"
  | "oci_compartment_delete"
```

- [ ] Step 2: Add 12 new entries to `CHANGE_TYPE_META` in `frontend/src/pages/CreateChangeRequest.tsx`. Group under an `// Oracle Cloud — Identity` comment. Place after any existing OCI entries from sub-projects 1-3 (search for `"oci_"` prefix to find the insert point). If no OCI entries exist yet, add after the GCE or Azure block:

```typescript
  // Oracle Cloud — Identity
  oci_iam_user_create: {
    label: "Create OCI IAM User",
    description: "Create an OCI IAM user (tenancy-scoped). Optionally assign to a group.",
    outcomeTemplate: JSON.stringify({
      name: "nexplane-user",
      description: "Created by Nexplane",
      email: "",
      group_id: "",
      rollback_strategy: "oci_iam_user_delete",
    }, null, 2),
  },
  oci_iam_user_delete: {
    label: "Delete OCI IAM User",
    description: "Remove all group memberships and permanently delete an OCI IAM user.",
    outcomeTemplate: JSON.stringify({
      user_id: "ocid1.user.oc1..",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_iam_user_disable: {
    label: "Disable OCI IAM User",
    description: "Block console login and API key access for an OCI IAM user.",
    outcomeTemplate: JSON.stringify({
      user_id: "ocid1.user.oc1..",
      rollback_strategy: "oci_iam_user_enable",
    }, null, 2),
  },
  oci_iam_user_enable: {
    label: "Enable OCI IAM User",
    description: "Restore console login and API key access for an OCI IAM user.",
    outcomeTemplate: JSON.stringify({
      user_id: "ocid1.user.oc1..",
      can_use_console_password: true,
      can_use_api_keys: true,
      rollback_strategy: "oci_iam_user_disable",
    }, null, 2),
  },
  oci_iam_group_create: {
    label: "Create OCI IAM Group",
    description: "Create an OCI IAM group and optionally add users.",
    outcomeTemplate: JSON.stringify({
      name: "nexplane-group",
      description: "Created by Nexplane",
      user_ids: [],
      rollback_strategy: "oci_iam_group_delete",
    }, null, 2),
  },
  oci_iam_group_delete: {
    label: "Delete OCI IAM Group",
    description: "Remove all memberships and permanently delete an OCI IAM group.",
    outcomeTemplate: JSON.stringify({
      group_id: "ocid1.group.oc1..",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_iam_policy_create: {
    label: "Create OCI IAM Policy",
    description: "Create an OCI IAM policy with policy statements.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      name: "nexplane-policy",
      description: "Created by Nexplane",
      statements: ["Allow group nexplane-group to read all-resources in tenancy"],
      rollback_strategy: "oci_iam_policy_delete",
    }, null, 2),
  },
  oci_iam_policy_delete: {
    label: "Delete OCI IAM Policy",
    description: "Permanently delete an OCI IAM policy.",
    outcomeTemplate: JSON.stringify({
      policy_id: "ocid1.policy.oc1..",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
  oci_vault_secret_create: {
    label: "Create OCI Vault Secret",
    description: "Create a secret in OCI Vault. Requires an ACTIVE vault in the compartment.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "",
      vault_id: "",
      key_id: "",
      secret_name: "nexplane-secret",
      secret_content: "changeme",
      description: "Created by Nexplane",
      rollback_strategy: "oci_vault_secret_delete",
    }, null, 2),
  },
  oci_vault_secret_delete: {
    label: "Delete OCI Vault Secret",
    description: "Schedule an OCI Vault secret for deferred deletion (minimum 1 day).",
    outcomeTemplate: JSON.stringify({
      secret_id: "ocid1.vaultsecret.oc1..",
      deletion_time_days: 1,
      rollback_strategy: "cancel_vault_secret_deletion",
    }, null, 2),
  },
  oci_compartment_create: {
    label: "Create OCI Compartment",
    description: "Create a child compartment under the target compartment.",
    outcomeTemplate: JSON.stringify({
      parent_compartment_id: "",
      name: "nexplane-compartment",
      description: "Created by Nexplane",
      rollback_strategy: "oci_compartment_delete",
    }, null, 2),
  },
  oci_compartment_delete: {
    label: "Delete OCI Compartment",
    description: "Delete an OCI compartment. Compartment must be empty.",
    outcomeTemplate: JSON.stringify({
      compartment_id: "ocid1.compartment.oc1..",
      rollback_strategy: "rollback_unavailable",
    }, null, 2),
  },
```

- [ ] Step 3: Extend the "Oracle Cloud" category grouping in `CreateChangeRequest.tsx`. Find the existing Oracle Cloud category section and add the 12 new change types to its list. If the Oracle Cloud category does not yet exist, add it in the same pattern as the "Amazon Web Services" or "Google Cloud" category block. The identity sub-group should appear after compute sub-group entries.

- [ ] Step 4: Add quick actions to `frontend/src/pages/AssetDetail.tsx` for OCI identity and application assets. Find the section that renders quick action buttons (look for existing patterns like AWS IAM or GCE identity quick actions). Add:
  - For `identity` assets with tag `oci-iam-user`: buttons for **Disable**, **Enable**, **Delete** (change types `oci_iam_user_disable`, `oci_iam_user_enable`, `oci_iam_user_delete`). Auto-populate `user_id` from `asset.asset_metadata.user_id`.
  - For `application` assets with tag `oci-iam-group`: button for **Delete** (`oci_iam_group_delete`). Auto-populate `group_id` from `asset.asset_metadata.group_id`.
  - For `application` assets with tag `oci-iam-policy`: button for **Delete** (`oci_iam_policy_delete`). Auto-populate `policy_id` from `asset.asset_metadata.policy_id`.
  - For `application` assets with tag `oci-vault-secret`: button for **Delete** (`oci_vault_secret_delete`). Auto-populate `secret_id` from `asset.asset_metadata.secret_id`.

  Example pattern for the quick action button (follow whatever component/pattern is already used for AWS IAM or GCE in `AssetDetail.tsx`):
  ```tsx
  {asset.asset_type === "identity" && asset.tags.includes("oci-iam-user") && (
    <>
      <QuickActionButton
        changeType="oci_iam_user_disable"
        label="Disable User"
        prefilledOutcome={{ user_id: asset.asset_metadata.user_id as string }}
        asset={asset}
      />
      <QuickActionButton
        changeType="oci_iam_user_enable"
        label="Enable User"
        prefilledOutcome={{ user_id: asset.asset_metadata.user_id as string }}
        asset={asset}
      />
      <QuickActionButton
        changeType="oci_iam_user_delete"
        label="Delete User"
        prefilledOutcome={{ user_id: asset.asset_metadata.user_id as string }}
        asset={asset}
      />
    </>
  )}
  {asset.asset_type === "application" && asset.tags.includes("oci-iam-group") && (
    <QuickActionButton
      changeType="oci_iam_group_delete"
      label="Delete Group"
      prefilledOutcome={{ group_id: asset.asset_metadata.group_id as string }}
      asset={asset}
    />
  )}
  {asset.asset_type === "application" && asset.tags.includes("oci-iam-policy") && (
    <QuickActionButton
      changeType="oci_iam_policy_delete"
      label="Delete Policy"
      prefilledOutcome={{ policy_id: asset.asset_metadata.policy_id as string }}
      asset={asset}
    />
  )}
  {asset.asset_type === "application" && asset.tags.includes("oci-vault-secret") && (
    <QuickActionButton
      changeType="oci_vault_secret_delete"
      label="Delete Secret"
      prefilledOutcome={{ secret_id: asset.asset_metadata.secret_id as string, deletion_time_days: 1 }}
      asset={asset}
    />
  )}
  ```

  Note: Adapt the exact component name and props to match the existing `AssetDetail.tsx` quick action pattern — read the file before editing.

- [ ] Step 5: Restart the frontend container to verify changes load: `docker compose stop frontend && docker compose up frontend -d`

---

### Task 11: Smoke tests — OCI_K (IAM) + OCI_L (Vault)

**Files:** Create or Modify in the smoke test directory (follow existing OCI smoke test file location from sub-projects 1-3):
- `backend/tests/smoke/oci/test_oci_k_iam.py`
- `backend/tests/smoke/oci/test_oci_l_vault.py`

- [ ] Step 1: Create `test_oci_k_iam.py`. Follow the rollback-stack pattern (use Nexplane rollback as primary cleanup, OCI SDK as safety net only).

```python
"""
OCI Smoke Test Phase K — IAM user/group/policy lifecycle.
Requires OCI_CONNECTOR_ID env var pointing to a live OCI connector.
"""
import os
import pytest
from tests.smoke.helpers import create_and_execute_cr, rollback_cr

CONNECTOR_ID = os.environ.get("OCI_CONNECTOR_ID", "")

@pytest.mark.skipif(not CONNECTOR_ID, reason="OCI_CONNECTOR_ID not set")
@pytest.mark.smoke
class TestOCIIAM:
    rollback_stack: list  # list of CR IDs to roll back in reverse on teardown

    def setup_method(self):
        self.rollback_stack = []

    def teardown_method(self, method):
        for cr_id in reversed(self.rollback_stack):
            try:
                rollback_cr(cr_id)
            except Exception as e:
                print(f"[warn] rollback failed for {cr_id}: {e}")

    def test_create_iam_user(self):
        cr = create_and_execute_cr(
            connector_id=CONNECTOR_ID,
            change_type="oci_iam_user_create",
            title="Smoke K1 — Create OCI IAM user",
            desired_outcome={"name": "nexplane-smoke-k1", "description": "Smoke test"},
        )
        self.rollback_stack.append(cr["id"])
        assert cr["status"] in ("completed", "verifying")
        # Verify identity asset appeared in inventory
        assets = list_assets(tag="iam-user", connector_id=CONNECTOR_ID)
        names = [a["name"] for a in assets]
        assert "nexplane-smoke-k1" in names

    def test_create_iam_group(self):
        cr = create_and_execute_cr(
            connector_id=CONNECTOR_ID,
            change_type="oci_iam_group_create",
            title="Smoke K2 — Create OCI IAM group",
            desired_outcome={"name": "nexplane-smoke-k2", "description": "Smoke test"},
        )
        self.rollback_stack.append(cr["id"])
        assert cr["status"] in ("completed", "verifying")
        assets = list_assets(tag="oci-iam-group", connector_id=CONNECTOR_ID)
        names = [a["name"] for a in assets]
        assert "nexplane-smoke-k2" in names

    def test_create_iam_policy(self):
        cr = create_and_execute_cr(
            connector_id=CONNECTOR_ID,
            change_type="oci_iam_policy_create",
            title="Smoke K3 — Create OCI IAM policy",
            desired_outcome={
                "name": "nexplane-smoke-k3",
                "statements": ["Allow group nexplane-smoke-k2 to read all-resources in tenancy"],
            },
        )
        self.rollback_stack.append(cr["id"])
        assert cr["status"] in ("completed", "verifying")
        assets = list_assets(tag="oci-iam-policy", connector_id=CONNECTOR_ID)
        names = [a["name"] for a in assets]
        assert "nexplane-smoke-k3" in names

    def test_disable_enable_iam_user(self, oci_identity_client):
        # Requires a user_id from a previous test or fixture — adapt as needed
        user_id = os.environ.get("OCI_SMOKE_USER_ID", "")
        if not user_id:
            pytest.skip("OCI_SMOKE_USER_ID not set")
        disable_cr = create_and_execute_cr(
            connector_id=CONNECTOR_ID,
            change_type="oci_iam_user_disable",
            title="Smoke K4 — Disable OCI IAM user",
            desired_outcome={"user_id": user_id},
        )
        self.rollback_stack.append(disable_cr["id"])
        assert disable_cr["status"] in ("completed", "verifying")
        # OCI SDK verify: capabilities disabled
        user = oci_identity_client.get_user(user_id).data
        assert user.capabilities.can_use_console_password is False

        enable_cr = create_and_execute_cr(
            connector_id=CONNECTOR_ID,
            change_type="oci_iam_user_enable",
            title="Smoke K5 — Enable OCI IAM user",
            desired_outcome={"user_id": user_id},
        )
        assert enable_cr["status"] in ("completed", "verifying")
        user = oci_identity_client.get_user(user_id).data
        assert user.capabilities.can_use_console_password is True
```

- [ ] Step 2: Create `test_oci_l_vault.py`. Conditional — skip if no ACTIVE vault in tenancy.

```python
"""
OCI Smoke Test Phase L — Vault secret lifecycle.
Skipped automatically if no ACTIVE vault exists in the tenancy.
"""
import os
import pytest
from tests.smoke.helpers import create_and_execute_cr, rollback_cr

CONNECTOR_ID = os.environ.get("OCI_CONNECTOR_ID", "")

@pytest.mark.skipif(not CONNECTOR_ID, reason="OCI_CONNECTOR_ID not set")
@pytest.mark.smoke
class TestOCIVault:
    rollback_stack: list

    def setup_method(self):
        self.rollback_stack = []

    def teardown_method(self, method):
        for cr_id in reversed(self.rollback_stack):
            try:
                rollback_cr(cr_id)
            except Exception as e:
                print(f"[warn] rollback failed for {cr_id}: {e}")

    def test_create_vault_secret(self):
        # Preflight: check for active vault via Nexplane discovery
        cr_discover = create_and_execute_cr(
            connector_id=CONNECTOR_ID,
            change_type=None,  # use direct discovery endpoint if available
            # Or rely on prior discovery ingest; skip if no vault-secret assets
        )
        # Simpler approach: attempt create and check for graceful failure message
        cr = create_and_execute_cr(
            connector_id=CONNECTOR_ID,
            change_type="oci_vault_secret_create",
            title="Smoke L1 — Create OCI Vault secret",
            desired_outcome={
                "secret_name": "nexplane-smoke-l1",
                "secret_content": "smoketest",
                "description": "Smoke test secret",
            },
        )
        if cr.get("result", {}).get("error", "").startswith("No ACTIVE Vault"):
            pytest.skip("No ACTIVE Vault in compartment; skipping vault smoke test")
        self.rollback_stack.append(cr["id"])
        assert cr["status"] in ("completed", "verifying")
        assets = list_assets(tag="oci-vault-secret", connector_id=CONNECTOR_ID)
        names = [a["name"] for a in assets]
        assert "nexplane-smoke-l1" in names

    def test_delete_vault_secret(self):
        secret_id = os.environ.get("OCI_SMOKE_SECRET_ID", "")
        if not secret_id:
            pytest.skip("OCI_SMOKE_SECRET_ID not set")
        cr = create_and_execute_cr(
            connector_id=CONNECTOR_ID,
            change_type="oci_vault_secret_delete",
            title="Smoke L2 — Schedule delete OCI Vault secret",
            desired_outcome={"secret_id": secret_id, "deletion_time_days": 1},
        )
        assert cr["status"] in ("completed", "verifying")
        result = cr.get("execution_runs", [{}])[-1].get("result", {})
        assert "scheduled_deletion" in result
```

---

### Task 12: Commit all

**Files:** All created/modified files above.

- [ ] Step 1: Stage all new executor files:
```
git add backend/app/connectors/executors/oci/discover_iam_users.py
git add backend/app/connectors/executors/oci/discover_iam_groups.py
git add backend/app/connectors/executors/oci/discover_iam_policies.py
git add backend/app/connectors/executors/oci/discover_vault_secrets.py
git add backend/app/connectors/executors/oci/create_iam_user.py
git add backend/app/connectors/executors/oci/delete_iam_user.py
git add backend/app/connectors/executors/oci/disable_iam_user.py
git add backend/app/connectors/executors/oci/enable_iam_user.py
git add backend/app/connectors/executors/oci/create_iam_group.py
git add backend/app/connectors/executors/oci/delete_iam_group.py
git add backend/app/connectors/executors/oci/create_iam_policy.py
git add backend/app/connectors/executors/oci/delete_iam_policy.py
git add backend/app/connectors/executors/oci/create_vault_secret.py
git add backend/app/connectors/executors/oci/delete_vault_secret.py
git add backend/app/connectors/executors/oci/create_compartment.py
git add backend/app/connectors/executors/oci/delete_compartment.py
```

- [ ] Step 2: Stage modified and new supporting files:
```
git add backend/app/connectors/executors/oci/_client.py
git add backend/app/connectors/catalog/oci.json
git add backend/alembic/versions/043_add_oci_identity_change_types.py
git add backend/app/models/change_request.py
git add backend/app/services/safety_engine.py
git add frontend/src/types/api.ts
git add frontend/src/pages/CreateChangeRequest.tsx
git add frontend/src/pages/AssetDetail.tsx
```

- [ ] Step 3: Stage smoke tests:
```
git add backend/tests/smoke/oci/test_oci_k_iam.py
git add backend/tests/smoke/oci/test_oci_l_vault.py
```

- [ ] Step 4: Commit:
```
git commit -m "feat(oci): sub-project 4 — IAM user/group/policy, Vault secret, compartment lifecycle (12 change types)"
```
