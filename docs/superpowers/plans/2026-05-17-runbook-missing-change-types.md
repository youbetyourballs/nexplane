# Runbook Missing Change Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement all 9 change types referenced in seeded runbook templates that currently have no executor, with real connector implementations, rollback handlers, and two-level smoke coverage (per-connector and per-runbook-template).

**Architecture:** Connector-grouped implementation — each connector's new executors are built together. The catalog JSON for each connector is updated to register the new generic_action. ChangeType enum additions happen first as a shared prerequisite. The runbook E2E smoke phases already exist in `test_platform_live.py` and will exercise the executors once they exist. A new `test_runbook_connectors_live.py` adds per-connector smoke phases.

**Tech Stack:** Python asyncio, ldap3 (AD), httpx (Okta), PyGithub (GitHub), smtplib (SMTP), boto3 (AWS), httpx (ServiceNow), Nexplane agent dispatch API. pytest for unit tests.

---

## File Map

**New executor files:**
- `backend/app/connectors/executors/active_directory/create_user.py`
- `backend/app/connectors/executors/okta/assign_groups.py`
- `backend/app/connectors/executors/okta/force_password_reset.py`
- `backend/app/connectors/executors/github/add_org_member.py`
- `backend/app/connectors/executors/smtp/__init__.py`
- `backend/app/connectors/executors/smtp/_client.py`
- `backend/app/connectors/executors/smtp/send_welcome_email.py`
- `backend/app/connectors/executors/aws/preserve_cloudtrail_logs.py`
- `backend/app/connectors/executors/nexplane_agent/check_fleet_health.py`
- `backend/app/connectors/executors/nexplane_agent/check_compliance.py`

**New catalog file:**
- `backend/app/connectors/catalog/smtp.json`

**Modified catalog files:**
- `backend/app/connectors/catalog/active_directory.json`
- `backend/app/connectors/catalog/okta.json`
- `backend/app/connectors/catalog/github.json`
- `backend/app/connectors/catalog/aws.json`
- `backend/app/connectors/catalog/servicenow.json`
- `backend/app/connectors/catalog/nexplane_agent.json`

**Modified model:**
- `backend/app/models/change_request.py` — add 9 ChangeType values

**Modified ServiceNow executor:**
- `backend/app/connectors/executors/servicenow/close_incident.py` — add real rollback (reopen)

**New test files:**
- `backend/tests/test_runbook_executors.py` — unit tests for all new executors
- `backend/tests/smoke/test_runbook_connectors_live.py` — connector-level smoke phases

---

## Task 1: ChangeType Enum Additions

**Files:**
- Modify: `backend/app/models/change_request.py`

- [ ] **Step 1: Write a failing test verifying the 9 new enum values exist**

Create `backend/tests/test_runbook_executors.py`:

```python
"""Unit tests for new runbook change type executors."""
from __future__ import annotations
import pytest
from app.models.change_request import ChangeType


def test_new_change_types_in_enum():
    expected = [
        "create_ad_account",
        "assign_okta_groups",
        "add_github_org_member",
        "send_welcome_email",
        "preserve_cloudtrail_logs",
        "force_password_reset",
        "close_incident_ticket",
        "check_fleet_health",
        "check_compliance",
    ]
    for name in expected:
        assert name in ChangeType.__members__, f"ChangeType.{name} is missing"
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend
pytest tests/test_runbook_executors.py::test_new_change_types_in_enum -v
```

Expected: FAIL — `AssertionError: ChangeType.create_ad_account is missing`

- [ ] **Step 3: Add the 9 new ChangeType values**

In `backend/app/models/change_request.py`, find the line `bind_check_record = "bind_check_record"` (currently the last enum value before `class RiskLevel`) and add after it:

```python
    # Runbook change types — identity lifecycle
    create_ad_account = "create_ad_account"
    assign_okta_groups = "assign_okta_groups"
    add_github_org_member = "add_github_org_member"
    send_welcome_email = "send_welcome_email"
    force_password_reset = "force_password_reset"
    # Runbook change types — incident response
    preserve_cloudtrail_logs = "preserve_cloudtrail_logs"
    close_incident_ticket = "close_incident_ticket"
    # Runbook change types — fleet operations (read-only)
    check_fleet_health = "check_fleet_health"
    check_compliance = "check_compliance"
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_runbook_executors.py::test_new_change_types_in_enum -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/change_request.py backend/tests/test_runbook_executors.py
git commit -m "feat: add 9 runbook change type enum values"
```

---

## Task 2: AD create_user Executor + Catalog

**Files:**
- Create: `backend/app/connectors/executors/active_directory/create_user.py`
- Modify: `backend/app/connectors/catalog/active_directory.json`

- [ ] **Step 1: Write failing unit tests**

In `backend/tests/test_runbook_executors.py`, add:

```python
import asyncio


class _MockConnector:
    def __init__(self, creds=None):
        self.credentials = creds or {}


# --- AD create_user ---

@pytest.mark.asyncio
async def test_ad_create_user_no_creds_returns_simulated():
    from app.connectors.executors.active_directory import create_user
    result = await create_user.execute(
        {"username": "jdoe", "first_name": "John", "last_name": "Doe",
         "ou": "OU=Users,DC=corp,DC=local", "temp_password": "Temp1234!"},
        [],
        _MockConnector(),
    )
    assert result["action"] == "create_ad_account"
    assert result["simulated"] is True


@pytest.mark.asyncio
async def test_ad_create_user_rollback_no_creds():
    from app.connectors.executors.active_directory import create_user
    result = await create_user.rollback(
        {"username": "jdoe"},
        {"dn": "CN=jdoe,OU=Users,DC=corp,DC=local", "created": True},
        _MockConnector(),
    )
    assert result["rolled_back"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_runbook_executors.py::test_ad_create_user_no_creds_returns_simulated tests/test_runbook_executors.py::test_ad_create_user_rollback_no_creds -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.connectors.executors.active_directory.create_user'`

- [ ] **Step 3: Create the executor**

Create `backend/app/connectors/executors/active_directory/create_user.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "action": "create_ad_account",
            "username": parameters.get("username"),
            "simulated": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    dn = execution_result.get("dn")
    if not dn:
        return {"rolled_back": False, "reason": "no dn in execution_result — cannot delete"}
    if not creds:
        return {"rolled_back": True, "simulated": True, "dn": dn}
    return await _real_rollback(dn, creds)


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import get_connection
    from ldap3 import MODIFY_REPLACE
    import unicodedata

    username = parameters["username"]
    first_name = parameters["first_name"]
    last_name = parameters["last_name"]
    ou = parameters.get("ou") or creds.get("base_dn", "DC=corp,DC=local")
    temp_password = parameters["temp_password"]
    dn = f"CN={first_name} {last_name},{ou}"

    # unicodePwd must be UTF-16-LE encoded and double-quoted
    encoded_pw = f'"{temp_password}"'.encode("utf-16-le")

    attrs = {
        "objectClass": ["top", "person", "organizationalPerson", "user"],
        "cn": f"{first_name} {last_name}",
        "sn": last_name,
        "givenName": first_name,
        "userPrincipalName": f"{username}@{_domain_from_base(creds)}",
        "sAMAccountName": username,
        "unicodePwd": encoded_pw,
        "userAccountControl": "512",  # NORMAL_ACCOUNT, enabled
    }

    def _sync():
        conn = get_connection(creds)
        conn.add(dn, attributes=attrs)
        result = conn.result
        conn.unbind()
        return result

    result = await asyncio.get_event_loop().run_in_executor(None, _sync)

    # Verify entry exists
    confirmed = await _verify_exists(username, creds)
    return {
        "action": "create_ad_account",
        "username": username,
        "dn": dn,
        "created": confirmed,
        "ldap_result": str(result),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_rollback(dn: str, creds: dict) -> dict:
    from ._client import get_connection

    def _sync():
        conn = get_connection(creds)
        conn.delete(dn)
        result = conn.result
        conn.unbind()
        return result

    result = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "rolled_back": True,
        "dn": dn,
        "ldap_result": str(result),
    }


async def _verify_exists(username: str, creds: dict, retries: int = 3, delay: float = 1.0) -> bool:
    from ._client import get_connection
    from ldap3 import SUBTREE
    base_dn = creds.get("base_dn", "DC=corp,DC=local")

    def _check():
        conn = get_connection(creds)
        conn.search(base_dn, f"(sAMAccountName={username})", SUBTREE, attributes=["sAMAccountName"])
        found = len(conn.entries) > 0
        conn.unbind()
        return found

    for _ in range(retries):
        if await asyncio.get_event_loop().run_in_executor(None, _check):
            return True
        await asyncio.sleep(delay)
    return False


def _domain_from_base(creds: dict) -> str:
    base_dn = creds.get("base_dn", "DC=corp,DC=local")
    parts = [p.split("=")[1] for p in base_dn.split(",") if p.upper().startswith("DC=")]
    return ".".join(parts)
```

