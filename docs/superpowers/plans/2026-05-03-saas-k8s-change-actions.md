# SaaS & Kubernetes Change Actions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add write/change action executors to five connectors — Google Workspace, GitHub, Slack, Microsoft 365 / Entra ID, and Kubernetes — following the existing `execute(parameters, asset_ids, connector) -> dict` module pattern and registering each new action in the connector's `catalog/*.json` file.

**Architecture:** Each new action is a Python module in `backend/app/connectors/executors/<connector>/`. It exports `async def execute(...)` and `async def rollback(...)`. The catalog service resolves `"executor": "connector.module_name"` to `app.connectors.executors.connector.module_name` via `importlib.import_module`. When `connector.credentials` is empty/falsy the executor returns a mock response so unit tests can run without real credentials. Real API calls are guarded by a `creds` check and lazy-imported inside `execute()`.

**Tech Stack:** Python 3.12, FastAPI/asyncio, `google-api-python-client`, `google-auth`, `PyGithub`, `slack-sdk`, `msal`, `httpx`, `kubernetes` Python client, `subprocess` for `helm` CLI. All packages except `slack-sdk` and `google-auth-httplib2` are already present in `requirements.txt`.

---

## Codebase State — What Already Exists

Before writing code, confirm what is already implemented so tasks do not duplicate work:

| Connector | Already-present executor modules | Missing from spec |
|-----------|----------------------------------|-------------------|
| `google_workspace` | `suspend_user`, `unsuspend_user`, `revoke_tokens`, `remove_from_group`, `wipe_device`, `block_device`, `reset_password` | `remove_from_groups` (multi-group bulk), `reset_2fa`, `revoke_oauth_tokens` (enumerated), `wipe_mobile_device` (user-scoped with resource_id param) |
| `github` | `suspend_org_member`, `enable_branch_protection`, `revoke_oauth_token`, `dismiss_secret_alert`, `enable_secret_scanning`, `enable_dependabot` | `remove_org_member`, `revoke_user_pats`, `enforce_branch_protection` (full ruleset), `archive_repo`, `disable_actions`, `enable_actions` |
| `slack` | — (no slack executor directory or catalog.json) | `deactivate_user`, `reactivate_user`, catalog.json |
| `entra_id` | `disable_user`, `enable_user`, `reset_mfa`, `revoke_sessions`, `remove_from_role`, `block_sign_in` | `remove_from_teams`, `assign_license`, `remove_license` |
| `kubernetes` | `delete_pod`, `cordon_node`, `uncordon_node`, `drain_node`, `create_network_policy`, `delete_network_policy`, `patch_deployment` | `restart_deployment`, `scale_deployment`, `apply_network_policy` (upsert), `update_rbac`, `rotate_secret`, `helm_upgrade`, `helm_rollback` |

---

## File Map

| File | Action | Notes |
|------|--------|-------|
| `backend/requirements.txt` | Modify | Add `slack-sdk>=3.27.0`, `google-auth-httplib2>=0.2.0` |
| `backend/Dockerfile` | Modify | Add helm CLI install |
| **Google Workspace** | | |
| `backend/app/connectors/executors/google_workspace/remove_from_groups.py` | Create | Bulk multi-group removal |
| `backend/app/connectors/executors/google_workspace/reset_2fa.py` | Create | `twoStepVerification/turnOff` |
| `backend/app/connectors/executors/google_workspace/revoke_oauth_tokens.py` | Create | Enumerate + delete all tokens |
| `backend/app/connectors/executors/google_workspace/wipe_mobile_device.py` | Create | User-scoped wipe, optional resource_id |
| `backend/app/connectors/catalog/google_workspace.json` | Modify | Add 4 new action entries |
| **GitHub** | | |
| `backend/app/connectors/executors/github/remove_org_member.py` | Create | `org.remove_from_members` |
| `backend/app/connectors/executors/github/revoke_user_pats.py` | Create | Fine-grained PAT revocation |
| `backend/app/connectors/executors/github/enforce_branch_protection.py` | Create | Full ruleset PUT |
| `backend/app/connectors/executors/github/archive_repo.py` | Create | PATCH archived=true |
| `backend/app/connectors/executors/github/disable_actions.py` | Create | PUT actions/permissions enabled=false |
| `backend/app/connectors/executors/github/enable_actions.py` | Create | PUT actions/permissions enabled=true |
| `backend/app/connectors/catalog/github.json` | Modify | Add 6 new action entries |
| **Slack** | | |
| `backend/app/connectors/executors/slack/__init__.py` | Create | Empty |
| `backend/app/connectors/executors/slack/_client.py` | Create | `get_slack_client(creds)` |
| `backend/app/connectors/executors/slack/deactivate_user.py` | Create | `admin.users.setInactive` |
| `backend/app/connectors/executors/slack/reactivate_user.py` | Create | `admin.users.setActive` |
| `backend/app/connectors/catalog/slack.json` | Create | New connector catalog |
| **Microsoft 365 / Entra ID** | | |
| `backend/app/connectors/executors/entra_id/remove_from_teams.py` | Create | `joinedTeams` → DELETE members |
| `backend/app/connectors/executors/entra_id/assign_license.py` | Create | `assignLicense` |
| `backend/app/connectors/executors/entra_id/remove_license.py` | Create | `assignLicense` with removeLicenses |
| `backend/app/connectors/catalog/entra_id.json` | Modify | Add 3 new action entries |
| **Kubernetes** | | |
| `backend/app/connectors/executors/kubernetes/restart_deployment.py` | Create | Patch `restartedAt` annotation |
| `backend/app/connectors/executors/kubernetes/scale_deployment.py` | Create | `patch_namespaced_deployment_scale` |
| `backend/app/connectors/executors/kubernetes/apply_network_policy.py` | Create | Create-or-patch upsert |
| `backend/app/connectors/executors/kubernetes/update_rbac.py` | Create | RoleBinding / ClusterRoleBinding upsert |
| `backend/app/connectors/executors/kubernetes/rotate_secret.py` | Create | Patch secret + restart dependents |
| `backend/app/connectors/executors/kubernetes/helm_upgrade.py` | Create | `subprocess.run helm upgrade` |
| `backend/app/connectors/executors/kubernetes/helm_rollback.py` | Create | `subprocess.run helm rollback` |
| `backend/app/connectors/catalog/kubernetes.json` | Modify | Add 7 new action entries |
| **Tests** | | |
| `backend/app/tests/test_google_workspace_changes.py` | Create | Mock-mode tests for 4 new executors |
| `backend/app/tests/test_github_changes.py` | Create | Mock-mode tests for 6 new executors |
| `backend/app/tests/test_slack_changes.py` | Create | Mock-mode tests for 2 new executors |
| `backend/app/tests/test_m365_changes.py` | Create | Mock-mode tests for 3 new executors |
| `backend/app/tests/test_kubernetes_changes.py` | Create | Mock-mode tests for 7 new executors |

---

## Task 0: Add missing Python dependencies

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/Dockerfile`

- [ ] **Step 1: Add `slack-sdk` and `google-auth-httplib2` to requirements.txt**

In `backend/requirements.txt`, add after the `google-auth>=2.27.0` line:

```
google-auth-httplib2>=0.2.0
slack-sdk>=3.27.0
```

- [ ] **Step 2: Verify no version conflicts**

```bash
docker compose run --rm backend pip install -r requirements.txt --dry-run 2>&1 | tail -20
```

Expected: no version conflicts. All packages resolve.

- [ ] **Step 3: Add helm CLI to backend Dockerfile**

In `backend/Dockerfile`, add after the `pip install` step and before the final `CMD`:

```dockerfile
RUN curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

- [ ] **Step 4: Rebuild and verify helm available**

```bash
docker compose build backend && docker compose run --rm backend helm version
```

Expected output contains: `version.BuildInfo{Version:"v3.`

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt backend/Dockerfile
git commit -m "chore(deps): add slack-sdk, google-auth-httplib2; install helm CLI in backend container"
```

---

## Task 1: Google Workspace — 4 new change action executors

**Files:**
- Create: `backend/app/connectors/executors/google_workspace/remove_from_groups.py`
- Create: `backend/app/connectors/executors/google_workspace/reset_2fa.py`
- Create: `backend/app/connectors/executors/google_workspace/revoke_oauth_tokens.py`
- Create: `backend/app/connectors/executors/google_workspace/wipe_mobile_device.py`
- Modify: `backend/app/connectors/catalog/google_workspace.json`
- Create: `backend/app/tests/test_google_workspace_changes.py`

### Step 1: Write failing tests first

Create `backend/app/tests/test_google_workspace_changes.py`:

```python
import pytest

MockConnector = type("Connector", (), {"credentials": {}})


@pytest.mark.asyncio
async def test_remove_from_groups_mock():
    from app.connectors.executors.google_workspace.remove_from_groups import execute
    result = await execute({"user_email": "alice@example.com"}, [], MockConnector())
    assert result["action"] == "remove_from_groups"
    assert result["user_email"] == "alice@example.com"
    assert isinstance(result["groups_removed"], list)


@pytest.mark.asyncio
async def test_reset_2fa_mock():
    from app.connectors.executors.google_workspace.reset_2fa import execute
    result = await execute({"user_email": "alice@example.com"}, [], MockConnector())
    assert result["action"] == "reset_2fa"
    assert result["user_email"] == "alice@example.com"
    assert result["reset"] is True


@pytest.mark.asyncio
async def test_revoke_oauth_tokens_mock():
    from app.connectors.executors.google_workspace.revoke_oauth_tokens import execute
    result = await execute({"user_email": "alice@example.com"}, [], MockConnector())
    assert result["action"] == "revoke_oauth_tokens"
    assert isinstance(result["tokens_revoked"], list)


@pytest.mark.asyncio
async def test_wipe_mobile_device_mock():
    from app.connectors.executors.google_workspace.wipe_mobile_device import execute
    result = await execute(
        {"user_email": "alice@example.com", "resource_id": "device123", "confirm_wipe": True},
        [],
        MockConnector(),
    )
    assert result["action"] == "wipe_mobile_device"
    assert result["wiped"] is True


@pytest.mark.asyncio
async def test_wipe_mobile_device_requires_confirm():
    from app.connectors.executors.google_workspace.wipe_mobile_device import execute
    result = await execute(
        {"user_email": "alice@example.com", "confirm_wipe": False},
        [],
        MockConnector(),
    )
    assert result.get("error") or result.get("skipped"), "should refuse without confirm_wipe"