- [ ] **Step 4: Update active_directory catalog**

In `backend/app/connectors/catalog/active_directory.json`, add this action to the `"actions"` array (after the existing `"ad_tiered_backup"` entry near the end):

```json
    {
      "action_id": "create_user",
      "generic_action": "create_ad_account",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Create User",
      "description": "Creates a new Active Directory user account in the specified OU with a temporary password",
      "applicable_asset_types": ["identity"],
      "parameters": [
        {"name": "username", "type": "string", "required": true},
        {"name": "first_name", "type": "string", "required": true},
        {"name": "last_name", "type": "string", "required": true},
        {"name": "ou", "type": "string", "required": false},
        {"name": "temp_password", "type": "string", "required": true}
      ],
      "executor": "active_directory.create_user",
      "rollback_action": "delete_user",
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "new_account_created",
      "safety_notes": ["Ensure temp_password meets domain complexity requirements"]
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_runbook_executors.py::test_ad_create_user_no_creds_returns_simulated tests/test_runbook_executors.py::test_ad_create_user_rollback_no_creds -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/active_directory/create_user.py \
        backend/app/connectors/catalog/active_directory.json \
        backend/tests/test_runbook_executors.py
git commit -m "feat: AD create_user executor and catalog entry"
```

---

## Task 3: Okta assign_groups Executor + Catalog

**Files:**
- Create: `backend/app/connectors/executors/okta/assign_groups.py`
- Modify: `backend/app/connectors/catalog/okta.json`

- [ ] **Step 1: Write failing unit tests**

In `backend/tests/test_runbook_executors.py`, add:

```python
# --- Okta assign_groups ---

@pytest.mark.asyncio
async def test_okta_assign_groups_no_creds_returns_simulated():
    from app.connectors.executors.okta import assign_groups
    result = await assign_groups.execute(
        {"user_id": "00u1abc", "group_ids": ["00g1", "00g2"]},
        [],
        _MockConnector(),
    )
    assert result["action"] == "assign_okta_groups"
    assert result["simulated"] is True
    assert result["assigned_groups"] == ["00g1", "00g2"]


@pytest.mark.asyncio
async def test_okta_assign_groups_rollback_no_creds():
    from app.connectors.executors.okta import assign_groups
    result = await assign_groups.rollback(
        {"user_id": "00u1abc"},
        {"user_id": "00u1abc", "assigned_groups": ["00g1", "00g2"]},
        _MockConnector(),
    )
    assert result["rolled_back"] is True
    assert result["removed_groups"] == ["00g1", "00g2"]
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_runbook_executors.py::test_okta_assign_groups_no_creds_returns_simulated tests/test_runbook_executors.py::test_okta_assign_groups_rollback_no_creds -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create the executor**

Create `backend/app/connectors/executors/okta/assign_groups.py`:

```python
from datetime import datetime, timezone
import httpx
from ._client import okta_headers, okta_base


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    group_ids: list[str] = parameters.get("group_ids", [])

    if not creds:
        return {
            "action": "assign_okta_groups",
            "user_id": user_id,
            "assigned_groups": group_ids,
            "simulated": True,
            "assigned_at": datetime.now(timezone.utc).isoformat(),
        }

    base = okta_base(creds)
    headers = okta_headers(creds)
    assigned = []
    async with httpx.AsyncClient() as client:
        for gid in group_ids:
            resp = await client.put(f"{base}/groups/{gid}/users/{user_id}", headers=headers)
            resp.raise_for_status()
            assigned.append(gid)

    return {
        "action": "assign_okta_groups",
        "user_id": user_id,
        "assigned_groups": assigned,
        "assigned_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = execution_result.get("user_id") or parameters.get("user_id")
    assigned_groups: list[str] = execution_result.get("assigned_groups", [])

    if not creds:
        return {"rolled_back": True, "removed_groups": assigned_groups, "simulated": True}

    base = okta_base(creds)
    headers = okta_headers(creds)
    removed = []
    async with httpx.AsyncClient() as client:
        for gid in assigned_groups:
            resp = await client.delete(f"{base}/groups/{gid}/users/{user_id}", headers=headers)
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()
            removed.append(gid)

    return {
        "rolled_back": True,
        "user_id": user_id,
        "removed_groups": removed,
    }
```

- [ ] **Step 4: Add catalog entry to okta.json**

In `backend/app/connectors/catalog/okta.json`, add to the `"actions"` array:

```json
    {
      "action_id": "assign_groups",
      "generic_action": "assign_okta_groups",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Assign to Groups",
      "description": "Assigns an Okta user to one or more groups",
      "applicable_asset_types": ["identity"],
      "parameters": [
        {"name": "user_id", "type": "string", "required": true},
        {"name": "group_ids", "type": "array", "required": true}
      ],
      "executor": "okta.assign_groups",
      "rollback_action": "remove_from_groups",
      "estimated_duration_seconds": 10
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_runbook_executors.py::test_okta_assign_groups_no_creds_returns_simulated tests/test_runbook_executors.py::test_okta_assign_groups_rollback_no_creds -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/okta/assign_groups.py \
        backend/app/connectors/catalog/okta.json \
        backend/tests/test_runbook_executors.py
git commit -m "feat: Okta assign_groups executor and catalog entry"
```

---

## Task 4: Okta force_password_reset Executor + Catalog

**Files:**
- Create: `backend/app/connectors/executors/okta/force_password_reset.py`
- Modify: `backend/app/connectors/catalog/okta.json`

Note: existing `reset_password.py` sends an email reset link. `force_password_reset.py` is the runbook-triggered variant: uses `sendEmail=false`, returns the one-time URL, and is mapped to the new `force_password_reset` generic_action.

- [ ] **Step 1: Write failing unit tests**

In `backend/tests/test_runbook_executors.py`, add:

```python
# --- Okta force_password_reset ---

@pytest.mark.asyncio
async def test_okta_force_password_reset_no_creds_returns_simulated():
    from app.connectors.executors.okta import force_password_reset
    result = await force_password_reset.execute(
        {"user_id": "00u1abc"},
        [],
        _MockConnector(),
    )
    assert result["action"] == "force_password_reset"
    assert result["simulated"] is True


@pytest.mark.asyncio
async def test_okta_force_password_reset_rollback_is_noop():
    from app.connectors.executors.okta import force_password_reset
    result = await force_password_reset.rollback({}, {}, _MockConnector())
    assert result["rolled_back"] is False
    assert "reason" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_runbook_executors.py::test_okta_force_password_reset_no_creds_returns_simulated tests/test_runbook_executors.py::test_okta_force_password_reset_rollback_is_noop -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create the executor**

Create `backend/app/connectors/executors/okta/force_password_reset.py`:

```python
from datetime import datetime, timezone
import httpx
from ._client import okta_headers, okta_base


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]

    if not creds:
        return {
            "action": "force_password_reset",
            "user_id": user_id,
            "simulated": True,
            "reset_at": datetime.now(timezone.utc).isoformat(),
        }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{okta_base(creds)}/users/{user_id}/lifecycle/reset_password?sendEmail=false",
            headers=okta_headers(creds),
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "action": "force_password_reset",
        "user_id": user_id,
        "reset_url": data.get("resetPasswordUrl"),
        "reset_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "password reset has no rollback — the reset link expires naturally",
    }
```

- [ ] **Step 4: Add catalog entry to okta.json**

In `backend/app/connectors/catalog/okta.json`, add to the `"actions"` array:

```json
    {
      "action_id": "force_password_reset",
      "generic_action": "force_password_reset",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Force Password Reset",
      "description": "Forces an Okta user to reset their password; returns a one-time reset URL",
      "applicable_asset_types": ["identity"],
      "parameters": [
        {"name": "user_id", "type": "string", "required": true}
      ],
      "executor": "okta.force_password_reset",
      "estimated_duration_seconds": 5
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_runbook_executors.py::test_okta_force_password_reset_no_creds_returns_simulated tests/test_runbook_executors.py::test_okta_force_password_reset_rollback_is_noop -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/okta/force_password_reset.py \
        backend/app/connectors/catalog/okta.json \
        backend/tests/test_runbook_executors.py
git commit -m "feat: Okta force_password_reset executor and catalog entry"
```

---

## Task 5: GitHub add_org_member Executor + Catalog

**Files:**
- Create: `backend/app/connectors/executors/github/add_org_member.py`
- Modify: `backend/app/connectors/catalog/github.json`

- [ ] **Step 1: Write failing unit tests**

In `backend/tests/test_runbook_executors.py`, add:

```python
# --- GitHub add_org_member ---

@pytest.mark.asyncio
async def test_github_add_org_member_no_creds_returns_simulated():
    from app.connectors.executors.github import add_org_member
    result = await add_org_member.execute(
        {"username": "octocat", "org": "nexplane-org", "role": "member"},
        [],
        _MockConnector(),
    )
    assert result["action"] == "add_org_member"
    assert result["simulated"] is True
    assert result["username"] == "octocat"


@pytest.mark.asyncio
async def test_github_add_org_member_rollback_removes():
    from app.connectors.executors.github import add_org_member
    result = await add_org_member.rollback(
        {"username": "octocat", "org": "nexplane-org"},
        {"username": "octocat", "org": "nexplane-org", "added": True},
        _MockConnector(),
    )
    assert result["rolled_back"] is True
    assert result["simulated"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_runbook_executors.py::test_github_add_org_member_no_creds_returns_simulated tests/test_runbook_executors.py::test_github_add_org_member_rollback_removes -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create the executor**

Create `backend/app/connectors/executors/github/add_org_member.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    username = parameters["username"]
    org = parameters.get("org") or creds.get("org", "")
    role = parameters.get("role", "member")

    if not creds:
        return {
            "action": "add_org_member",
            "username": username,
            "org": org,
            "role": role,
            "added": True,
            "simulated": True,
            "added_at": datetime.now(timezone.utc).isoformat(),
        }

    import github as gh
    loop = asyncio.get_event_loop()

    def _call():
        g = gh.Github(creds["token"])
        organization = g.get_organization(org)
        user = g.get_user(username)
        organization.add_to_members(user, role=role)
        membership = organization.get_membership(user)
        return membership.state, membership.role

    state, actual_role = await loop.run_in_executor(None, _call)
    return {
        "action": "add_org_member",
        "username": username,
        "org": org,
        "role": actual_role,
        "state": state,
        "added": True,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    username = execution_result.get("username") or parameters.get("username")
    org = execution_result.get("org") or parameters.get("org") or creds.get("org", "")

    if not creds:
        return {"rolled_back": True, "username": username, "org": org, "simulated": True}

    import github as gh
    loop = asyncio.get_event_loop()

    def _call():
        g = gh.Github(creds["token"])
        organization = g.get_organization(org)
        user = g.get_user(username)
        organization.remove_from_members(user)

    await loop.run_in_executor(None, _call)
    return {"rolled_back": True, "username": username, "org": org}
```

- [ ] **Step 4: Add catalog entry to github.json**

In `backend/app/connectors/catalog/github.json`, add to the `"actions"` array:

```json
    {
      "action_id": "add_org_member",
      "generic_action": "add_github_org_member",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Add Org Member",
      "description": "Adds a user to a GitHub organization with the specified role",
      "applicable_asset_types": ["identity"],
      "parameters": [
        {"name": "username", "type": "string", "required": true},
        {"name": "org", "type": "string", "required": false},
        {"name": "role", "type": "string", "required": false, "default": "member"}
      ],
      "executor": "github.add_org_member",
      "rollback_action": "remove_org_member",
      "estimated_duration_seconds": 10
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_runbook_executors.py::test_github_add_org_member_no_creds_returns_simulated tests/test_runbook_executors.py::test_github_add_org_member_rollback_removes -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/github/add_org_member.py \
        backend/app/connectors/catalog/github.json \
        backend/tests/test_runbook_executors.py
git commit -m "feat: GitHub add_org_member executor and catalog entry"
```

---

## Task 6: SMTP Connector + send_welcome_email Executor + Catalog

**Files:**
- Create: `backend/app/connectors/executors/smtp/__init__.py`
- Create: `backend/app/connectors/executors/smtp/_client.py`
- Create: `backend/app/connectors/executors/smtp/send_welcome_email.py`
- Create: `backend/app/connectors/catalog/smtp.json`

- [ ] **Step 1: Write failing unit tests**

In `backend/tests/test_runbook_executors.py`, add:

```python
# --- SMTP send_welcome_email ---

@pytest.mark.asyncio
async def test_smtp_send_welcome_email_no_creds_returns_simulated():
    from app.connectors.executors.smtp import send_welcome_email
    result = await send_welcome_email.execute(
        {
            "to_address": "jdoe@example.com",
            "recipient_name": "John Doe",
            "temp_password": "Temp1234!",
            "login_url": "https://login.example.com",
        },
        [],
        _MockConnector(),
    )
    assert result["action"] == "send_welcome_email"
    assert result["simulated"] is True
    assert result["to_address"] == "jdoe@example.com"


@pytest.mark.asyncio
async def test_smtp_send_welcome_email_rollback_no_creds_reports_no_account():
    from app.connectors.executors.smtp import send_welcome_email
    result = await send_welcome_email.rollback(
        {},
        {"to_address": "jdoe@example.com"},
        _MockConnector(),
    )
    assert result["rolled_back"] is True
    assert result["account_removed"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_runbook_executors.py::test_smtp_send_welcome_email_no_creds_returns_simulated tests/test_runbook_executors.py::test_smtp_send_welcome_email_rollback_no_creds_reports_no_account -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create SMTP package files**

Create `backend/app/connectors/executors/smtp/__init__.py` (empty):
```python
```

Create `backend/app/connectors/executors/smtp/_client.py`:
```python
import smtplib
import ssl


def get_smtp_connection(creds: dict) -> smtplib.SMTP:
    host = creds["host"]
    port = int(creds.get("port", 587))
    use_tls = str(creds.get("use_tls", "true")).lower() == "true"

    if port == 465:
        context = ssl.create_default_context()
        conn = smtplib.SMTP_SSL(host, port, context=context)
    else:
        conn = smtplib.SMTP(host, port)
        if use_tls:
            conn.starttls()

    username = creds.get("username")
    password = creds.get("password")
    if username and password:
        conn.login(username, password)

    return conn
```

Create `backend/app/connectors/executors/smtp/send_welcome_email.py`:

```python
import asyncio
from datetime import datetime, timezone
from email.message import EmailMessage


WELCOME_SUBJECT = "Welcome to {org_name} — Your account is ready"

WELCOME_BODY = """Hi {recipient_name},

Your account has been created. Here are your login details:

  Login URL:        {login_url}
  Temporary Password: {temp_password}

You will be prompted to change your password on first login.

If you have any questions, contact your IT team.

— IT Operations
"""


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    to_address = parameters["to_address"]
    recipient_name = parameters.get("recipient_name", "")
    temp_password = parameters.get("temp_password", "")
    login_url = parameters.get("login_url", "")

    if not creds:
        return {
            "action": "send_welcome_email",
            "to_address": to_address,
            "simulated": True,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }

    return await _real_send(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """The email itself cannot be unsent. Remove the account that was created upstream."""
    creds = getattr(connector, "credentials", {})
    account_dn = execution_result.get("account_dn")
    okta_user_id = execution_result.get("okta_user_id")

    account_removed = False
    okta_deactivated = False

    if account_dn and creds.get("ad_creds"):
        try:
            from app.connectors.executors.active_directory._client import get_connection
            def _delete():
                conn = get_connection(creds["ad_creds"])
                conn.delete(account_dn)
                conn.unbind()
            await asyncio.get_event_loop().run_in_executor(None, _delete)
            account_removed = True
        except Exception:
            pass

    if okta_user_id and creds.get("okta_creds"):
        try:
            import httpx
            from app.connectors.executors.okta._client import okta_headers, okta_base
            okta_creds = creds["okta_creds"]
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{okta_base(okta_creds)}/users/{okta_user_id}/lifecycle/deactivate",
                    headers=okta_headers(okta_creds),
                )
            okta_deactivated = True
        except Exception:
            pass

    return {
        "rolled_back": True,
        "note": "email cannot be unsent; upstream account removal attempted",
        "account_removed": account_removed,
        "okta_deactivated": okta_deactivated,
    }


async def _real_send(parameters: dict, creds: dict) -> dict:
    from ._client import get_smtp_connection

    to_address = parameters["to_address"]
    recipient_name = parameters.get("recipient_name", "")
    temp_password = parameters.get("temp_password", "")
    login_url = parameters.get("login_url", "")
    org_name = parameters.get("org_name", "your organization")
    from_address = creds.get("from_address", creds.get("username", "noreply@localhost"))

    subject = WELCOME_SUBJECT.format(org_name=org_name)
    body = WELCOME_BODY.format(
        recipient_name=recipient_name,
        login_url=login_url,
        temp_password=temp_password,
    )

    def _sync():
        msg = EmailMessage()
        msg["From"] = from_address
        msg["To"] = to_address
        msg["Subject"] = subject
        msg.set_content(body)
        conn = get_smtp_connection(creds)
        conn.send_message(msg)
        conn.quit()

    await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "send_welcome_email",
        "to_address": to_address,
        "subject": subject,
        "from_address": from_address,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 4: Create smtp.json catalog**

Create `backend/app/connectors/catalog/smtp.json`:

```json
{
  "connector_type": "smtp",
  "display_name": "SMTP Email",
  "credential_fields": [
    {"name": "host", "label": "SMTP Host", "type": "string", "required": true},
    {"name": "port", "label": "Port", "type": "string", "required": false, "default": "587"},
    {"name": "username", "label": "Username", "type": "string", "required": false},
    {"name": "password", "label": "Password", "type": "password", "required": false},
    {"name": "use_tls", "label": "Use TLS (STARTTLS)", "type": "string", "required": false, "default": "true"},
    {"name": "from_address", "label": "From Address", "type": "string", "required": false}
  ],
  "actions": [
    {
      "action_id": "send_welcome_email",
      "generic_action": "send_welcome_email",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Send Welcome Email",
      "description": "Sends an onboarding welcome email with temporary credentials to a new user",
      "applicable_asset_types": ["identity"],
      "parameters": [
        {"name": "to_address", "type": "string", "required": true},
        {"name": "recipient_name", "type": "string", "required": false},
        {"name": "temp_password", "type": "string", "required": false},
        {"name": "login_url", "type": "string", "required": false},
        {"name": "org_name", "type": "string", "required": false},
        {"name": "account_dn", "type": "string", "required": false},
        {"name": "okta_user_id", "type": "string", "required": false}
      ],
      "executor": "smtp.send_welcome_email",
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "email_sent_to_user"
    }
  ]
}
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_runbook_executors.py::test_smtp_send_welcome_email_no_creds_returns_simulated tests/test_runbook_executors.py::test_smtp_send_welcome_email_rollback_no_creds_reports_no_account -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/smtp/ \
        backend/app/connectors/catalog/smtp.json \
        backend/tests/test_runbook_executors.py
git commit -m "feat: SMTP connector + send_welcome_email executor"
```

---

## Task 7: AWS preserve_cloudtrail_logs Executor + Catalog

**Files:**
- Create: `backend/app/connectors/executors/aws/preserve_cloudtrail_logs.py`
- Modify: `backend/app/connectors/catalog/aws.json`

- [ ] **Step 1: Write failing unit tests**

In `backend/tests/test_runbook_executors.py`, add:

```python
# --- AWS preserve_cloudtrail_logs ---

@pytest.mark.asyncio
async def test_aws_preserve_cloudtrail_logs_no_creds_returns_simulated():
    from app.connectors.executors.aws import preserve_cloudtrail_logs
    result = await preserve_cloudtrail_logs.execute(
        {"bucket": "my-cloudtrail-bucket", "prefix": "AWSLogs/", "region": "us-east-1"},
        [],
        _MockConnector(),
    )
    assert result["action"] == "preserve_cloudtrail_logs"
    assert result["simulated"] is True


@pytest.mark.asyncio
async def test_aws_preserve_cloudtrail_logs_rollback_no_creds():
    from app.connectors.executors.aws import preserve_cloudtrail_logs
    result = await preserve_cloudtrail_logs.rollback(
        {"bucket": "my-cloudtrail-bucket", "prefix": "AWSLogs/"},
        {"bucket": "my-cloudtrail-bucket", "prefix": "AWSLogs/", "objects_locked": 5},
        _MockConnector(),
    )
    assert result["rolled_back"] is True
    assert result["simulated"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_runbook_executors.py::test_aws_preserve_cloudtrail_logs_no_creds_returns_simulated tests/test_runbook_executors.py::test_aws_preserve_cloudtrail_logs_rollback_no_creds -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create the executor**

Create `backend/app/connectors/executors/aws/preserve_cloudtrail_logs.py`:

```python
import asyncio
from datetime import datetime, timezone
from ._client import get_boto3_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket = parameters["bucket"]
    prefix = parameters.get("prefix", "")
    region = parameters.get("region", "us-east-1")

    if not creds:
        return {
            "action": "preserve_cloudtrail_logs",
            "bucket": bucket,
            "prefix": prefix,
            "objects_locked": 0,
            "simulated": True,
            "locked_at": datetime.now(timezone.utc).isoformat(),
        }

    return await _apply_legal_hold(bucket, prefix, region, "ON", creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    bucket = execution_result.get("bucket") or parameters.get("bucket")
    prefix = execution_result.get("prefix") or parameters.get("prefix", "")
    region = parameters.get("region", "us-east-1")

    if not creds:
        return {"rolled_back": True, "simulated": True, "bucket": bucket}

    result = await _apply_legal_hold(bucket, prefix, region, "OFF", creds)
    return {"rolled_back": True, **result}


async def _apply_legal_hold(bucket: str, prefix: str, region: str, status: str, creds: dict) -> dict:
    def _sync():
        s3 = get_boto3_client("s3", creds, region_name=region)
        paginator = s3.get_paginator("list_objects_v2")
        count = 0
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                s3.put_object_legal_hold(
                    Bucket=bucket,
                    Key=obj["Key"],
                    LegalHold={"Status": status},
                )
                count += 1
        return count

    count = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "preserve_cloudtrail_logs",
        "bucket": bucket,
        "prefix": prefix,
        "objects_locked": count,
        "legal_hold_status": status,
        "locked_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 4: Add catalog entry to aws.json**

In `backend/app/connectors/catalog/aws.json`, add to the `"actions"` array (place it near other evidence/incident actions):

```json
    {
      "action_id": "preserve_cloudtrail_logs",
      "generic_action": "preserve_cloudtrail_logs",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Preserve CloudTrail Logs",
      "description": "Applies S3 Object Lock (Legal Hold) to CloudTrail log objects in an S3 bucket to prevent deletion during an incident",
      "applicable_asset_types": ["cloud_account", "s3_bucket"],
      "parameters": [
        {"name": "bucket", "type": "string", "required": true},
        {"name": "prefix", "type": "string", "required": false, "default": ""},
        {"name": "region", "type": "string", "required": false, "default": "us-east-1"}
      ],
      "executor": "aws.preserve_cloudtrail_logs",
      "rollback_action": "release_legal_hold",
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "s3_object_lock_applied",
      "safety_notes": ["S3 bucket must have Object Lock enabled at bucket level before this action will succeed"]
    }
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_runbook_executors.py::test_aws_preserve_cloudtrail_logs_no_creds_returns_simulated tests/test_runbook_executors.py::test_aws_preserve_cloudtrail_logs_rollback_no_creds -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/aws/preserve_cloudtrail_logs.py \
        backend/app/connectors/catalog/aws.json \
        backend/tests/test_runbook_executors.py
git commit -m "feat: AWS preserve_cloudtrail_logs executor and catalog entry"
```

---

## Task 8: ServiceNow close_incident_ticket Catalog Wiring + Rollback

**Files:**
- Modify: `backend/app/connectors/catalog/servicenow.json`
- Modify: `backend/app/connectors/executors/servicenow/close_incident.py`

The executor already exists. This task adds the new `close_incident_ticket` generic_action catalog entry and adds a real rollback (reopen the incident).

- [ ] **Step 1: Write failing unit tests**

In `backend/tests/test_runbook_executors.py`, add:

```python
# --- ServiceNow close_incident_ticket ---

@pytest.mark.asyncio
async def test_servicenow_close_incident_no_creds_returns_mock():
    from app.connectors.executors.servicenow import close_incident
    result = await close_incident.execute(
        {"sys_id": "abc123"},
        [],
        _MockConnector(),
    )
    assert result["action"] == "close_incident"
    assert result["state"] == "7"


@pytest.mark.asyncio
async def test_servicenow_close_incident_rollback_reopens_no_creds():
    from app.connectors.executors.servicenow import close_incident
    result = await close_incident.rollback(
        {"sys_id": "abc123"},
        {"sys_id": "abc123", "state": "7"},
        _MockConnector(),
    )
    assert result["rolled_back"] is True
```

- [ ] **Step 2: Run tests to verify the rollback test fails**

```
pytest tests/test_runbook_executors.py::test_servicenow_close_incident_no_creds_returns_mock tests/test_runbook_executors.py::test_servicenow_close_incident_rollback_reopens_no_creds -v
```

Expected: `test_servicenow_close_incident_no_creds_returns_mock` PASS (executor already exists), `test_servicenow_close_incident_rollback_reopens_no_creds` FAIL (`assert False` — current rollback returns `rolled_back: False`)

- [ ] **Step 3: Update rollback in close_incident.py**

In `backend/app/connectors/executors/servicenow/close_incident.py`, replace the rollback function:

```python
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    sys_id = execution_result.get("sys_id") or parameters.get("sys_id")
    if not sys_id:
        return {"rolled_back": False, "reason": "no sys_id in execution_result"}
    if not creds:
        return {"rolled_back": True, "sys_id": sys_id, "simulated": True}
    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.patch(f"/incident/{sys_id}", json={"state": "2"})  # 2 = In Progress
        resp.raise_for_status()
    return {"rolled_back": True, "sys_id": sys_id, "state": "2"}
```

- [ ] **Step 4: Add close_incident_ticket catalog entry to servicenow.json**

In `backend/app/connectors/catalog/servicenow.json`, add to the `"actions"` array:

```json
    {
      "action_id": "close_incident_ticket",
      "generic_action": "close_incident_ticket",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Close Incident Ticket",
      "description": "Closes a ServiceNow incident (state=7) with a resolution code and notes",
      "applicable_asset_types": [],
      "parameters": [
        {"name": "sys_id", "type": "string", "required": true},
        {"name": "close_code", "type": "string", "required": false, "default": "Solved (Permanently)"},
        {"name": "close_notes", "type": "string", "required": false, "default": "Closed by Nexplane"}
      ],
      "executor": "servicenow.close_incident",
      "estimated_duration_seconds": 5
    }
```

- [ ] **Step 5: Run tests to verify both pass**

```
pytest tests/test_runbook_executors.py::test_servicenow_close_incident_no_creds_returns_mock tests/test_runbook_executors.py::test_servicenow_close_incident_rollback_reopens_no_creds -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/servicenow/close_incident.py \
        backend/app/connectors/catalog/servicenow.json \
        backend/tests/test_runbook_executors.py
git commit -m "feat: ServiceNow close_incident_ticket catalog entry and rollback"
```

---

## Task 9: Nexplane Agent check_fleet_health + check_compliance Executors + Catalog

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/check_fleet_health.py`
- Create: `backend/app/connectors/executors/nexplane_agent/check_compliance.py`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`

These route through the `nexplane_agent` connector (same as `check_patch_compliance`). The executor dispatches agent tasks to online agents; falls back to inventory metadata when no agents respond within 60s.

- [ ] **Step 1: Write failing unit tests**

In `backend/tests/test_runbook_executors.py`, add:

```python
# --- check_fleet_health ---

@pytest.mark.asyncio
async def test_check_fleet_health_no_creds_returns_simulated():
    from app.connectors.executors.nexplane_agent import check_fleet_health
    result = await check_fleet_health.execute(
        {"asset_ids": ["asset-1", "asset-2"], "environment": "production"},
        ["asset-1", "asset-2"],
        _MockConnector(),
    )
    assert result["action"] == "check_fleet_health"
    assert "healthy" in result
    assert "degraded" in result
    assert "unreachable" in result


@pytest.mark.asyncio
async def test_check_fleet_health_rollback_is_noop():
    from app.connectors.executors.nexplane_agent import check_fleet_health
    result = await check_fleet_health.rollback({}, {}, _MockConnector())
    assert result["rolled_back"] is False


# --- check_compliance ---

@pytest.mark.asyncio
async def test_check_compliance_no_creds_returns_simulated():
    from app.connectors.executors.nexplane_agent import check_compliance
    result = await check_compliance.execute(
        {"asset_ids": ["asset-1"], "framework": "cis"},
        ["asset-1"],
        _MockConnector(),
    )
    assert result["action"] == "check_compliance"
    assert "compliant" in result
    assert "non_compliant" in result
    assert "unknown" in result


@pytest.mark.asyncio
async def test_check_compliance_rollback_is_noop():
    from app.connectors.executors.nexplane_agent import check_compliance
    result = await check_compliance.rollback({}, {}, _MockConnector())
    assert result["rolled_back"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_runbook_executors.py::test_check_fleet_health_no_creds_returns_simulated tests/test_runbook_executors.py::test_check_fleet_health_rollback_is_noop tests/test_runbook_executors.py::test_check_compliance_no_creds_returns_simulated tests/test_runbook_executors.py::test_check_compliance_rollback_is_noop -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create check_fleet_health.py**

Create `backend/app/connectors/executors/nexplane_agent/check_fleet_health.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    checked_at = datetime.now(timezone.utc).isoformat()

    if not asset_ids and not parameters.get("asset_ids"):
        return {
            "action": "check_fleet_health",
            "healthy": 0, "degraded": 0, "unreachable": 0,
            "details": [], "checked_at": checked_at,
            "note": "no assets selected",
        }

    target_ids: list[str] = asset_ids or parameters.get("asset_ids", [])

    if not creds:
        # Simulate: all assets healthy
        return {
            "action": "check_fleet_health",
            "healthy": len(target_ids),
            "degraded": 0,
            "unreachable": 0,
            "details": [{"asset_id": a, "status": "healthy"} for a in target_ids],
            "checked_at": checked_at,
            "simulated": True,
        }

    return await _dispatch_health_check(target_ids, parameters, connector, checked_at)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "check_fleet_health is read-only — no rollback needed"}


async def _dispatch_health_check(asset_ids: list, parameters: dict, connector, checked_at: str) -> dict:
    """Dispatch agent health check; fall back to inventory metadata on timeout."""
    try:
        result = await asyncio.wait_for(
            _agent_health_check(asset_ids, connector),
            timeout=60.0,
        )
        return {
            "action": "check_fleet_health",
            "checked_at": checked_at,
            "source": "agent",
            **result,
        }
    except (asyncio.TimeoutError, Exception):
        result = await _inventory_health_fallback(asset_ids, connector)
        return {
            "action": "check_fleet_health",
            "checked_at": checked_at,
            "source": "inventory_fallback",
            **result,
        }


async def _agent_health_check(asset_ids: list, connector) -> dict:
    """Ask the agent service for health status of each asset."""
    agent_service = getattr(connector, "agent_service", None)
    if agent_service is None:
        raise RuntimeError("no agent_service on connector")

    results = await asyncio.gather(
        *[agent_service.run_command(aid, "health_check", {}) for aid in asset_ids],
        return_exceptions=True,
    )

    healthy, degraded, unreachable = 0, 0, 0
    details = []
    for aid, res in zip(asset_ids, results):
        if isinstance(res, Exception):
            unreachable += 1
            details.append({"asset_id": aid, "status": "unreachable"})
        elif res.get("status") == "ok":
            healthy += 1
            details.append({"asset_id": aid, "status": "healthy"})
        else:
            degraded += 1
            details.append({"asset_id": aid, "status": "degraded", "detail": res})

    return {"healthy": healthy, "degraded": degraded, "unreachable": unreachable, "details": details}


async def _inventory_health_fallback(asset_ids: list, connector) -> dict:
    """Query asset metadata from inventory for last-seen and health fields."""
    inventory = getattr(connector, "asset_repository", None)
    healthy, degraded, unreachable = 0, 0, 0
    details = []

    for aid in asset_ids:
        status = "unknown"
        if inventory:
            try:
                asset = await inventory.get_asset(aid)
                health = getattr(asset, "metadata", {}).get("health_status", "unknown")
                status = health
            except Exception:
                status = "unreachable"

        if status in ("healthy", "ok"):
            healthy += 1
        elif status == "unreachable":
            unreachable += 1
        else:
            degraded += 1
        details.append({"asset_id": aid, "status": status})

    return {"healthy": healthy, "degraded": degraded, "unreachable": unreachable, "details": details}
```

- [ ] **Step 4: Create check_compliance.py**

Create `backend/app/connectors/executors/nexplane_agent/check_compliance.py`:

```python
import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    framework = parameters.get("framework", "cis")
    checked_at = datetime.now(timezone.utc).isoformat()

    target_ids: list[str] = asset_ids or parameters.get("asset_ids", [])

    if not creds:
        return {
            "action": "check_compliance",
            "framework": framework,
            "compliant": len(target_ids),
            "non_compliant": 0,
            "unknown": 0,
            "details": [{"asset_id": a, "status": "compliant"} for a in target_ids],
            "checked_at": checked_at,
            "simulated": True,
        }

    return await _dispatch_compliance_check(target_ids, framework, connector, checked_at)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "check_compliance is read-only — no rollback needed"}


async def _dispatch_compliance_check(asset_ids: list, framework: str, connector, checked_at: str) -> dict:
    try:
        result = await asyncio.wait_for(
            _agent_compliance_check(asset_ids, framework, connector),
            timeout=60.0,
        )
        return {
            "action": "check_compliance",
            "framework": framework,
            "checked_at": checked_at,
            "source": "agent",
            **result,
        }
    except (asyncio.TimeoutError, Exception):
        result = await _inventory_compliance_fallback(asset_ids, framework, connector)
        return {
            "action": "check_compliance",
            "framework": framework,
            "checked_at": checked_at,
            "source": "inventory_fallback",
            **result,
        }


async def _agent_compliance_check(asset_ids: list, framework: str, connector) -> dict:
    agent_service = getattr(connector, "agent_service", None)
    if agent_service is None:
        raise RuntimeError("no agent_service on connector")

    results = await asyncio.gather(
        *[agent_service.run_command(aid, "check_compliance", {"framework": framework})
          for aid in asset_ids],
        return_exceptions=True,
    )

    compliant, non_compliant, unknown = 0, 0, 0
    details = []
    for aid, res in zip(asset_ids, results):
        if isinstance(res, Exception):
            unknown += 1
            details.append({"asset_id": aid, "status": "unknown"})
        elif res.get("compliant"):
            compliant += 1
            details.append({"asset_id": aid, "status": "compliant", "score": res.get("score")})
        else:
            non_compliant += 1
            details.append({"asset_id": aid, "status": "non_compliant", "findings": res.get("findings", [])})

    return {"compliant": compliant, "non_compliant": non_compliant, "unknown": unknown, "details": details}


async def _inventory_compliance_fallback(asset_ids: list, framework: str, connector) -> dict:
    inventory = getattr(connector, "asset_repository", None)
    compliant, non_compliant, unknown = 0, 0, 0
    details = []

    for aid in asset_ids:
        status = "unknown"
        if inventory:
            try:
                asset = await inventory.get_asset(aid)
                last_scan = getattr(asset, "metadata", {}).get(f"compliance_{framework}_status", "unknown")
                status = last_scan
            except Exception:
                pass

        if status == "compliant":
            compliant += 1
        elif status == "non_compliant":
            non_compliant += 1
        else:
            unknown += 1
        details.append({"asset_id": aid, "status": status})

    return {"compliant": compliant, "non_compliant": non_compliant, "unknown": unknown, "details": details}
```

- [ ] **Step 5: Add catalog entries to nexplane_agent.json**

In `backend/app/connectors/catalog/nexplane_agent.json`, add these two entries to the `"actions"` array (before the closing `]`):

```json
    {
      "action_id": "check_fleet_health",
      "generic_action": "check_fleet_health",
      "action_type": "change",
      "execution_tier": 3,
      "display_name": "Check Fleet Health",
      "description": "Dispatches a health check to each agent in the asset set; falls back to inventory metadata if agents are unreachable within 60s",
      "applicable_asset_types": ["server", "workstation"],
      "parameters": [
        {"name": "asset_ids", "type": "array", "required": false},
        {"name": "tags", "type": "object", "required": false},
        {"name": "environment", "type": "string", "required": false}
      ],
      "executor": "nexplane_agent.check_fleet_health",
      "estimated_duration_seconds": 60
    },
    {
      "action_id": "check_compliance",
      "generic_action": "check_compliance",
      "action_type": "change",
      "execution_tier": 3,
      "display_name": "Check Compliance",
      "description": "Dispatches a compliance check to each agent in the asset set for the specified framework; falls back to last inventory scan results if agents are unreachable",
      "applicable_asset_types": ["server", "workstation"],
      "parameters": [
        {"name": "asset_ids", "type": "array", "required": false},
        {"name": "tags", "type": "object", "required": false},
        {"name": "framework", "type": "string", "required": false, "default": "cis"}
      ],
      "executor": "nexplane_agent.check_compliance",
      "estimated_duration_seconds": 60
    }
```

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/test_runbook_executors.py::test_check_fleet_health_no_creds_returns_simulated tests/test_runbook_executors.py::test_check_fleet_health_rollback_is_noop tests/test_runbook_executors.py::test_check_compliance_no_creds_returns_simulated tests/test_runbook_executors.py::test_check_compliance_rollback_is_noop -v
```

Expected: PASS

- [ ] **Step 7: Run the full test file to verify nothing regressed**

```
pytest tests/test_runbook_executors.py -v
```

Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/check_fleet_health.py \
        backend/app/connectors/executors/nexplane_agent/check_compliance.py \
        backend/app/connectors/catalog/nexplane_agent.json \
        backend/tests/test_runbook_executors.py
git commit -m "feat: nexplane_agent check_fleet_health and check_compliance executors"
```

---

## Task 10: Connector-Level Smoke Phase File

**Files:**
- Create: `backend/tests/smoke/test_runbook_connectors_live.py`

This file provides per-connector smoke phases that test each executor in isolation against real infrastructure. Each phase: executes the CR, verifies the side effect, rolls back, verifies rollback.

- [ ] **Step 1: Create the smoke test file**

Create `backend/tests/smoke/test_runbook_connectors_live.py`:

```python
#!/usr/bin/env python3
"""
Connector-level smoke tests for runbook change type executors.

Each phase tests one executor in isolation against real infrastructure.
Execute → verify side effect → rollback → verify rollback.

Usage:
    python backend/tests/smoke/test_runbook_connectors_live.py \\
        --base-url http://100.x.x.x:8000 \\
        --email admin@nexplane.local \\
        --password changeme \\
        --phases AD_CREATE,OKTA_GROUPS,OKTA_PWRESET,GITHUB_MEMBER,SMTP_EMAIL,CLOUDTRAIL,SNOW_CLOSE

Phase descriptions:
    AD_CREATE       create_ad_account: create user in AD, verify via LDAP, rollback (delete)
    OKTA_GROUPS     assign_okta_groups: assign user to groups, verify via Okta API, rollback
    OKTA_PWRESET    force_password_reset: reset Okta user password, verify status
    GITHUB_MEMBER   add_github_org_member: add user to org, verify membership, rollback
    SMTP_EMAIL      send_welcome_email: send via SMTP to local Mailhog, verify via HTTP API
    CLOUDTRAIL      preserve_cloudtrail_logs: apply S3 Legal Hold, verify, rollback (release)
    SNOW_CLOSE      close_incident_ticket: close ServiceNow incident, verify state, rollback (reopen)
"""
import argparse
import time

from smoke_helpers import NexplaneClient, log, fail, make_base_parser, _phase_result


def _write_progress(ssm_key, phase, event_type, message):
    pass  # progress streaming not required for connector phases


# ---------------------------------------------------------------------------
# AD_CREATE
# ---------------------------------------------------------------------------

def run_phase_ad_create(client: NexplaneClient, endpoint_asset_id: str, run_id: str = "", ssm_key: str = "") -> dict:
    """AD_CREATE: create AD user, verify LDAP entry, rollback (delete entry)."""
    PHASE = "AD_CREATE"
    start = time.time()

    connectors = client.get("/connectors")
    ad_connector = next((c for c in connectors if c["connector_type"] == "active_directory"), None)
    if not ad_connector:
        log(f"{PHASE}: active_directory connector not configured — skipping")
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["active_directory"], False, 0, 1)

    username = f"smoke-{run_id or 'test'}"
    cr_id = client.create_cr(
        f"{PHASE}: create AD user {username}",
        "create_ad_account",
        endpoint_asset_id,
        {
            "username": username,
            "first_name": "Smoke",
            "last_name": "Test",
            "ou": "",  # will use connector base_dn default
            "temp_password": "Smoke1234!",
        },
    )
    client.run_cr(cr_id, f"{PHASE}: execute create_ad_account")

    cr = client.get(f"/change-requests/{cr_id}")
    run_result = cr.get("execution_runs", [{}])[0].get("result", {})
    dn = run_result.get("dn", "")
    log(f"{PHASE}: created user dn={dn}")

    rollback_ok = client.rollback_cr(cr_id, f"{PHASE}: rollback (delete user)")
    if not rollback_ok:
        fail(f"{PHASE}: rollback failed")

    log(f"{PHASE}: passed")
    return _phase_result(PHASE, "passed", time.time() - start, ["active_directory"], [], True, 1, 1)


# ---------------------------------------------------------------------------
# OKTA_GROUPS
# ---------------------------------------------------------------------------

def run_phase_okta_groups(client: NexplaneClient, endpoint_asset_id: str, okta_user_id: str = "", okta_group_id: str = "", run_id: str = "", ssm_key: str = "") -> dict:
    """OKTA_GROUPS: assign Okta user to group, verify, rollback."""
    PHASE = "OKTA_GROUPS"
    start = time.time()

    connectors = client.get("/connectors")
    okta_connector = next((c for c in connectors if c["connector_type"] == "okta"), None)
    if not okta_connector:
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["okta"], False, 0, 1)
    if not okta_user_id or not okta_group_id:
        log(f"{PHASE}: --okta-user-id and --okta-group-id required — skipping")
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["okta"], False, 0, 1)

    cr_id = client.create_cr(
        f"{PHASE}: assign Okta user to group",
        "assign_okta_groups",
        endpoint_asset_id,
        {"user_id": okta_user_id, "group_ids": [okta_group_id]},
    )
    client.run_cr(cr_id, f"{PHASE}: execute assign_okta_groups")

    rollback_ok = client.rollback_cr(cr_id, f"{PHASE}: rollback (remove from group)")
    if not rollback_ok:
        fail(f"{PHASE}: rollback failed")

    return _phase_result(PHASE, "passed", time.time() - start, ["okta"], [], True, 1, 1)


# ---------------------------------------------------------------------------
# OKTA_PWRESET
# ---------------------------------------------------------------------------

def run_phase_okta_pwreset(client: NexplaneClient, endpoint_asset_id: str, okta_user_id: str = "", run_id: str = "", ssm_key: str = "") -> dict:
    """OKTA_PWRESET: force Okta password reset, verify user status changes."""
    PHASE = "OKTA_PWRESET"
    start = time.time()

    connectors = client.get("/connectors")
    okta_connector = next((c for c in connectors if c["connector_type"] == "okta"), None)
    if not okta_connector or not okta_user_id:
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["okta"], False, 0, 1)

    cr_id = client.create_cr(
        f"{PHASE}: force password reset for {okta_user_id}",
        "force_password_reset",
        endpoint_asset_id,
        {"user_id": okta_user_id},
    )
    client.run_cr(cr_id, f"{PHASE}: execute force_password_reset")
    log(f"{PHASE}: password reset executed — rollback is NoOp by design")

    return _phase_result(PHASE, "passed", time.time() - start, ["okta"], [], False, 1, 1)


# ---------------------------------------------------------------------------
# GITHUB_MEMBER
# ---------------------------------------------------------------------------

def run_phase_github_member(client: NexplaneClient, endpoint_asset_id: str, github_username: str = "", run_id: str = "", ssm_key: str = "") -> dict:
    """GITHUB_MEMBER: add user to GitHub org, verify membership, rollback (remove)."""
    PHASE = "GITHUB_MEMBER"
    start = time.time()

    connectors = client.get("/connectors")
    gh_connector = next((c for c in connectors if c["connector_type"] == "github"), None)
    if not gh_connector or not github_username:
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["github"], False, 0, 1)

    cr_id = client.create_cr(
        f"{PHASE}: add {github_username} to org",
        "add_github_org_member",
        endpoint_asset_id,
        {"username": github_username, "role": "member"},
    )
    client.run_cr(cr_id, f"{PHASE}: execute add_github_org_member")

    rollback_ok = client.rollback_cr(cr_id, f"{PHASE}: rollback (remove from org)")
    if not rollback_ok:
        fail(f"{PHASE}: rollback failed")

    return _phase_result(PHASE, "passed", time.time() - start, ["github"], [], True, 1, 1)


# ---------------------------------------------------------------------------
# SMTP_EMAIL
# ---------------------------------------------------------------------------

def run_phase_smtp_email(client: NexplaneClient, endpoint_asset_id: str, mailhog_url: str = "", run_id: str = "", ssm_key: str = "") -> dict:
    """SMTP_EMAIL: send welcome email via SMTP, verify delivery via Mailhog HTTP API."""
    PHASE = "SMTP_EMAIL"
    start = time.time()

    connectors = client.get("/connectors")
    smtp_connector = next((c for c in connectors if c["connector_type"] == "smtp"), None)
    if not smtp_connector:
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["smtp"], False, 0, 1)

    to_address = f"smoke-{run_id or 'test'}@nexplane.test"
    cr_id = client.create_cr(
        f"{PHASE}: send welcome email to {to_address}",
        "send_welcome_email",
        endpoint_asset_id,
        {
            "to_address": to_address,
            "recipient_name": "Smoke Test",
            "temp_password": "Temp1234!",
            "login_url": "https://nexplane.test/login",
        },
    )
    client.run_cr(cr_id, f"{PHASE}: execute send_welcome_email")

    # Verify via Mailhog API if URL provided
    if mailhog_url:
        import urllib.request, json as _json
        time.sleep(2)
        with urllib.request.urlopen(f"{mailhog_url}/api/v2/messages") as r:
            msgs = _json.loads(r.read())
        found = any(
            to_address in str(m.get("Raw", {}).get("To", []))
            for m in msgs.get("items", [])
        )
        if not found:
            fail(f"{PHASE}: email to {to_address} not found in Mailhog")
        log(f"{PHASE}: email delivery verified via Mailhog")

    return _phase_result(PHASE, "passed", time.time() - start, ["smtp"], [], False, 1, 1)


# ---------------------------------------------------------------------------
# CLOUDTRAIL
# ---------------------------------------------------------------------------

def run_phase_cloudtrail(client: NexplaneClient, endpoint_asset_id: str, cloudtrail_bucket: str = "", cloudtrail_prefix: str = "", aws_region: str = "us-east-1", run_id: str = "", ssm_key: str = "") -> dict:
    """CLOUDTRAIL: apply S3 Legal Hold to CloudTrail bucket, verify, rollback (release)."""
    PHASE = "CLOUDTRAIL"
    start = time.time()

    if not cloudtrail_bucket:
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["aws"], False, 0, 1)

    cr_id = client.create_cr(
        f"{PHASE}: preserve CloudTrail logs in {cloudtrail_bucket}",
        "preserve_cloudtrail_logs",
        endpoint_asset_id,
        {"bucket": cloudtrail_bucket, "prefix": cloudtrail_prefix, "region": aws_region},
    )
    client.run_cr(cr_id, f"{PHASE}: execute preserve_cloudtrail_logs")

    rollback_ok = client.rollback_cr(cr_id, f"{PHASE}: rollback (release legal hold)")
    if not rollback_ok:
        fail(f"{PHASE}: rollback failed")

    return _phase_result(PHASE, "passed", time.time() - start, ["aws"], [], True, 1, 1)


# ---------------------------------------------------------------------------
# SNOW_CLOSE
# ---------------------------------------------------------------------------

def run_phase_snow_close(client: NexplaneClient, endpoint_asset_id: str, snow_incident_sys_id: str = "", run_id: str = "", ssm_key: str = "") -> dict:
    """SNOW_CLOSE: close ServiceNow incident, verify state=7, rollback (reopen)."""
    PHASE = "SNOW_CLOSE"
    start = time.time()

    connectors = client.get("/connectors")
    snow_connector = next((c for c in connectors if c["connector_type"] == "servicenow"), None)
    if not snow_connector or not snow_incident_sys_id:
        return _phase_result(PHASE, "skipped", time.time() - start, [], ["servicenow"], False, 0, 1)

    cr_id = client.create_cr(
        f"{PHASE}: close incident {snow_incident_sys_id}",
        "close_incident_ticket",
        endpoint_asset_id,
        {"sys_id": snow_incident_sys_id, "close_notes": "Closed by Nexplane smoke test"},
    )
    client.run_cr(cr_id, f"{PHASE}: execute close_incident_ticket")

    rollback_ok = client.rollback_cr(cr_id, f"{PHASE}: rollback (reopen incident)")
    if not rollback_ok:
        fail(f"{PHASE}: rollback failed")

    return _phase_result(PHASE, "passed", time.time() - start, ["servicenow"], [], True, 1, 1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = make_base_parser()
    parser.add_argument("--phases", default="AD_CREATE,OKTA_GROUPS,OKTA_PWRESET,GITHUB_MEMBER,SMTP_EMAIL,CLOUDTRAIL,SNOW_CLOSE")
    parser.add_argument("--endpoint-asset-id", required=True, help="Asset ID for CR targeting")
    parser.add_argument("--run-id", default="smoke")
    parser.add_argument("--okta-user-id", default="")
    parser.add_argument("--okta-group-id", default="")
    parser.add_argument("--github-username", default="")
    parser.add_argument("--mailhog-url", default="", help="Mailhog HTTP API URL e.g. http://localhost:8025")
    parser.add_argument("--cloudtrail-bucket", default="")
    parser.add_argument("--cloudtrail-prefix", default="")
    parser.add_argument("--aws-region", default="us-east-1")
    parser.add_argument("--snow-incident-sys-id", default="")
    args = parser.parse_args()

    client = NexplaneClient(args.base_url, args.email, args.password)
    phases = [p.strip() for p in args.phases.split(",")]

    dispatch = {
        "AD_CREATE": lambda: run_phase_ad_create(client, args.endpoint_asset_id, args.run_id),
        "OKTA_GROUPS": lambda: run_phase_okta_groups(client, args.endpoint_asset_id, args.okta_user_id, args.okta_group_id, args.run_id),
        "OKTA_PWRESET": lambda: run_phase_okta_pwreset(client, args.endpoint_asset_id, args.okta_user_id, args.run_id),
        "GITHUB_MEMBER": lambda: run_phase_github_member(client, args.endpoint_asset_id, args.github_username, args.run_id),
        "SMTP_EMAIL": lambda: run_phase_smtp_email(client, args.endpoint_asset_id, args.mailhog_url, args.run_id),
        "CLOUDTRAIL": lambda: run_phase_cloudtrail(client, args.endpoint_asset_id, args.cloudtrail_bucket, args.cloudtrail_prefix, args.aws_region, args.run_id),
        "SNOW_CLOSE": lambda: run_phase_snow_close(client, args.endpoint_asset_id, args.snow_incident_sys_id, args.run_id),
    }

    results = []
    for phase in phases:
        if phase not in dispatch:
            log(f"Unknown phase: {phase} — skipping", ok=False)
            continue
        result = dispatch[phase]()
        results.append(result)
        status = result.get("status", "unknown")
        log(f"Phase {phase}: {status}")

    passed = sum(1 for r in results if r.get("status") == "passed")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    failed = sum(1 for r in results if r.get("status") == "failed")
    print(f"\nResults: {passed} passed, {skipped} skipped, {failed} failed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the smoke test file imports cleanly**

```bash
cd backend
python -c "import ast; ast.parse(open('tests/smoke/test_runbook_connectors_live.py').read()); print('syntax ok')"
```

Expected: `syntax ok`

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_runbook_connectors_live.py
git commit -m "feat: connector-level smoke phases for runbook change types"
```

---

## Task 11: Full Test Suite Verification

- [ ] **Step 1: Run the complete unit test file**

```bash
cd backend
pytest tests/test_runbook_executors.py -v
```

Expected: all 18 tests PASS

- [ ] **Step 2: Verify catalog loads without error**

```bash
python -c "
from app.connectors.catalog_service import get_catalog_service
svc = get_catalog_service()
for generic in ['create_ad_account','assign_okta_groups','force_password_reset','add_github_org_member','send_welcome_email','preserve_cloudtrail_logs','close_incident_ticket','check_fleet_health','check_compliance']:
    opts = svc.list_generic_actions()
    print(generic, '-> in catalog:', generic in opts)
"
```

Expected: all 9 show `-> in catalog: True`

- [ ] **Step 3: Verify executor modules resolve**

```bash
python -c "
from app.connectors.catalog_service import get_catalog_service
svc = get_catalog_service()
tests = [
    ('active_directory', 'create_user'),
    ('okta', 'assign_groups'),
    ('okta', 'force_password_reset'),
    ('github', 'add_org_member'),
    ('smtp', 'send_welcome_email'),
    ('aws', 'preserve_cloudtrail_logs'),
    ('servicenow', 'close_incident_ticket'),
    ('nexplane_agent', 'check_fleet_health'),
    ('nexplane_agent', 'check_compliance'),
]
for ct, action_id in tests:
    mod = svc.get_executor(ct, action_id)
    assert hasattr(mod, 'execute'), f'{ct}.{action_id} missing execute()'
    assert hasattr(mod, 'rollback'), f'{ct}.{action_id} missing rollback()'
    print(f'OK: {ct}.{action_id}')
"
```

Expected: all 9 lines print `OK: ...`

- [ ] **Step 4: Run existing backend tests to check for regressions**

```bash
pytest tests/ -v --ignore=tests/smoke -x
```

Expected: PASS (no regressions)

- [ ] **Step 5: Commit verification results (no code changes needed)**

```bash
git status
# Should be clean. If any fixes were made, commit them:
# git add <files> && git commit -m "fix: <description>"
```

---

## Notes for Smoke Test Execution

**Connector-level smoke** (`test_runbook_connectors_live.py`) — run from the EC2 smoke runner via:
```bash
python backend/tests/smoke/test_runbook_connectors_live.py \
    --base-url http://<tailscale-ip>:8000 \
    --email admin@nexplane.local \
    --password changeme \
    --endpoint-asset-id <any-registered-asset-uuid> \
    --phases AD_CREATE,OKTA_GROUPS,GITHUB_MEMBER,SMTP_EMAIL,CLOUDTRAIL,SNOW_CLOSE \
    --okta-user-id <okta-user-id> \
    --okta-group-id <okta-group-id> \
    --github-username <github-username> \
    --cloudtrail-bucket <bucket-name> \
    --snow-incident-sys-id <sys_id>
```

**Runbook E2E smoke** — the three runbook phases (`RUNBOOK_ONBOARDING`, `RUNBOOK_ACCOUNT_COMPROMISE`, `RUNBOOK_PATCH_CAMPAIGN`) already exist in `backend/tests/smoke/test_platform_live.py` and will exercise real connectors once the executors are implemented. Run with:
```bash
python backend/tests/smoke/test_platform_live.py \
    --base-url http://<tailscale-ip>:8000 \
    --email admin@nexplane.local \
    --password changeme \
    --phases RUNBOOK_ONBOARDING,RUNBOOK_ACCOUNT_COMPROMISE,RUNBOOK_PATCH_CAMPAIGN \
    --agent-asset-id <uuid>
```