@pytest.mark.asyncio
async def test_remove_from_groups_rollback():
    from app.connectors.executors.google_workspace.remove_from_groups import rollback
    result = await rollback(
        {"user_email": "alice@example.com"},
        {"groups_removed": ["group1@example.com"]},
        MockConnector(),
    )
    assert "rolled_back" in result or "action" in result
```

- [ ] **Step 2: Run tests — expect ImportError (modules don't exist yet)**

```bash
cd backend && python -m pytest app/tests/test_google_workspace_changes.py -v 2>&1 | head -40
```

Expected: `ModuleNotFoundError` or `ImportError` for each executor module.

- [ ] **Step 3: Implement `remove_from_groups.py`**

Create `backend/app/connectors/executors/google_workspace/remove_from_groups.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_email = parameters["user_email"]
    dry_run = parameters.get("dry_run", False)

    if not creds:
        return {
            "action": "remove_from_groups",
            "user_email": user_email,
            "groups_removed": [],
            "mock": True,
        }

    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")

    def _call():
        groups_resp = service.groups().list(userKey=user_email).execute()
        groups = groups_resp.get("groups", [])
        removed = []
        errors = []
        for group in groups:
            group_email = group["email"]
            if dry_run:
                removed.append(group_email)
                continue
            try:
                service.members().delete(groupKey=group_email, memberKey=user_email).execute()
                removed.append(group_email)
            except Exception as exc:
                errors.append({"group": group_email, "error": str(exc)})
        return {"groups_removed": removed, "errors": errors}

    result = await loop.run_in_executor(None, _call)
    return {
        "action": "remove_from_groups",
        "user_email": user_email,
        "dry_run": dry_run,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Re-add user to each group they were removed from."""
    creds = getattr(connector, "credentials", {})
    user_email = parameters["user_email"]
    groups = execution_result.get("groups_removed", [])

    if not creds:
        return {"action": "restore_group_memberships", "user_email": user_email, "groups_restored": groups, "mock": True}

    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")

    def _restore():
        restored = []
        errors = []
        for group_email in groups:
            try:
                service.members().insert(
                    groupKey=group_email,
                    body={"email": user_email, "role": "MEMBER"},
                ).execute()
                restored.append(group_email)
            except Exception as exc:
                errors.append({"group": group_email, "error": str(exc)})
        return {"groups_restored": restored, "errors": errors}

    result = await loop.run_in_executor(None, _restore)
    return {"action": "restore_group_memberships", "user_email": user_email, **result}
```

- [ ] **Step 4: Implement `reset_2fa.py`**

Create `backend/app/connectors/executors/google_workspace/reset_2fa.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_email = parameters["user_email"]

    if not creds:
        return {"action": "reset_2fa", "user_email": user_email, "reset": True, "mock": True}

    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")

    def _call():
        service.twoStepVerification().turnOff(userKey=user_email).execute()

    await loop.run_in_executor(None, _call)
    return {
        "action": "reset_2fa",
        "user_email": user_email,
        "reset": True,
        "rollback_data": None,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "2FA reset cannot be automatically rolled back. User must re-enroll.",
    }
```

- [ ] **Step 5: Implement `revoke_oauth_tokens.py`**

Create `backend/app/connectors/executors/google_workspace/revoke_oauth_tokens.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_email = parameters["user_email"]
    dry_run = parameters.get("dry_run", False)

    if not creds:
        return {
            "action": "revoke_oauth_tokens",
            "user_email": user_email,
            "tokens_revoked": [],
            "mock": True,
        }

    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")

    def _call():
        resp = service.tokens().list(userKey=user_email).execute()
        tokens = resp.get("items", [])
        revoked = []
        errors = []
        for token in tokens:
            client_id = token["clientId"]
            snapshot = {
                "clientId": client_id,
                "displayText": token.get("displayText", ""),
                "scopes": token.get("scopes", []),
            }
            if dry_run:
                revoked.append(snapshot)
                continue
            try:
                service.tokens().delete(userKey=user_email, clientId=client_id).execute()
                revoked.append(snapshot)
            except Exception as exc:
                errors.append({"clientId": client_id, "error": str(exc)})
        return {"tokens_revoked": revoked, "errors": errors}

    result = await loop.run_in_executor(None, _call)
    return {
        "action": "revoke_oauth_tokens",
        "user_email": user_email,
        "dry_run": dry_run,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Individual OAuth grants cannot be re-issued programmatically.",
        "tokens_revoked_for_audit": execution_result.get("tokens_revoked", []),
    }
```

- [ ] **Step 6: Implement `wipe_mobile_device.py`**

Create `backend/app/connectors/executors/google_workspace/wipe_mobile_device.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_email = parameters["user_email"]
    resource_id = parameters.get("resource_id")
    confirm_wipe = parameters.get("confirm_wipe", False)

    if not confirm_wipe:
        return {
            "action": "wipe_mobile_device",
            "user_email": user_email,
            "skipped": True,
            "reason": "confirm_wipe must be True to execute this irreversible action.",
        }

    if not creds:
        return {"action": "wipe_mobile_device", "user_email": user_email, "wiped": True, "mock": True}

    from ._client import get_admin_service
    loop = asyncio.get_event_loop()
    service = get_admin_service(creds, "admin", "directory_v1")

    def _call():
        if resource_id:
            device_ids = [resource_id]
        else:
            resp = service.mobiledevices().list(
                customerId="my_customer", query=f"email:{user_email}"
            ).execute()
            device_ids = [d["resourceId"] for d in resp.get("mobiledevices", [])]

        wiped = []
        errors = []
        for rid in device_ids:
            try:
                service.mobiledevices().action(
                    customerId="my_customer",
                    resourceId=rid,
                    body={"action": "wipe"},
                ).execute()
                wiped.append(rid)
            except Exception as exc:
                errors.append({"resourceId": rid, "error": str(exc)})
        return {"devices_wiped": wiped, "errors": errors}

    result = await loop.run_in_executor(None, _call)
    return {
        "action": "wipe_mobile_device",
        "user_email": user_email,
        "wiped": len(result["devices_wiped"]) > 0,
        "rollback_data": None,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "Device wipe is irreversible. All data on the device has been deleted."}
```

- [ ] **Step 7: Run tests — expect pass**

```bash
cd backend && python -m pytest app/tests/test_google_workspace_changes.py -v
```

Expected: all 6 tests pass (mock-mode, no real credentials needed).

- [ ] **Step 8: Update `google_workspace.json` catalog**

In `backend/app/connectors/catalog/google_workspace.json`, add these 4 entries to the `"actions"` array (before the closing `]`):

```json
    {"action_id": "remove_from_groups", "generic_action": "remove_from_groups", "action_type": "change", "execution_tier": 2, "display_name": "Remove From All Groups", "description": "Remove a user from every Google Group they belong to. Rollback: re-add to each group.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "user_email", "type": "string", "required": true}, {"name": "dry_run", "type": "boolean", "required": false, "default": false}], "executor": "google_workspace.remove_from_groups", "rollback_action": "remove_from_groups", "estimated_duration_seconds": 15, "blast_radius_hint": "group_membership_loss"},
    {"action_id": "reset_2fa", "generic_action": "reset_2fa", "action_type": "change", "execution_tier": 2, "display_name": "Reset 2FA Enrollment", "description": "Revoke all 2-Step Verification methods, forcing re-enrollment on next login. Irreversible.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "user_email", "type": "string", "required": true}], "executor": "google_workspace.reset_2fa", "estimated_duration_seconds": 5, "blast_radius_hint": "mfa_revocation", "safety_notes": ["2FA reset cannot be automatically rolled back. User must re-enroll."]},
    {"action_id": "revoke_oauth_tokens", "generic_action": "revoke_oauth_tokens", "action_type": "change", "execution_tier": 2, "display_name": "Revoke All OAuth Tokens", "description": "Enumerate and revoke every third-party OAuth token granted by a user.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "user_email", "type": "string", "required": true}, {"name": "dry_run", "type": "boolean", "required": false, "default": false}], "executor": "google_workspace.revoke_oauth_tokens", "estimated_duration_seconds": 10},
    {"action_id": "wipe_mobile_device", "generic_action": "wipe_mobile_device", "action_type": "change", "execution_tier": 3, "display_name": "Wipe Mobile Device (User-Scoped)", "description": "Remotely wipe all enrolled mobile devices for a user, or a specific device by resource_id. IRREVERSIBLE.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "user_email", "type": "string", "required": true}, {"name": "resource_id", "type": "string", "required": false}, {"name": "confirm_wipe", "type": "boolean", "required": true}], "executor": "google_workspace.wipe_mobile_device", "estimated_duration_seconds": 30, "blast_radius_hint": "data_destruction", "safety_notes": ["THIS ACTION IS IRREVERSIBLE. All data on the device will be deleted."]}
```

- [ ] **Step 9: Verify catalog loads cleanly**

```bash
cd backend && python -c "from app.connectors.catalog_service import get_catalog_service; svc = get_catalog_service(); print(len([a for a in svc._catalog.get('google_workspace', []) if a['action_type']=='change']), 'change actions')"
```

Expected: prints `11 change actions` (7 existing + 4 new).

- [ ] **Step 10: Commit**

```bash
git add backend/app/connectors/executors/google_workspace/remove_from_groups.py \
        backend/app/connectors/executors/google_workspace/reset_2fa.py \
        backend/app/connectors/executors/google_workspace/revoke_oauth_tokens.py \
        backend/app/connectors/executors/google_workspace/wipe_mobile_device.py \
        backend/app/connectors/catalog/google_workspace.json \
        backend/app/tests/test_google_workspace_changes.py
git commit -m "feat(google-workspace): add remove_from_groups, reset_2fa, revoke_oauth_tokens, wipe_mobile_device change actions"
```

---

## Task 2: GitHub — 6 new change action executors

**Files:**
- Create: `backend/app/connectors/executors/github/remove_org_member.py`
- Create: `backend/app/connectors/executors/github/revoke_user_pats.py`
- Create: `backend/app/connectors/executors/github/enforce_branch_protection.py`
- Create: `backend/app/connectors/executors/github/archive_repo.py`
- Create: `backend/app/connectors/executors/github/disable_actions.py`
- Create: `backend/app/connectors/executors/github/enable_actions.py`
- Modify: `backend/app/connectors/catalog/github.json`
- Create: `backend/app/tests/test_github_changes.py`

### Step 1: Write failing tests first

Create `backend/app/tests/test_github_changes.py`:

```python
import pytest

MockConnector = type("Connector", (), {"credentials": {}})
RealConnector = type("Connector", (), {"credentials": {"token": "ghp_test", "org": "my-org"}})


@pytest.mark.asyncio
async def test_remove_org_member_mock():
    from app.connectors.executors.github.remove_org_member import execute
    result = await execute({"username": "octocat"}, [], MockConnector())
    assert result["action"] == "remove_org_member"
    assert result["username"] == "octocat"


@pytest.mark.asyncio
async def test_revoke_user_pats_mock():
    from app.connectors.executors.github.revoke_user_pats import execute
    result = await execute({"username": "octocat"}, [], MockConnector())
    assert result["action"] == "revoke_user_pats"
    assert isinstance(result["pats_revoked"], list)


@pytest.mark.asyncio
async def test_enforce_branch_protection_mock():
    from app.connectors.executors.github.enforce_branch_protection import execute
    result = await execute(
        {"repo": "my-repo", "branch": "main", "require_pr_reviews": True, "enforce_admins": True},
        [],
        MockConnector(),
    )
    assert result["action"] == "enforce_branch_protection"
    assert result["repo"] == "my-repo"


@pytest.mark.asyncio
async def test_archive_repo_mock():
    from app.connectors.executors.github.archive_repo import execute
    result = await execute({"repo": "my-repo"}, [], MockConnector())
    assert result["action"] == "archive_repo"
    assert result["archived"] is True


@pytest.mark.asyncio
async def test_disable_actions_mock():
    from app.connectors.executors.github.disable_actions import execute
    result = await execute({"repo": "my-repo"}, [], MockConnector())
    assert result["action"] == "disable_actions"
    assert result["enabled"] is False


@pytest.mark.asyncio
async def test_enable_actions_mock():
    from app.connectors.executors.github.enable_actions import execute
    result = await execute({"repo": "my-repo"}, [], MockConnector())
    assert result["action"] == "enable_actions"
    assert result["enabled"] is True


@pytest.mark.asyncio
async def test_archive_repo_rollback():
    from app.connectors.executors.github.archive_repo import rollback
    result = await rollback({"repo": "my-repo"}, {"archived": True}, MockConnector())
    assert "rolled_back" in result or "action" in result
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
cd backend && python -m pytest app/tests/test_github_changes.py -v 2>&1 | head -40
```

Expected: `ModuleNotFoundError` for each new executor.

- [ ] **Step 3: Implement `remove_org_member.py`**

Create `backend/app/connectors/executors/github/remove_org_member.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    username = parameters["username"]
    dry_run = parameters.get("dry_run", False)

    if not creds:
        return {"action": "remove_org_member", "username": username, "removed": True, "mock": True}

    import github as gh
    loop = asyncio.get_event_loop()

    def _call():
        g = gh.Github(creds["token"])
        org = g.get_organization(creds["org"])
        user = g.get_user(username)
        # Snapshot pre-change state for rollback
        teams = [t.name for t in org.get_teams() if t.has_in_members(user)]
        try:
            membership = org.get_membership(user)
            org_role = membership.role
        except Exception:
            org_role = "member"
        rollback_data = {"username": username, "org_role": org_role, "teams": teams}
        if not dry_run:
            org.remove_from_members(user)
        return rollback_data

    rollback_data = await loop.run_in_executor(None, _call)
    return {
        "action": "remove_org_member",
        "username": username,
        "dry_run": dry_run,
        "removed": not dry_run,
        "rollback_data": rollback_data,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Re-invitation must be accepted by the user. Send invitation via GitHub org settings.",
        "rollback_data": execution_result.get("rollback_data"),
    }
```

- [ ] **Step 4: Implement `revoke_user_pats.py`**

Create `backend/app/connectors/executors/github/revoke_user_pats.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    username = parameters["username"]
    dry_run = parameters.get("dry_run", False)

    if not creds:
        return {"action": "revoke_user_pats", "username": username, "pats_revoked": [], "mock": True}

    import httpx
    loop = asyncio.get_event_loop()

    def _call():
        headers = {
            "Authorization": f"Bearer {creds['token']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        org = creds["org"]
        # List fine-grained PATs for the org filtered by owner
        resp = httpx.get(
            f"https://api.github.com/orgs/{org}/personal-access-tokens",
            params={"owner": username},
            headers=headers,
        )
        resp.raise_for_status()
        pats = resp.json()
        revoked = []
        errors = []
        for pat in pats:
            pat_id = pat["id"]
            meta = {"id": pat_id, "name": pat.get("name"), "last_used": pat.get("last_used_at")}
            if dry_run:
                revoked.append(meta)
                continue
            del_resp = httpx.delete(
                f"https://api.github.com/orgs/{org}/personal-access-tokens/{pat_id}",
                headers=headers,
            )
            if del_resp.status_code in (204, 404):
                revoked.append(meta)
            else:
                errors.append({"id": pat_id, "status": del_resp.status_code})
        return {"pats_revoked": revoked, "errors": errors}

    result = await loop.run_in_executor(None, _call)
    return {
        "action": "revoke_user_pats",
        "username": username,
        "dry_run": dry_run,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "PATs cannot be re-issued programmatically.",
        "pats_revoked_for_audit": execution_result.get("pats_revoked", []),
    }
```

- [ ] **Step 5: Implement `enforce_branch_protection.py`**

Create `backend/app/connectors/executors/github/enforce_branch_protection.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]
    branch = parameters["branch"]
    dry_run = parameters.get("dry_run", False)

    if not creds:
        return {"action": "enforce_branch_protection", "repo": repo, "branch": branch, "applied": True, "mock": True}

    import httpx
    loop = asyncio.get_event_loop()

    def _call():
        org = creds["org"]
        headers = {
            "Authorization": f"Bearer {creds['token']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        # Snapshot current protection for rollback
        existing_resp = httpx.get(
            f"https://api.github.com/repos/{org}/{repo}/branches/{branch}/protection",
            headers=headers,
        )
        rollback_data = existing_resp.json() if existing_resp.status_code == 200 else None

        # Build protection payload from parameters
        required_approvals = parameters.get("required_approving_review_count", 1)
        protection = {
            "required_status_checks": None,
            "enforce_admins": parameters.get("enforce_admins", True),
            "required_pull_request_reviews": {
                "required_approving_review_count": required_approvals,
                "dismiss_stale_reviews": True,
            } if parameters.get("require_pr_reviews", True) else None,
            "restrictions": None,
        }
        if parameters.get("require_status_checks"):
            protection["required_status_checks"] = {
                "strict": True,
                "contexts": parameters["require_status_checks"],
            }
        if parameters.get("restrict_push"):
            protection["restrictions"] = {
                "users": [],
                "teams": parameters["restrict_push"],
                "apps": [],
            }

        if not dry_run:
            put_resp = httpx.put(
                f"https://api.github.com/repos/{org}/{repo}/branches/{branch}/protection",
                headers=headers,
                json=protection,
            )
            put_resp.raise_for_status()
        return rollback_data

    rollback_data = await loop.run_in_executor(None, _call)
    return {
        "action": "enforce_branch_protection",
        "repo": repo,
        "branch": branch,
        "dry_run": dry_run,
        "applied": not dry_run,
        "rollback_data": rollback_data,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    rollback_data = execution_result.get("rollback_data")
    repo = parameters["repo"]
    branch = parameters["branch"]

    if not creds:
        return {"action": "restore_branch_protection", "repo": repo, "branch": branch, "mock": True}

    import httpx
    loop = asyncio.get_event_loop()

    def _restore():
        org = creds["org"]
        headers = {
            "Authorization": f"Bearer {creds['token']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if rollback_data is None:
            # No protection existed before; delete
            httpx.delete(
                f"https://api.github.com/repos/{org}/{repo}/branches/{branch}/protection",
                headers=headers,
            )
            return {"restored": "deleted"}
        else:
            resp = httpx.put(
                f"https://api.github.com/repos/{org}/{repo}/branches/{branch}/protection",
                headers=headers,
                json=rollback_data,
            )
            resp.raise_for_status()
            return {"restored": "previous_policy"}

    result = await loop.run_in_executor(None, _restore)
    return {"action": "restore_branch_protection", "repo": repo, "branch": branch, **result}
```

- [ ] **Step 6: Implement `archive_repo.py`**

Create `backend/app/connectors/executors/github/archive_repo.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]

    if not creds:
        return {"action": "archive_repo", "repo": repo, "archived": True, "rollback_data": {"archived": False}, "mock": True}

    import github as gh
    loop = asyncio.get_event_loop()

    def _call():
        g = gh.Github(creds["token"])
        org = creds["org"]
        repository = g.get_repo(f"{org}/{repo}")
        was_archived = repository.archived
        repository.edit(archived=True)
        return was_archived

    was_archived = await loop.run_in_executor(None, _call)
    return {
        "action": "archive_repo",
        "repo": repo,
        "archived": True,
        "rollback_data": {"archived": was_archived},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]

    if not creds:
        return {"action": "unarchive_repo", "repo": repo, "mock": True}

    import github as gh
    loop = asyncio.get_event_loop()

    def _unarchive():
        g = gh.Github(creds["token"])
        repository = g.get_repo(f"{creds['org']}/{repo}")
        repository.edit(archived=False)

    await loop.run_in_executor(None, _unarchive)
    return {"action": "unarchive_repo", "repo": repo, "rolled_back": True}
```

- [ ] **Step 7: Implement `disable_actions.py` and `enable_actions.py`**

Create `backend/app/connectors/executors/github/disable_actions.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]

    if not creds:
        return {"action": "disable_actions", "repo": repo, "enabled": False, "mock": True}

    import httpx
    loop = asyncio.get_event_loop()

    def _call():
        org = creds["org"]
        headers = {
            "Authorization": f"Bearer {creds['token']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        # Snapshot current state
        current = httpx.get(f"https://api.github.com/repos/{org}/{repo}/actions/permissions", headers=headers)
        was_enabled = current.json().get("enabled", True) if current.status_code == 200 else True
        httpx.put(
            f"https://api.github.com/repos/{org}/{repo}/actions/permissions",
            headers=headers,
            json={"enabled": False},
        ).raise_for_status()
        return was_enabled

    was_enabled = await loop.run_in_executor(None, _call)
    return {
        "action": "disable_actions",
        "repo": repo,
        "enabled": False,
        "rollback_data": {"was_enabled": was_enabled},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.github.enable_actions import execute as enable
    return await enable(parameters, [], connector)
```

Create `backend/app/connectors/executors/github/enable_actions.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    repo = parameters["repo"]

    if not creds:
        return {"action": "enable_actions", "repo": repo, "enabled": True, "mock": True}

    import httpx
    loop = asyncio.get_event_loop()

    def _call():
        org = creds["org"]
        headers = {
            "Authorization": f"Bearer {creds['token']}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        httpx.put(
            f"https://api.github.com/repos/{org}/{repo}/actions/permissions",
            headers=headers,
            json={"enabled": True},
        ).raise_for_status()

    await loop.run_in_executor(None, _call)
    return {
        "action": "enable_actions",
        "repo": repo,
        "enabled": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.github.disable_actions import execute as disable
    return await disable(parameters, [], connector)
```

- [ ] **Step 8: Run tests — expect pass**

```bash
cd backend && python -m pytest app/tests/test_github_changes.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 9: Update `github.json` catalog**

Add these 6 entries to the `"actions"` array in `backend/app/connectors/catalog/github.json`:

```json
    {"action_id": "remove_org_member", "generic_action": "remove_org_member", "action_type": "change", "execution_tier": 3, "display_name": "Remove Org Member", "description": "Remove a user from the GitHub organization. Rollback requires the user to accept a re-invitation.", "applicable_asset_types": ["cloud_account", "identity"], "parameters": [{"name": "username", "type": "string", "required": true}, {"name": "dry_run", "type": "boolean", "required": false, "default": false}], "executor": "github.remove_org_member", "estimated_duration_seconds": 10, "blast_radius_hint": "access_loss"},
    {"action_id": "revoke_user_pats", "generic_action": "revoke_user_pats", "action_type": "change", "execution_tier": 2, "display_name": "Revoke User Fine-Grained PATs", "description": "Revoke all org-authorized fine-grained personal access tokens for a user. Classic PATs cannot be org-revoked via API.", "applicable_asset_types": ["cloud_account", "identity"], "parameters": [{"name": "username", "type": "string", "required": true}, {"name": "dry_run", "type": "boolean", "required": false, "default": false}], "executor": "github.revoke_user_pats", "estimated_duration_seconds": 15},
    {"action_id": "enforce_branch_protection", "generic_action": "enforce_branch_protection", "action_type": "change", "execution_tier": 2, "display_name": "Enforce Branch Protection", "description": "Apply a full branch protection ruleset to a repository branch. Snapshots existing rules for rollback.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "repo", "type": "string", "required": true}, {"name": "branch", "type": "string", "required": true}, {"name": "require_pr_reviews", "type": "boolean", "required": false, "default": true}, {"name": "required_approving_review_count", "type": "integer", "required": false, "default": 1}, {"name": "require_status_checks", "type": "array", "required": false}, {"name": "enforce_admins", "type": "boolean", "required": false, "default": true}, {"name": "restrict_push", "type": "array", "required": false}, {"name": "dry_run", "type": "boolean", "required": false, "default": false}], "executor": "github.enforce_branch_protection", "rollback_action": "enforce_branch_protection", "estimated_duration_seconds": 10},
    {"action_id": "archive_repo", "generic_action": "archive_repo", "action_type": "change", "execution_tier": 2, "display_name": "Archive Repository", "description": "Archive a repository, making it read-only. Rollback: unarchive.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "repo", "type": "string", "required": true}], "executor": "github.archive_repo", "rollback_action": "archive_repo", "estimated_duration_seconds": 5},
    {"action_id": "disable_actions", "generic_action": "disable_actions", "action_type": "change", "execution_tier": 2, "display_name": "Disable GitHub Actions", "description": "Disable GitHub Actions on a repository. Rollback: enable_actions.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "repo", "type": "string", "required": true}], "executor": "github.disable_actions", "rollback_action": "enable_actions", "estimated_duration_seconds": 5},
    {"action_id": "enable_actions", "generic_action": "enable_actions", "action_type": "change", "execution_tier": 2, "display_name": "Enable GitHub Actions", "description": "Re-enable GitHub Actions on a repository.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "repo", "type": "string", "required": true}], "executor": "github.enable_actions", "estimated_duration_seconds": 5}
```

- [ ] **Step 10: Verify catalog**

```bash
cd backend && python -c "from app.connectors.catalog_service import get_catalog_service; svc = get_catalog_service(); print(len(svc._catalog.get('github', [])), 'github actions')"
```

Expected: `17 github actions` (11 existing + 6 new).

- [ ] **Step 11: Commit**

```bash
git add backend/app/connectors/executors/github/remove_org_member.py \
        backend/app/connectors/executors/github/revoke_user_pats.py \
        backend/app/connectors/executors/github/enforce_branch_protection.py \
        backend/app/connectors/executors/github/archive_repo.py \
        backend/app/connectors/executors/github/disable_actions.py \
        backend/app/connectors/executors/github/enable_actions.py \
        backend/app/connectors/catalog/github.json \
        backend/app/tests/test_github_changes.py
git commit -m "feat(github): add remove_org_member, revoke_user_pats, enforce_branch_protection, archive_repo, disable/enable_actions"
```

---

## Task 3: Slack — New connector with 2 change action executors

Slack has no executor directory, `_client.py`, or catalog JSON yet. This task creates the full connector from scratch.

**Files:**
- Create: `backend/app/connectors/executors/slack/__init__.py`
- Create: `backend/app/connectors/executors/slack/_client.py`
- Create: `backend/app/connectors/executors/slack/deactivate_user.py`
- Create: `backend/app/connectors/executors/slack/reactivate_user.py`
- Create: `backend/app/connectors/catalog/slack.json`
- Create: `backend/app/tests/test_slack_changes.py`

### Step 1: Write failing tests first

Create `backend/app/tests/test_slack_changes.py`:

```python
import pytest

MockConnector = type("Connector", (), {"credentials": {}})


@pytest.mark.asyncio
async def test_deactivate_user_mock():
    from app.connectors.executors.slack.deactivate_user import execute
    result = await execute({"user_id": "U012AB3CD"}, [], MockConnector())
    assert result["action"] == "deactivate_user"
    assert result["user_id"] == "U012AB3CD"
    assert result["deactivated"] is True


@pytest.mark.asyncio
async def test_reactivate_user_mock():
    from app.connectors.executors.slack.reactivate_user import execute
    result = await execute({"user_id": "U012AB3CD"}, [], MockConnector())
    assert result["action"] == "reactivate_user"
    assert result["activated"] is True


@pytest.mark.asyncio
async def test_deactivate_user_rollback():
    from app.connectors.executors.slack.deactivate_user import rollback
    result = await rollback({"user_id": "U012AB3CD"}, {"deactivated": True}, MockConnector())
    assert result["action"] == "reactivate_user"


@pytest.mark.asyncio
async def test_slack_catalog_loads():
    import pathlib
    from app.connectors.catalog_service import ActionCatalogService
    catalog_dir = pathlib.Path(__file__).parent.parent / "connectors" / "catalog"
    svc = ActionCatalogService(catalog_dir)
    assert "slack" in svc._catalog
    slack_actions = [a["action_id"] for a in svc._catalog["slack"]]
    assert "deactivate_user" in slack_actions
    assert "reactivate_user" in slack_actions
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
cd backend && python -m pytest app/tests/test_slack_changes.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError` for slack executor modules.

- [ ] **Step 3: Create executor directory and `__init__.py`**

Create `backend/app/connectors/executors/slack/__init__.py` (empty file).

- [ ] **Step 4: Create `_client.py`**

Create `backend/app/connectors/executors/slack/_client.py`:

```python
def get_slack_client(creds: dict, token_type: str = "admin"):
    """Return a slack_sdk.WebClient using admin_token (xoxp-) or bot_token (xoxb-)."""
    from slack_sdk import WebClient
    if token_type == "admin":
        token = creds.get("admin_token") or creds.get("bot_token")
    else:
        token = creds.get("bot_token")
    if not token:
        raise ValueError(f"Slack credential missing '{token_type}_token'")
    return WebClient(token=token)
```

- [ ] **Step 5: Implement `deactivate_user.py`**

Create `backend/app/connectors/executors/slack/deactivate_user.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]

    if not creds:
        return {
            "action": "deactivate_user",
            "user_id": user_id,
            "deactivated": True,
            "rollback_data": {"user_id": user_id, "was_active": True},
            "mock": True,
        }

    from ._client import get_slack_client
    loop = asyncio.get_event_loop()
    client = get_slack_client(creds, token_type="admin")

    def _call():
        resp = client.admin_users_setInactive(user_id=user_id)
        if not resp["ok"]:
            raise RuntimeError(f"Slack API error: {resp.get('error')}")

    await loop.run_in_executor(None, _call)
    return {
        "action": "deactivate_user",
        "user_id": user_id,
        "deactivated": True,
        "rollback_data": {"user_id": user_id, "was_active": True},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.slack.reactivate_user import execute as reactivate
    return await reactivate(parameters, [], connector)
```

- [ ] **Step 6: Implement `reactivate_user.py`**

Create `backend/app/connectors/executors/slack/reactivate_user.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]

    if not creds:
        return {"action": "reactivate_user", "user_id": user_id, "activated": True, "mock": True}

    from ._client import get_slack_client
    loop = asyncio.get_event_loop()
    client = get_slack_client(creds, token_type="admin")

    def _call():
        resp = client.admin_users_setActive(user_id=user_id)
        if not resp["ok"]:
            raise RuntimeError(f"Slack API error: {resp.get('error')}")

    await loop.run_in_executor(None, _call)
    return {
        "action": "reactivate_user",
        "user_id": user_id,
        "activated": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.slack.deactivate_user import execute as deactivate
    return await deactivate(parameters, [], connector)
```

- [ ] **Step 7: Create `slack.json` catalog**

Create `backend/app/connectors/catalog/slack.json`:

```json
{
  "connector_type": "slack",
  "display_name": "Slack",
  "credential_fields": [
    {"name": "bot_token", "label": "Bot Token (xoxb-)", "type": "password", "required": true},
    {"name": "admin_token", "label": "Admin User Token (xoxp-, Enterprise Grid only)", "type": "password", "required": false}
  ],
  "actions": [
    {
      "action_id": "deactivate_user",
      "generic_action": "deactivate_user",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Deactivate User",
      "description": "Deactivate a Slack user, preventing sign-in. Enterprise Grid only — requires xoxp- admin token with admin.users:write scope.",
      "applicable_asset_types": ["identity", "cloud_account"],
      "parameters": [
        {"name": "user_id", "type": "string", "required": true, "description": "Slack user ID (e.g. U012AB3CD)"}
      ],
      "executor": "slack.deactivate_user",
      "rollback_action": "reactivate_user",
      "estimated_duration_seconds": 5,
      "blast_radius_hint": "account_access_loss",
      "safety_notes": ["Requires Enterprise Grid. User will be immediately unable to sign in."]
    },
    {
      "action_id": "reactivate_user",
      "generic_action": "reactivate_user",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Reactivate User",
      "description": "Reactivate a previously deactivated Slack user.",
      "applicable_asset_types": ["identity", "cloud_account"],
      "parameters": [
        {"name": "user_id", "type": "string", "required": true, "description": "Slack user ID to reactivate"}
      ],
      "executor": "slack.reactivate_user",
      "rollback_action": "deactivate_user",
      "estimated_duration_seconds": 5
    }
  ]
}
```

- [ ] **Step 8: Run tests — expect pass**

```bash
cd backend && python -m pytest app/tests/test_slack_changes.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 9: Commit**

```bash
git add backend/app/connectors/executors/slack/ \
        backend/app/connectors/catalog/slack.json \
        backend/app/tests/test_slack_changes.py
git commit -m "feat(slack): add Slack connector with deactivate_user and reactivate_user change actions"
```

---

## Task 4: Microsoft 365 / Entra ID — 3 new change action executors

The `entra_id` connector already has `revoke_sessions`, `disable_user`, `reset_mfa`, etc. This task adds the three actions missing from the spec: `remove_from_teams`, `assign_license`, and `remove_license`. The existing `_client.py` should provide MSAL token acquisition; confirm before adding a duplicate.

**Files:**
- Create: `backend/app/connectors/executors/entra_id/remove_from_teams.py`
- Create: `backend/app/connectors/executors/entra_id/assign_license.py`
- Create: `backend/app/connectors/executors/entra_id/remove_license.py`
- Modify: `backend/app/connectors/catalog/entra_id.json`
- Create: `backend/app/tests/test_m365_changes.py`

### Step 1: Read the existing `_client.py` before writing any code

```bash
cat backend/app/connectors/executors/entra_id/_client.py
```

Confirm it exports a function that returns an MSAL access token or an `httpx`-ready client. Use the same pattern in the new executors — do not create a second MSAL flow.

### Step 2: Write failing tests first

Create `backend/app/tests/test_m365_changes.py`:

```python
import pytest

MockConnector = type("Connector", (), {"credentials": {}})


@pytest.mark.asyncio
async def test_remove_from_teams_mock():
    from app.connectors.executors.entra_id.remove_from_teams import execute
    result = await execute({"user_id": "user-object-id"}, [], MockConnector())
    assert result["action"] == "remove_from_teams"
    assert isinstance(result["teams_removed"], list)


@pytest.mark.asyncio
async def test_assign_license_mock():
    from app.connectors.executors.entra_id.assign_license import execute
    result = await execute(
        {"user_id": "user-object-id", "sku_id": "6fd2c87f-b296-42f0-b197-1e91e994b900"},
        [],
        MockConnector(),
    )
    assert result["action"] == "assign_license"
    assert result["sku_id"] == "6fd2c87f-b296-42f0-b197-1e91e994b900"


@pytest.mark.asyncio
async def test_remove_license_mock():
    from app.connectors.executors.entra_id.remove_license import execute
    result = await execute(
        {"user_id": "user-object-id", "sku_id": "6fd2c87f-b296-42f0-b197-1e91e994b900"},
        [],
        MockConnector(),
    )
    assert result["action"] == "remove_license"
    assert result["removed"] is True


@pytest.mark.asyncio
async def test_remove_from_teams_rollback():
    from app.connectors.executors.entra_id.remove_from_teams import rollback
    result = await rollback(
        {"user_id": "user-object-id"},
        {"teams_removed": [{"teamId": "t1", "teamName": "Team1"}]},
        MockConnector(),
    )
    assert "rolled_back" in result or "action" in result
```

- [ ] **Step 3: Run tests — expect ImportError**

```bash
cd backend && python -m pytest app/tests/test_m365_changes.py -v 2>&1 | head -30
```

- [ ] **Step 4: Implement `remove_from_teams.py`**

Create `backend/app/connectors/executors/entra_id/remove_from_teams.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    dry_run = parameters.get("dry_run", False)

    if not creds:
        return {
            "action": "remove_from_teams",
            "user_id": user_id,
            "teams_removed": [],
            "mock": True,
        }

    from ._client import get_graph_token
    loop = asyncio.get_event_loop()

    def _call():
        import httpx
        token = get_graph_token(creds)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        graph = "https://graph.microsoft.com/v1.0"

        # Get joined teams
        resp = httpx.get(f"{graph}/users/{user_id}/joinedTeams", headers=headers)
        resp.raise_for_status()
        teams = resp.json().get("value", [])

        removed = []
        errors = []
        for team in teams:
            team_id = team["id"]
            team_name = team.get("displayName", team_id)
            snapshot = {"teamId": team_id, "teamName": team_name}
            if dry_run:
                removed.append(snapshot)
                continue
            del_resp = httpx.delete(
                f"{graph}/groups/{team_id}/members/{user_id}/$ref",
                headers=headers,
            )
            if del_resp.status_code in (204, 404):
                removed.append(snapshot)
            else:
                errors.append({"teamId": team_id, "status": del_resp.status_code})

        return {"teams_removed": removed, "errors": errors}

    result = await loop.run_in_executor(None, _call)
    return {
        "action": "remove_from_teams",
        "user_id": user_id,
        "dry_run": dry_run,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    teams = execution_result.get("teams_removed", [])

    if not creds:
        return {"action": "restore_team_memberships", "user_id": user_id, "mock": True}

    from ._client import get_graph_token
    loop = asyncio.get_event_loop()

    def _restore():
        import httpx
        token = get_graph_token(creds)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        graph = "https://graph.microsoft.com/v1.0"
        restored = []
        errors = []
        for team in teams:
            team_id = team["teamId"]
            body = {"@odata.id": f"{graph}/directoryObjects/{user_id}"}
            resp = httpx.post(f"{graph}/groups/{team_id}/members/$ref", headers=headers, json=body)
            if resp.status_code in (204, 201):
                restored.append(team_id)
            else:
                errors.append({"teamId": team_id, "status": resp.status_code})
        return {"teams_restored": restored, "errors": errors}

    result = await loop.run_in_executor(None, _restore)
    return {"action": "restore_team_memberships", "user_id": user_id, **result}
```

- [ ] **Step 5: Implement `assign_license.py`**

Create `backend/app/connectors/executors/entra_id/assign_license.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    sku_id = parameters["sku_id"]

    if not creds:
        return {"action": "assign_license", "user_id": user_id, "sku_id": sku_id, "assigned": True, "mock": True}

    from ._client import get_graph_token
    loop = asyncio.get_event_loop()

    def _call():
        import httpx
        token = get_graph_token(creds)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        graph = "https://graph.microsoft.com/v1.0"

        # Snapshot current licenses
        current_resp = httpx.get(f"{graph}/users/{user_id}?$select=assignedLicenses", headers=headers)
        current_resp.raise_for_status()
        previous_licenses = current_resp.json().get("assignedLicenses", [])

        assign_resp = httpx.post(
            f"{graph}/users/{user_id}/assignLicense",
            headers=headers,
            json={"addLicenses": [{"skuId": sku_id}], "removeLicenses": []},
        )
        assign_resp.raise_for_status()
        return previous_licenses

    previous_licenses = await loop.run_in_executor(None, _call)
    return {
        "action": "assign_license",
        "user_id": user_id,
        "sku_id": sku_id,
        "assigned": True,
        "rollback_data": {"previous_licenses": previous_licenses},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.entra_id.remove_license import execute as remove
    return await remove(parameters, [], connector)
```

- [ ] **Step 6: Implement `remove_license.py`**

Create `backend/app/connectors/executors/entra_id/remove_license.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    sku_id = parameters["sku_id"]

    if not creds:
        return {"action": "remove_license", "user_id": user_id, "sku_id": sku_id, "removed": True, "mock": True}

    from ._client import get_graph_token
    loop = asyncio.get_event_loop()

    def _call():
        import httpx
        token = get_graph_token(creds)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        graph = "https://graph.microsoft.com/v1.0"

        # Snapshot current licenses
        current_resp = httpx.get(f"{graph}/users/{user_id}?$select=assignedLicenses", headers=headers)
        current_resp.raise_for_status()
        previous_licenses = current_resp.json().get("assignedLicenses", [])

        remove_resp = httpx.post(
            f"{graph}/users/{user_id}/assignLicense",
            headers=headers,
            json={"addLicenses": [], "removeLicenses": [sku_id]},
        )
        remove_resp.raise_for_status()
        return previous_licenses

    previous_licenses = await loop.run_in_executor(None, _call)
    return {
        "action": "remove_license",
        "user_id": user_id,
        "sku_id": sku_id,
        "removed": True,
        "rollback_data": {"previous_licenses": previous_licenses},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.entra_id.assign_license import execute as assign
    return await assign(parameters, [], connector)
```

- [ ] **Step 7: Run tests — expect pass**

```bash
cd backend && python -m pytest app/tests/test_m365_changes.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 8: Update `entra_id.json` catalog**

Add these 3 entries to the `"actions"` array in `backend/app/connectors/catalog/entra_id.json`:

```json
    {"action_id": "remove_from_teams", "generic_action": "remove_from_teams", "action_type": "change", "execution_tier": 2, "display_name": "Remove From All Teams", "description": "Remove a user from every Microsoft Team they belong to. Rollback: re-add to each team.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true, "description": "Entra user ID or UPN"}, {"name": "dry_run", "type": "boolean", "required": false, "default": false}], "executor": "entra_id.remove_from_teams", "rollback_action": "remove_from_teams", "estimated_duration_seconds": 20, "blast_radius_hint": "group_membership_loss"},
    {"action_id": "assign_license", "generic_action": "assign_license", "action_type": "change", "execution_tier": 1, "display_name": "Assign M365 License", "description": "Assign a Microsoft 365 license SKU to a user. Rollback: remove the same license.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true}, {"name": "sku_id", "type": "string", "required": true, "description": "License SKU GUID (e.g. E3: 6fd2c87f-b296-42f0-b197-1e91e994b900)"}], "executor": "entra_id.assign_license", "rollback_action": "remove_license", "estimated_duration_seconds": 10},
    {"action_id": "remove_license", "generic_action": "remove_license", "action_type": "change", "execution_tier": 1, "display_name": "Remove M365 License", "description": "Remove a Microsoft 365 license SKU from a user. Rollback: re-assign.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true}, {"name": "sku_id", "type": "string", "required": true}], "executor": "entra_id.remove_license", "rollback_action": "assign_license", "estimated_duration_seconds": 10}
```

- [ ] **Step 9: Verify catalog**

```bash
cd backend && python -c "from app.connectors.catalog_service import get_catalog_service; svc = get_catalog_service(); print(len(svc._catalog.get('entra_id', [])), 'entra_id actions')"
```

Expected: `14 entra_id actions` (11 existing + 3 new).

- [ ] **Step 10: Commit**

```bash
git add backend/app/connectors/executors/entra_id/remove_from_teams.py \
        backend/app/connectors/executors/entra_id/assign_license.py \
        backend/app/connectors/executors/entra_id/remove_license.py \
        backend/app/connectors/catalog/entra_id.json \
        backend/app/tests/test_m365_changes.py
git commit -m "feat(m365): add remove_from_teams, assign_license, remove_license change actions to Entra ID connector"
```

---

## Task 5: Kubernetes — 7 new change action executors

**Files:**
- Create: `backend/app/connectors/executors/kubernetes/restart_deployment.py`
- Create: `backend/app/connectors/executors/kubernetes/scale_deployment.py`
- Create: `backend/app/connectors/executors/kubernetes/apply_network_policy.py`
- Create: `backend/app/connectors/executors/kubernetes/update_rbac.py`
- Create: `backend/app/connectors/executors/kubernetes/rotate_secret.py`
- Create: `backend/app/connectors/executors/kubernetes/helm_upgrade.py`
- Create: `backend/app/connectors/executors/kubernetes/helm_rollback.py`
- Modify: `backend/app/connectors/catalog/kubernetes.json`
- Create: `backend/app/tests/test_kubernetes_changes.py`

### Step 1: Read existing `_client.py` before writing any code

```bash
cat backend/app/connectors/executors/kubernetes/_client.py
```

Confirm what `get_k8s_clients(creds)` returns (expect a dict keyed by API group, e.g. `"apps"`, `"core"`, `"networking"`, `"rbac"`). All new executors use the same `get_k8s_clients` call.

### Step 2: Write failing tests first

Create `backend/app/tests/test_kubernetes_changes.py`:

```python
import pytest

MockConnector = type("Connector", (), {"credentials": {}})


@pytest.mark.asyncio
async def test_restart_deployment_mock():
    from app.connectors.executors.kubernetes.restart_deployment import execute
    result = await execute(
        {"namespace": "default", "deployment_name": "my-app"},
        [],
        MockConnector(),
    )
    assert result["action"] == "restart_deployment"
    assert result["deployment_name"] == "my-app"


@pytest.mark.asyncio
async def test_scale_deployment_mock():
    from app.connectors.executors.kubernetes.scale_deployment import execute
    result = await execute(
        {"namespace": "default", "deployment_name": "my-app", "replicas": 3},
        [],
        MockConnector(),
    )
    assert result["action"] == "scale_deployment"
    assert result["replicas"] == 3


@pytest.mark.asyncio
async def test_scale_deployment_rollback():
    from app.connectors.executors.kubernetes.scale_deployment import rollback
    result = await rollback(
        {"namespace": "default", "deployment_name": "my-app", "replicas": 3},
        {"previous_replicas": 1},
        MockConnector(),
    )
    assert result["action"] == "scale_deployment"


@pytest.mark.asyncio
async def test_apply_network_policy_mock():
    from app.connectors.executors.kubernetes.apply_network_policy import execute
    manifest = '{"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "deny-all"}, "spec": {"podSelector": {}}}'
    result = await execute(
        {"namespace": "default", "manifest": manifest},
        [],
        MockConnector(),
    )
    assert result["action"] == "apply_network_policy"


@pytest.mark.asyncio
async def test_update_rbac_mock():
    from app.connectors.executors.kubernetes.update_rbac import execute
    manifest = '{"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": "test-binding"}, "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "view"}, "subjects": []}'
    result = await execute({"manifest": manifest}, [], MockConnector())
    assert result["action"] == "update_rbac"


@pytest.mark.asyncio
async def test_rotate_secret_mock():
    from app.connectors.executors.kubernetes.rotate_secret import execute
    result = await execute(
        {"namespace": "default", "secret_name": "my-secret", "data": {"API_KEY": "new-value"}},
        [],
        MockConnector(),
    )
    assert result["action"] == "rotate_secret"
    assert result["secret_name"] == "my-secret"


@pytest.mark.asyncio
async def test_helm_upgrade_mock():
    from app.connectors.executors.kubernetes.helm_upgrade import execute
    result = await execute(
        {"namespace": "default", "release_name": "my-release", "chart": "bitnami/nginx"},
        [],
        MockConnector(),
    )
    assert result["action"] == "helm_upgrade"
    assert result["release_name"] == "my-release"


@pytest.mark.asyncio
async def test_helm_rollback_mock():
    from app.connectors.executors.kubernetes.helm_rollback import execute
    result = await execute(
        {"namespace": "default", "release_name": "my-release", "revision": 2},
        [],
        MockConnector(),
    )
    assert result["action"] == "helm_rollback"
```

- [ ] **Step 3: Run tests — expect ImportError**

```bash
cd backend && python -m pytest app/tests/test_kubernetes_changes.py -v 2>&1 | head -40
```

- [ ] **Step 4: Implement `restart_deployment.py`**

Create `backend/app/connectors/executors/kubernetes/restart_deployment.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    namespace = parameters["namespace"]
    deployment_name = parameters["deployment_name"]
    now = datetime.now(timezone.utc).isoformat()

    if not creds:
        return {
            "action": "restart_deployment",
            "namespace": namespace,
            "deployment_name": deployment_name,
            "restarted_at": now,
            "mock": True,
        }

    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)

    def _call():
        apps = clients["apps"]
        # Snapshot current restartedAt annotation
        dep = apps.read_namespaced_deployment(deployment_name, namespace)
        annotations = dep.spec.template.metadata.annotations or {}
        previous_restart = annotations.get("kubectl.kubernetes.io/restartedAt")

        patch = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {"kubectl.kubernetes.io/restartedAt": now}
                    }
                }
            }
        }
        apps.patch_namespaced_deployment(deployment_name, namespace, patch)
        return {
            "previous_restart": previous_restart,
            "resource_version": dep.metadata.resource_version,
        }

    snapshot = await loop.run_in_executor(None, _call)
    return {
        "action": "restart_deployment",
        "namespace": namespace,
        "deployment_name": deployment_name,
        "restarted_at": now,
        "rollback_data": snapshot,
        "executed_at": now,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Pods were restarted from the same image. No meaningful rollback — re-deploy if needed.",
        "snapshot": execution_result.get("rollback_data"),
    }
```

- [ ] **Step 5: Implement `scale_deployment.py`**

Create `backend/app/connectors/executors/kubernetes/scale_deployment.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    namespace = parameters["namespace"]
    deployment_name = parameters["deployment_name"]
    replicas = parameters["replicas"]

    if not creds:
        return {
            "action": "scale_deployment",
            "namespace": namespace,
            "deployment_name": deployment_name,
            "replicas": replicas,
            "mock": True,
        }

    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)

    def _call():
        apps = clients["apps"]
        current_scale = apps.read_namespaced_deployment_scale(deployment_name, namespace)
        previous_replicas = current_scale.spec.replicas or 0
        apps.patch_namespaced_deployment_scale(
            deployment_name, namespace, {"spec": {"replicas": replicas}}
        )
        return previous_replicas

    previous_replicas = await loop.run_in_executor(None, _call)
    return {
        "action": "scale_deployment",
        "namespace": namespace,
        "deployment_name": deployment_name,
        "replicas": replicas,
        "previous_replicas": previous_replicas,
        "rollback_data": {"previous_replicas": previous_replicas},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    previous = execution_result.get("previous_replicas", execution_result.get("rollback_data", {}).get("previous_replicas"))
    rollback_params = {**parameters, "replicas": previous}
    return await execute(rollback_params, [], connector)
```

- [ ] **Step 6: Implement `apply_network_policy.py`**

Create `backend/app/connectors/executors/kubernetes/apply_network_policy.py`:

```python
import asyncio
import json
import yaml
from datetime import datetime, timezone


def _parse_manifest(manifest_str: str) -> dict:
    try:
        return json.loads(manifest_str)
    except json.JSONDecodeError:
        return yaml.safe_load(manifest_str)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    namespace = parameters["namespace"]
    manifest_str = parameters["manifest"]
    manifest = _parse_manifest(manifest_str)
    policy_name = manifest.get("metadata", {}).get("name", "unknown")

    if not creds:
        return {
            "action": "apply_network_policy",
            "namespace": namespace,
            "policy_name": policy_name,
            "applied": True,
            "mock": True,
        }

    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)

    def _call():
        networking = clients["networking"]
        # Check if policy exists (for rollback snapshot)
        try:
            existing = networking.read_namespaced_network_policy(policy_name, namespace)
            existing_dict = existing.to_dict()
            rollback_data = {"existed": True, "previous": existing_dict}
        except Exception:
            rollback_data = {"existed": False, "name": policy_name, "namespace": namespace}

        # Server-side apply: try patch first, then create
        try:
            networking.patch_namespaced_network_policy(policy_name, namespace, manifest)
        except Exception:
            networking.create_namespaced_network_policy(namespace, manifest)
        return rollback_data

    rollback_data = await loop.run_in_executor(None, _call)
    return {
        "action": "apply_network_policy",
        "namespace": namespace,
        "policy_name": policy_name,
        "applied": True,
        "rollback_data": rollback_data,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    rollback_data = execution_result.get("rollback_data", {})
    namespace = parameters["namespace"]
    manifest = _parse_manifest(parameters["manifest"])
    policy_name = manifest.get("metadata", {}).get("name", "unknown")

    if not creds:
        return {"action": "restore_network_policy", "namespace": namespace, "mock": True}

    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)

    def _restore():
        networking = clients["networking"]
        if rollback_data.get("existed"):
            networking.replace_namespaced_network_policy(policy_name, namespace, rollback_data["previous"])
        else:
            networking.delete_namespaced_network_policy(policy_name, namespace)

    await loop.run_in_executor(None, _restore)
    return {"action": "restore_network_policy", "namespace": namespace, "policy_name": policy_name, "rolled_back": True}
```

- [ ] **Step 7: Implement `update_rbac.py`**

Create `backend/app/connectors/executors/kubernetes/update_rbac.py`:

```python
import asyncio
import json
import yaml
from datetime import datetime, timezone


def _parse_manifest(manifest_str: str) -> dict:
    try:
        return json.loads(manifest_str)
    except json.JSONDecodeError:
        return yaml.safe_load(manifest_str)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    manifest_str = parameters["manifest"]
    namespace = parameters.get("namespace")
    manifest = _parse_manifest(manifest_str)
    kind = manifest.get("kind", "")
    name = manifest.get("metadata", {}).get("name", "unknown")

    if not creds:
        return {"action": "update_rbac", "kind": kind, "name": name, "applied": True, "mock": True}

    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)

    def _call():
        rbac = clients["rbac"]
        is_cluster_scoped = kind == "ClusterRoleBinding"

        # Snapshot existing binding
        try:
            if is_cluster_scoped:
                existing = rbac.read_cluster_role_binding(name)
            else:
                existing = rbac.read_namespaced_role_binding(name, namespace)
            rollback_data = {"existed": True, "previous": existing.to_dict()}
        except Exception:
            rollback_data = {"existed": False, "name": name, "namespace": namespace, "kind": kind}

        # Apply: try patch then create
        try:
            if is_cluster_scoped:
                rbac.patch_cluster_role_binding(name, manifest)
            else:
                rbac.patch_namespaced_role_binding(name, namespace, manifest)
        except Exception:
            if is_cluster_scoped:
                rbac.create_cluster_role_binding(manifest)
            else:
                rbac.create_namespaced_role_binding(namespace, manifest)

        return rollback_data

    rollback_data = await loop.run_in_executor(None, _call)
    return {
        "action": "update_rbac",
        "kind": kind,
        "name": name,
        "namespace": namespace,
        "applied": True,
        "rollback_data": rollback_data,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Restore previous RBAC binding by re-applying rollback_data manifest manually.",
        "rollback_data": execution_result.get("rollback_data"),
    }
```

- [ ] **Step 8: Implement `rotate_secret.py`**

Create `backend/app/connectors/executors/kubernetes/rotate_secret.py`:

```python
import asyncio
import base64
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    namespace = parameters["namespace"]
    secret_name = parameters["secret_name"]
    data = parameters["data"]  # plain-text key-value pairs
    restart_deployments = parameters.get("restart_deployments", [])

    if not creds:
        return {
            "action": "rotate_secret",
            "namespace": namespace,
            "secret_name": secret_name,
            "keys_rotated": list(data.keys()),
            "deployments_restarted": restart_deployments,
            "mock": True,
        }

    from ._client import get_k8s_clients
    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    now = datetime.now(timezone.utc).isoformat()

    def _call():
        core = clients["core"]
        apps = clients["apps"]

        # Snapshot current secret keys (not values — security)
        existing = core.read_namespaced_secret(secret_name, namespace)
        previous_keys = list((existing.data or {}).keys())

        # Encode values to base64
        encoded_data = {k: base64.b64encode(v.encode()).decode() for k, v in data.items()}
        core.patch_namespaced_secret(secret_name, namespace, {"data": encoded_data})

        # Restart specified or auto-detected deployments
        deployments_restarted = []
        targets = restart_deployments
        if not targets:
            # Auto-detect: list all deployments and check if they reference the secret
            all_deps = apps.list_namespaced_deployment(namespace)
            for dep in all_deps.items:
                volumes = dep.spec.template.spec.volumes or []
                for vol in volumes:
                    if vol.secret and vol.secret.secret_name == secret_name:
                        targets.append(dep.metadata.name)
                        break
                else:
                    # Also check envFrom
                    for container in dep.spec.template.spec.containers:
                        for env_from in (container.env_from or []):
                            if env_from.secret_ref and env_from.secret_ref.name == secret_name:
                                targets.append(dep.metadata.name)
                                break

        for dep_name in targets:
            try:
                apps.patch_namespaced_deployment(
                    dep_name, namespace,
                    {"spec": {"template": {"metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}}}}
                )
                deployments_restarted.append(dep_name)
            except Exception:
                pass

        return {"previous_keys": previous_keys, "deployments_restarted": deployments_restarted}

    result = await loop.run_in_executor(None, _call)
    return {
        "action": "rotate_secret",
        "namespace": namespace,
        "secret_name": secret_name,
        "keys_rotated": list(data.keys()),
        "deployments_restarted": result["deployments_restarted"],
        "rollback_data": {"previous_keys": result["previous_keys"]},
        "executed_at": now,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Secret rollback requires re-applying previous values. Store rollback values in SecretsService before executing.",
        "previous_keys": execution_result.get("rollback_data", {}).get("previous_keys", []),
    }
```

- [ ] **Step 9: Implement `helm_upgrade.py`**

Create `backend/app/connectors/executors/kubernetes/helm_upgrade.py`:

```python
import asyncio
import base64
import json
import subprocess
import tempfile
import os
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    namespace = parameters["namespace"]
    release_name = parameters["release_name"]
    chart = parameters["chart"]
    version = parameters.get("version")
    values = parameters.get("values", {})
    values_yaml = parameters.get("values_yaml")
    dry_run = parameters.get("dry_run", False)

    if not creds:
        return {
            "action": "helm_upgrade",
            "namespace": namespace,
            "release_name": release_name,
            "chart": chart,
            "upgraded": True,
            "mock": True,
        }

    loop = asyncio.get_event_loop()

    def _call():
        kubeconfig_b64 = creds.get("kubeconfig_b64") or creds.get("kubeconfig")
        kubeconfig_bytes = base64.b64decode(kubeconfig_b64)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".yaml") as kf:
            kf.write(kubeconfig_bytes)
            kubeconfig_path = kf.name

        values_file = None
        try:
            # Get previous revision for rollback
            history_proc = subprocess.run(
                ["helm", "history", release_name, "--namespace", namespace,
                 "--kubeconfig", kubeconfig_path, "--output", "json"],
                capture_output=True, text=True,
            )
            previous_revision = None
            if history_proc.returncode == 0:
                history = json.loads(history_proc.stdout)
                if history:
                    previous_revision = history[-1].get("revision")

            cmd = [
                "helm", "upgrade", "--install", release_name, chart,
                "--namespace", namespace,
                "--kubeconfig", kubeconfig_path,
                "--atomic", "--timeout", "5m0s",
                "--output", "json",
            ]
            if version:
                cmd += ["--version", version]
            for k, v in values.items():
                cmd += ["--set", f"{k}={v}"]
            if values_yaml:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".yaml", mode="w") as vf:
                    vf.write(values_yaml)
                    values_file = vf.name
                cmd += ["--values", values_file]
            if dry_run:
                cmd.append("--dry-run")

            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"helm upgrade failed: {proc.stderr}")
            return {"previous_revision": previous_revision, "helm_output": proc.stdout}
        finally:
            os.unlink(kubeconfig_path)
            if values_file:
                os.unlink(values_file)

    result = await loop.run_in_executor(None, _call)
    return {
        "action": "helm_upgrade",
        "namespace": namespace,
        "release_name": release_name,
        "chart": chart,
        "dry_run": dry_run,
        "upgraded": True,
        "rollback_data": {"previous_revision": result["previous_revision"]},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.kubernetes.helm_rollback import execute as helm_rb
    revision = execution_result.get("rollback_data", {}).get("previous_revision")
    rollback_params = {
        "namespace": parameters["namespace"],
        "release_name": parameters["release_name"],
        "revision": revision,
    }
    return await helm_rb(rollback_params, [], connector)
```

- [ ] **Step 10: Implement `helm_rollback.py`**

Create `backend/app/connectors/executors/kubernetes/helm_rollback.py`:

```python
import asyncio
import base64
import json
import subprocess
import tempfile
import os
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    namespace = parameters["namespace"]
    release_name = parameters["release_name"]
    revision = parameters.get("revision")  # None = one revision back

    if not creds:
        return {
            "action": "helm_rollback",
            "namespace": namespace,
            "release_name": release_name,
            "revision": revision,
            "rolled_back": True,
            "mock": True,
        }

    loop = asyncio.get_event_loop()

    def _call():
        kubeconfig_b64 = creds.get("kubeconfig_b64") or creds.get("kubeconfig")
        kubeconfig_bytes = base64.b64decode(kubeconfig_b64)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".yaml") as kf:
            kf.write(kubeconfig_bytes)
            kubeconfig_path = kf.name

        try:
            # Snapshot current revision before rollback
            history_proc = subprocess.run(
                ["helm", "history", release_name, "--namespace", namespace,
                 "--kubeconfig", kubeconfig_path, "--output", "json"],
                capture_output=True, text=True,
            )
            current_revision = None
            if history_proc.returncode == 0:
                history = json.loads(history_proc.stdout)
                if history:
                    current_revision = history[-1].get("revision")

            cmd = ["helm", "rollback", release_name, "--namespace", namespace,
                   "--kubeconfig", kubeconfig_path, "--wait"]
            if revision is not None:
                cmd.append(str(revision))

            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"helm rollback failed: {proc.stderr}")
            return current_revision
        finally:
            os.unlink(kubeconfig_path)

    current_revision = await loop.run_in_executor(None, _call)
    return {
        "action": "helm_rollback",
        "namespace": namespace,
        "release_name": release_name,
        "revision": revision,
        "rolled_back": True,
        "rollback_data": {"rolled_back_from_revision": current_revision},
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Re-run helm_upgrade to the version that was active before this rollback.",
        "rolled_back_from": execution_result.get("rollback_data", {}).get("rolled_back_from_revision"),
    }
```

- [ ] **Step 11: Run tests — expect pass**

```bash
cd backend && python -m pytest app/tests/test_kubernetes_changes.py -v
```

Expected: all 8 tests pass.

- [ ] **Step 12: Update `kubernetes.json` catalog**

Add these 7 entries to the `"actions"` array in `backend/app/connectors/catalog/kubernetes.json`:

```json
    {"action_id": "restart_deployment", "generic_action": "restart_deployment", "action_type": "change", "execution_tier": 2, "display_name": "Restart Deployment", "description": "Perform a rolling restart by patching the restartedAt annotation. Records deployment resourceVersion for audit.", "applicable_asset_types": ["server"], "parameters": [{"name": "namespace", "type": "string", "required": true}, {"name": "deployment_name", "type": "string", "required": true}], "executor": "kubernetes.restart_deployment", "estimated_duration_seconds": 60, "blast_radius_hint": "pod_restart"},
    {"action_id": "scale_deployment", "generic_action": "scale_deployment", "action_type": "change", "execution_tier": 2, "display_name": "Scale Deployment", "description": "Set replica count for a deployment. Rollback restores previous replica count.", "applicable_asset_types": ["server"], "parameters": [{"name": "namespace", "type": "string", "required": true}, {"name": "deployment_name", "type": "string", "required": true}, {"name": "replicas", "type": "integer", "required": true, "description": "Target replica count (0 to scale to zero)"}], "executor": "kubernetes.scale_deployment", "rollback_action": "scale_deployment", "estimated_duration_seconds": 30},
    {"action_id": "apply_network_policy", "generic_action": "apply_network_policy", "action_type": "change", "execution_tier": 2, "display_name": "Apply Network Policy", "description": "Create or update a NetworkPolicy in a namespace. Snapshots existing policy for rollback.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "namespace", "type": "string", "required": true}, {"name": "manifest", "type": "string", "required": true, "description": "NetworkPolicy manifest as YAML or JSON string"}], "executor": "kubernetes.apply_network_policy", "rollback_action": "apply_network_policy", "estimated_duration_seconds": 10, "blast_radius_hint": "network_access_change"},
    {"action_id": "update_rbac", "generic_action": "update_rbac", "action_type": "change", "execution_tier": 2, "display_name": "Update RBAC Binding", "description": "Apply a RoleBinding or ClusterRoleBinding manifest (create or patch).", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "manifest", "type": "string", "required": true, "description": "RoleBinding or ClusterRoleBinding as YAML or JSON"}, {"name": "namespace", "type": "string", "required": false, "description": "Required for RoleBinding; omit for ClusterRoleBinding"}], "executor": "kubernetes.update_rbac", "estimated_duration_seconds": 10, "blast_radius_hint": "permission_change"},
    {"action_id": "rotate_secret", "generic_action": "rotate_secret", "action_type": "change", "execution_tier": 2, "display_name": "Rotate Kubernetes Secret", "description": "Update secret data and restart dependent deployments. Values are base64-encoded automatically.", "applicable_asset_types": ["cloud_account"], "parameters": [{"name": "namespace", "type": "string", "required": true}, {"name": "secret_name", "type": "string", "required": true}, {"name": "data", "type": "object", "required": true, "description": "Key-value pairs (plain strings; executor encodes to base64)"}, {"name": "restart_deployments", "type": "array", "required": false, "description": "Explicit deployment names to restart; auto-detected if omitted"}], "executor": "kubernetes.rotate_secret", "estimated_duration_seconds": 30, "blast_radius_hint": "credential_rotation", "safety_notes": ["Previous secret values are not stored in rollback_data for security. Store externally if rollback may be needed."]},
    {"action_id": "helm_upgrade", "generic_action": "helm_upgrade", "action_type": "change", "execution_tier": 2, "display_name": "Helm Upgrade", "description": "Upgrade a Helm release using --atomic --timeout 5m. Records previous revision for rollback.", "applicable_asset_types": ["server", "cloud_account"], "parameters": [{"name": "namespace", "type": "string", "required": true}, {"name": "release_name", "type": "string", "required": true}, {"name": "chart", "type": "string", "required": true}, {"name": "version", "type": "string", "required": false}, {"name": "values", "type": "object", "required": false}, {"name": "values_yaml", "type": "string", "required": false}, {"name": "dry_run", "type": "boolean", "required": false, "default": false}], "executor": "kubernetes.helm_upgrade", "rollback_action": "helm_rollback", "estimated_duration_seconds": 300, "blast_radius_hint": "service_version_change"},
    {"action_id": "helm_rollback", "generic_action": "helm_rollback", "action_type": "change", "execution_tier": 2, "display_name": "Helm Rollback", "description": "Roll back a Helm release to a previous revision.", "applicable_asset_types": ["server", "cloud_account"], "parameters": [{"name": "namespace", "type": "string", "required": true}, {"name": "release_name", "type": "string", "required": true}, {"name": "revision", "type": "integer", "required": false, "description": "Target revision; omit to roll back one revision"}], "executor": "kubernetes.helm_rollback", "estimated_duration_seconds": 120}
```

- [ ] **Step 13: Verify catalog**

```bash
cd backend && python -c "from app.connectors.catalog_service import get_catalog_service; svc = get_catalog_service(); print(len(svc._catalog.get('kubernetes', [])), 'kubernetes actions')"
```

Expected: `22 kubernetes actions` (15 existing + 7 new).

- [ ] **Step 14: Commit**

```bash
git add backend/app/connectors/executors/kubernetes/restart_deployment.py \
        backend/app/connectors/executors/kubernetes/scale_deployment.py \
        backend/app/connectors/executors/kubernetes/apply_network_policy.py \
        backend/app/connectors/executors/kubernetes/update_rbac.py \
        backend/app/connectors/executors/kubernetes/rotate_secret.py \
        backend/app/connectors/executors/kubernetes/helm_upgrade.py \
        backend/app/connectors/executors/kubernetes/helm_rollback.py \
        backend/app/connectors/catalog/kubernetes.json \
        backend/app/tests/test_kubernetes_changes.py
git commit -m "feat(kubernetes): add restart_deployment, scale_deployment, apply_network_policy, update_rbac, rotate_secret, helm_upgrade, helm_rollback"
```

---

## Task 6: Full test suite regression check

- [ ] **Step 1: Run all backend tests**

```bash
cd backend && python -m pytest app/tests/ -v --tb=short 2>&1 | tail -40
```

Expected: all tests pass. No regressions in existing connector or catalog service tests.

- [ ] **Step 2: Verify catalog service loads all connectors including new slack.json**

```bash
cd backend && python -c "
from app.connectors.catalog_service import get_catalog_service
svc = get_catalog_service()
connectors = sorted(svc._catalog.keys())
print('Connectors loaded:', len(connectors))
print('Slack present:', 'slack' in connectors)
print('Google Workspace change actions:', len([a for a in svc._catalog.get('google_workspace',[]) if a['action_type']=='change']))
print('GitHub actions:', len(svc._catalog.get('github',[])))
print('Kubernetes actions:', len(svc._catalog.get('kubernetes',[])))
print('Entra ID actions:', len(svc._catalog.get('entra_id',[])))
"
```

Expected output:
```
Connectors loaded: 38
Slack present: True
Google Workspace change actions: 11
GitHub actions: 17
Kubernetes actions: 22
Entra ID actions: 14
```

- [ ] **Step 3: Verify executor resolution works for each new action**

```bash
cd backend && python -c "
from app.connectors.catalog_service import get_catalog_service
svc = get_catalog_service()
new_actions = [
    ('google_workspace', 'remove_from_groups'),
    ('google_workspace', 'reset_2fa'),
    ('google_workspace', 'revoke_oauth_tokens'),
    ('google_workspace', 'wipe_mobile_device'),
    ('github', 'remove_org_member'),
    ('github', 'revoke_user_pats'),
    ('github', 'enforce_branch_protection'),
    ('github', 'archive_repo'),
    ('github', 'disable_actions'),
    ('github', 'enable_actions'),
    ('slack', 'deactivate_user'),
    ('slack', 'reactivate_user'),
    ('entra_id', 'remove_from_teams'),
    ('entra_id', 'assign_license'),
    ('entra_id', 'remove_license'),
    ('kubernetes', 'restart_deployment'),
    ('kubernetes', 'scale_deployment'),
    ('kubernetes', 'apply_network_policy'),
    ('kubernetes', 'update_rbac'),
    ('kubernetes', 'rotate_secret'),
    ('kubernetes', 'helm_upgrade'),
    ('kubernetes', 'helm_rollback'),
]
for connector, action in new_actions:
    mod = svc.get_executor(connector, action)
    assert hasattr(mod, 'execute'), f'{connector}.{action} missing execute()'
    assert hasattr(mod, 'rollback'), f'{connector}.{action} missing rollback()'
    print(f'OK {connector}.{action}')
print('All 22 new executors resolve correctly.')
"
```

Expected: `All 22 new executors resolve correctly.`

- [ ] **Step 4: Commit**

```bash
git add -p  # review any stray changes
git commit -m "test: verify all 22 new SaaS and Kubernetes change action executors resolve and pass mock tests"
```

---

## Self-Review Checklist

**Spec coverage:**

| Spec Section | Actions | Task |
|---|---|---|
| Section 1: Google Workspace | `suspend_user` ✓ (existed), `unsuspend_user` ✓ (existed), `remove_from_groups` ✓ (Task 1), `reset_2fa` ✓ (Task 1), `revoke_oauth_tokens` ✓ (Task 1), `wipe_mobile_device` ✓ (Task 1) | Task 1 |
| Section 2: GitHub | `remove_org_member` ✓ (Task 2), `revoke_user_pats` ✓ (Task 2), `enforce_branch_protection` ✓ (Task 2), `archive_repo` ✓ (Task 2), `disable_actions` ✓ (Task 2), `enable_actions` ✓ (Task 2) | Task 2 |
| Section 3: Slack | `deactivate_user` ✓ (Task 3), `reactivate_user` ✓ (Task 3) | Task 3 |
| Section 4: M365 | `disable_mailbox` ✓ (existed as `disable_user`), `remove_from_teams` ✓ (Task 4), `revoke_sessions` ✓ (existed), `assign_license` ✓ (Task 4), `remove_license` ✓ (Task 4) | Task 4 |
| Section 5: Kubernetes | `restart_deployment` ✓ (Task 5), `scale_deployment` ✓ (Task 5), `apply_network_policy` ✓ (Task 5), `update_rbac` ✓ (Task 5), `rotate_secret` ✓ (Task 5), `helm_upgrade` ✓ (Task 5), `helm_rollback` ✓ (Task 5) | Task 5 |
| Dependencies | `slack-sdk`, `google-auth-httplib2`, `helm` CLI | Task 0 |

**TDD compliance:** Each task writes the test file first (Step 1), verifies it fails with `ImportError` (Step 2), then implements the executors, then re-runs to confirm passing.

**Rollback coverage:** Every executor has a `rollback()` function. Where rollback is impossible (wipe_mobile_device, reset_2fa, revoke_user_pats, revoke_oauth_tokens, helm_rollback) `rollback()` returns `{"rolled_back": False, "reason": "..."}` with an explicit explanation.

**Mock safety:** All executors short-circuit on `not creds` with a mock response that satisfies the test assertions without network calls.

**No placeholder steps:** All code snippets are complete and runnable. Test assertions are specific and meaningful.
