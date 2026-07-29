# Offboard User — Discovery + Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `offboard_user` with pre-execution account discovery (gates plan generation) and post-execution verification with exponential backoff retry, so the approval view shows only systems where the user exists and the report only runs after confirmed disables.

**Architecture:** Discovery runs async in `change_plan_service.plan_cr` before plan generation — queries every identity connector in the org for the target email, stores a manifest, then builds the plan with only found connectors. Verification runs as Phase 5 steps (one per connector), with an immediate check then exponential backoff (2s→4s→8s→16s→30s); hard fail if any connector still shows active. The report is Phase 6 and only runs if Phase 5 passes.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, ldap3, httpx, google-auth, pytest-asyncio.

## Global Constraints

- Existing step executor files (`disable_ad.py`, `revoke_okta.py`, `suspend_entra.py`, `suspend_google.py`, `remove_github.py`, `deactivate_slack.py`, `isolate_crowdstrike.py`) are NOT modified.
- Verification retry total wall-clock per connector must not exceed 60 seconds (delays: `[2, 4, 8, 16, 30]`).
- Hard fail on verification failure — Phase 6 report must not execute if any Phase 5 step fails.
- `plan_cr` must raise `PlanBlockedError` (not return an empty plan) if discovery finds zero accounts.
- FILO rollback order: Phase 4 → 3 → 2 → 1. Phases 5 and 6 have `rollback_action: None`.
- Smoke test runs against live AD (DC AMI `ami-058deb2fa3a1acc14`); other connectors optional.
- All new files start with the SPDX header: `# SPDX-License-Identifier: AGPL-3.0-only\n# Copyright (C) 2024-2026 Nexplane, Inc.`

---

### Task 1: `discover_accounts.py` — per-connector account discovery

**Files:**
- Create: `backend/app/connectors/executors/offboard_user/steps/discover_accounts.py`
- Create: `backend/app/tests/test_offboard_discover_accounts.py`

**Interfaces:**
- Consumes: nothing from prior tasks
- Produces: `execute(parameters: dict, connector) -> dict` where result always contains `{"action": "discover_account", "connector_type": str, "target_email": str, "found": bool, "account_identifier": str | None, "details": dict}`. `rollback(parameters, execution_result, connector) -> dict` returns `{"skipped": True}`. Task 3 reads `result["found"]` and `result["account_identifier"]` from this output.

- [ ] **Step 1: Write the failing tests**

```python
# backend/app/tests/test_offboard_discover_accounts.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from app.connectors.executors.offboard_user.steps import discover_accounts


def _connector_no_creds():
    class C:
        credentials = {}
    return C()


@pytest.mark.asyncio
async def test_no_creds_returns_not_found():
    result = await discover_accounts.execute(
        {"target_email": "alice@corp.com", "connector_type": "active_directory"},
        _connector_no_creds(),
    )
    assert result["found"] is False
    assert result["simulated"] is True
    assert result["connector_type"] == "active_directory"
    assert result["account_identifier"] is None


@pytest.mark.asyncio
async def test_unknown_connector_type_returns_not_found():
    result = await discover_accounts.execute(
        {"target_email": "alice@corp.com", "connector_type": "unknown_system"},
        _connector_no_creds(),
    )
    assert result["found"] is False
    assert "error" in result["details"]


@pytest.mark.asyncio
async def test_rollback_is_noop():
    result = await discover_accounts.rollback({}, {}, _connector_no_creds())
    assert result["skipped"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && python -m pytest app/tests/test_offboard_discover_accounts.py -v
```
Expected: `ModuleNotFoundError` or `AttributeError` — `discover_accounts` does not exist yet.

- [ ] **Step 3: Write `discover_accounts.py`**

```python
# backend/app/connectors/executors/offboard_user/steps/discover_accounts.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio


async def execute(parameters: dict, connector) -> dict:
    target_email = parameters["target_email"]
    connector_type = parameters["connector_type"]
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "discover_account",
            "connector_type": connector_type,
            "target_email": target_email,
            "found": False,
            "account_identifier": None,
            "details": {},
            "simulated": True,
        }

    handler = _HANDLERS.get(connector_type)
    if handler is None:
        return {
            "action": "discover_account",
            "connector_type": connector_type,
            "target_email": target_email,
            "found": False,
            "account_identifier": None,
            "details": {"error": f"No discovery handler for {connector_type}"},
        }

    return await handler(target_email, creds)


async def _discover_ad(email: str, creds: dict) -> dict:
    from app.connectors.executors.active_directory._client import get_connection

    base_dn = creds.get("base_dn", "DC=corp,DC=local")

    def _sync():
        conn = get_connection(creds)
        conn.search(base_dn, f"(mail={email})", attributes=["sAMAccountName", "displayName", "userAccountControl"])
        if not conn.entries:
            conn.search(base_dn, f"(userPrincipalName={email})", attributes=["sAMAccountName", "displayName", "userAccountControl"])
        if not conn.entries:
            conn.unbind()
            return None, {}
        entry = conn.entries[0]
        sam = entry.sAMAccountName.value
        details = {
            "displayName": str(entry.displayName) if entry.displayName else None,
            "userAccountControl": int(entry.userAccountControl.value),
        }
        conn.unbind()
        return sam, details

    sam, details = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "discover_account",
        "connector_type": "active_directory",
        "target_email": email,
        "found": sam is not None,
        "account_identifier": sam,
        "details": details,
    }


async def _discover_okta(email: str, creds: dict) -> dict:
    import httpx
    domain = creds.get("domain")
    api_token = creds.get("api_token")
    headers = {"Authorization": f"SSWS {api_token}", "Accept": "application/json"}

    async with httpx.AsyncClient(base_url=f"https://{domain}") as client:
        resp = await client.get(f"/api/v1/users/{email}", headers=headers)
        if resp.status_code == 404:
            return {"action": "discover_account", "connector_type": "okta", "target_email": email, "found": False, "account_identifier": None, "details": {}}
        resp.raise_for_status()
        user = resp.json()

    return {
        "action": "discover_account",
        "connector_type": "okta",
        "target_email": email,
        "found": True,
        "account_identifier": user["id"],
        "details": {"status": user.get("status"), "login": user.get("profile", {}).get("login")},
    }


async def _discover_entra(email: str, creds: dict) -> dict:
    import httpx
    tenant_id = creds.get("tenant_id")
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={"grant_type": "client_credentials", "client_id": client_id,
                  "client_secret": client_secret, "scope": "https://graph.microsoft.com/.default"},
        )
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(
            f"https://graph.microsoft.com/v1.0/users/{email}",
            headers=headers,
            params={"$select": "id,displayName,accountEnabled"},
        )
        if resp.status_code == 404:
            return {"action": "discover_account", "connector_type": "entra_id", "target_email": email, "found": False, "account_identifier": None, "details": {}}
        resp.raise_for_status()
        user = resp.json()

    return {
        "action": "discover_account",
        "connector_type": "entra_id",
        "target_email": email,
        "found": True,
        "account_identifier": user["id"],
        "details": {"displayName": user.get("displayName"), "accountEnabled": user.get("accountEnabled")},
    }


async def _discover_google(email: str, creds: dict) -> dict:
    import json
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    sa_info = json.loads(creds.get("service_account_json", "{}"))
    admin_email = creds.get("admin_email")
    credentials = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/admin.directory.user.readonly"],
        subject=admin_email,
    )

    def _sync():
        service = build("admin", "directory_v1", credentials=credentials)
        try:
            user = service.users().get(userKey=email).execute()
            return user
        except Exception as exc:
            if "404" in str(exc) or "Resource Not Found" in str(exc):
                return None
            raise

    user = await asyncio.get_event_loop().run_in_executor(None, _sync)
    if user is None:
        return {"action": "discover_account", "connector_type": "google_workspace", "target_email": email, "found": False, "account_identifier": None, "details": {}}

    return {
        "action": "discover_account",
        "connector_type": "google_workspace",
        "target_email": email,
        "found": True,
        "account_identifier": user["primaryEmail"],
        "details": {"suspended": user.get("suspended"), "fullName": user.get("name", {}).get("fullName")},
    }


async def _discover_github(email: str, creds: dict) -> dict:
    import httpx
    token = creds.get("token")
    org = creds.get("org")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    async with httpx.AsyncClient(base_url="https://api.github.com") as client:
        resp = await client.get(f"/search/users?q={email}+in:email", headers=headers)
        if resp.status_code != 200:
            return {"action": "discover_account", "connector_type": "github", "target_email": email, "found": False, "account_identifier": None, "details": {}}
        items = resp.json().get("items", [])
        if not items:
            return {"action": "discover_account", "connector_type": "github", "target_email": email, "found": False, "account_identifier": None, "details": {}}
        login = items[0]["login"]

        # Verify they are actually in this org
        member_resp = await client.get(f"/orgs/{org}/members/{login}", headers=headers)
        if member_resp.status_code != 204:
            return {"action": "discover_account", "connector_type": "github", "target_email": email, "found": False, "account_identifier": None, "details": {"reason": "not an org member"}}

    return {
        "action": "discover_account",
        "connector_type": "github",
        "target_email": email,
        "found": True,
        "account_identifier": login,
        "details": {"login": login, "org": org},
    }


async def _discover_slack(email: str, creds: dict) -> dict:
    import httpx
    token = creds.get("bot_token") or creds.get("token")
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url="https://slack.com/api") as client:
        resp = await client.get("/users.lookupByEmail", headers=headers, params={"email": email})
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            return {"action": "discover_account", "connector_type": "slack", "target_email": email, "found": False, "account_identifier": None, "details": {}}
        user = data["user"]

    return {
        "action": "discover_account",
        "connector_type": "slack",
        "target_email": email,
        "found": True,
        "account_identifier": user["id"],
        "details": {"name": user.get("name"), "deleted": user.get("deleted", False)},
    }


async def _discover_crowdstrike(email: str, creds: dict) -> dict:
    import httpx
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")
    base_url = creds.get("base_url", "https://api.crowdstrike.com")
    username = email.split("@")[0]

    async with httpx.AsyncClient(base_url=base_url) as client:
        token_resp = await client.post("/oauth2/token", data={"client_id": client_id, "client_secret": client_secret})
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(
            "/devices/queries/devices/v1",
            headers=headers,
            params={"filter": f"last_login_user:'{username}'"},
        )
        resp.raise_for_status()
        device_ids = resp.json().get("resources", [])

    found = len(device_ids) > 0
    return {
        "action": "discover_account",
        "connector_type": "crowdstrike",
        "target_email": email,
        "found": found,
        "account_identifier": ",".join(device_ids) if found else None,
        "details": {"device_count": len(device_ids), "device_ids": device_ids},
    }


_HANDLERS = {
    "active_directory": _discover_ad,
    "okta": _discover_okta,
    "entra_id": _discover_entra,
    "google_workspace": _discover_google,
    "github": _discover_github,
    "slack": _discover_slack,
    "crowdstrike": _discover_crowdstrike,
}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"action": "discover_account_rollback", "skipped": True, "reason": "discovery is read-only"}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && python -m pytest app/tests/test_offboard_discover_accounts.py -v
```
Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/offboard_user/steps/discover_accounts.py \
        backend/app/tests/test_offboard_discover_accounts.py
git commit -m "feat: offboard_user — per-connector account discovery step"
```

---

### Task 2: `verify_disabled.py` — per-connector verification with retry

**Files:**
- Create: `backend/app/connectors/executors/offboard_user/steps/verify_disabled.py`
- Create: `backend/app/tests/test_offboard_verify_disabled.py`

**Interfaces:**
- Consumes: `parameters["account_identifier"]` (from discovery result stored in plan, set in Task 3), `parameters["connector_type"]`
- Produces: `execute(parameters, connector) -> dict` where result contains `{"verified": bool, "connector_type": str, "attempts": int, "final_state": dict}`. If `verified=False`, also contains `"error": str`. Task 5 (smoke) checks `result["verified"]`.

- [ ] **Step 1: Write the failing tests**

```python
# backend/app/tests/test_offboard_verify_disabled.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, patch
from app.connectors.executors.offboard_user.steps import verify_disabled


def _connector_no_creds():
    class C:
        credentials = {}
    return C()


@pytest.mark.asyncio
async def test_no_creds_simulated_pass():
    result = await verify_disabled.execute(
        {"target_email": "alice@corp.com", "connector_type": "active_directory", "account_identifier": "alice"},
        _connector_no_creds(),
    )
    assert result["verified"] is True
    assert result["simulated"] is True


@pytest.mark.asyncio
async def test_unknown_connector_type_fails():
    class FakeCon:
        credentials = {"some": "cred"}
    result = await verify_disabled.execute(
        {"target_email": "alice@corp.com", "connector_type": "unknown_system", "account_identifier": "alice"},
        FakeCon(),
    )
    assert result["verified"] is False
    assert "error" in result


@pytest.mark.asyncio
async def test_rollback_is_noop():
    result = await verify_disabled.rollback({}, {}, _connector_no_creds())
    assert result["skipped"] is True


@pytest.mark.asyncio
async def test_ad_disabled_passes_on_first_check(monkeypatch):
    """_check_ad returns is_disabled=True immediately — verify passes with 1 attempt."""
    async def _mock_check(account_identifier, creds):
        return {"is_disabled": True, "userAccountControl": 514}

    monkeypatch.setattr(verify_disabled, "_CHECKERS", {"active_directory": _mock_check})
    monkeypatch.setattr(verify_disabled, "_DELAYS", [])  # skip sleep in unit test

    class FakeCon:
        credentials = {"base_dn": "DC=corp,DC=local"}

    result = await verify_disabled.execute(
        {"target_email": "alice@corp.com", "connector_type": "active_directory", "account_identifier": "alice"},
        FakeCon(),
    )
    assert result["verified"] is True
    assert result["attempts"] == 1


@pytest.mark.asyncio
async def test_verify_fails_after_all_retries(monkeypatch):
    """_check_ad always returns is_disabled=False — verify fails with error."""
    async def _mock_check(account_identifier, creds):
        return {"is_disabled": False, "userAccountControl": 512}

    monkeypatch.setattr(verify_disabled, "_CHECKERS", {"active_directory": _mock_check})
    monkeypatch.setattr(verify_disabled, "_DELAYS", [0, 0])  # instant retries in test

    class FakeCon:
        credentials = {"base_dn": "DC=corp,DC=local"}

    result = await verify_disabled.execute(
        {"target_email": "alice@corp.com", "connector_type": "active_directory", "account_identifier": "alice"},
        FakeCon(),
    )
    assert result["verified"] is False
    assert "still active" in result["error"]
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && python -m pytest app/tests/test_offboard_verify_disabled.py -v
```
Expected: `ModuleNotFoundError` or `AttributeError`.

- [ ] **Step 3: Write `verify_disabled.py`**

```python
# backend/app/connectors/executors/offboard_user/steps/verify_disabled.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

# Exposed as module-level so tests can monkeypatch it
_DELAYS = [2, 4, 8, 16, 30]


async def execute(parameters: dict, connector) -> dict:
    connector_type = parameters["connector_type"]
    account_identifier = parameters["account_identifier"]
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "verify_disabled",
            "connector_type": connector_type,
            "verified": True,
            "simulated": True,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }

    checker = _CHECKERS.get(connector_type)
    if checker is None:
        return {
            "action": "verify_disabled",
            "connector_type": connector_type,
            "verified": False,
            "error": f"No verification handler for {connector_type}",
        }

    # Immediate check first, then retry with delays
    for attempt, delay in enumerate(([0] + _DELAYS), start=1):
        if delay:
            await asyncio.sleep(delay)
        final_state = await checker(account_identifier, creds)
        if final_state.get("is_disabled"):
            return {
                "action": "verify_disabled",
                "connector_type": connector_type,
                "account_identifier": account_identifier,
                "verified": True,
                "attempts": attempt,
                "final_state": final_state,
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }

    return {
        "action": "verify_disabled",
        "connector_type": connector_type,
        "account_identifier": account_identifier,
        "verified": False,
        "attempts": len(_DELAYS) + 1,
        "final_state": final_state,
        "error": "Account still active after 60s retry window",
    }


async def _check_ad(sam: str, creds: dict) -> dict:
    from app.connectors.executors.active_directory._client import get_connection

    base_dn = creds.get("base_dn", "DC=corp,DC=local")

    def _sync():
        conn = get_connection(creds)
        conn.search(base_dn, f"(sAMAccountName={sam})", attributes=["userAccountControl"])
        if not conn.entries:
            conn.unbind()
            return None
        uac = int(conn.entries[0].userAccountControl.value)
        conn.unbind()
        return uac

    uac = await asyncio.get_event_loop().run_in_executor(None, _sync)
    if uac is None:
        return {"is_disabled": False, "error": f"User {sam} not found in AD"}
    return {"is_disabled": bool(uac & 2), "userAccountControl": uac}


async def _check_okta(user_id: str, creds: dict) -> dict:
    import httpx
    domain = creds.get("domain")
    api_token = creds.get("api_token")
    headers = {"Authorization": f"SSWS {api_token}", "Accept": "application/json"}

    async with httpx.AsyncClient(base_url=f"https://{domain}") as client:
        resp = await client.get(f"/api/v1/users/{user_id}", headers=headers)
        resp.raise_for_status()
        status = resp.json().get("status")

    return {"is_disabled": status in ("DEPROVISIONED", "SUSPENDED"), "status": status}


async def _check_entra(object_id: str, creds: dict) -> dict:
    import httpx
    tenant_id = creds.get("tenant_id")
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")

    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={"grant_type": "client_credentials", "client_id": client_id,
                  "client_secret": client_secret, "scope": "https://graph.microsoft.com/.default"},
        )
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]

        resp = await client.get(
            f"https://graph.microsoft.com/v1.0/users/{object_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"$select": "accountEnabled"},
        )
        resp.raise_for_status()
        account_enabled = resp.json().get("accountEnabled", True)

    return {"is_disabled": not account_enabled, "accountEnabled": account_enabled}


async def _check_google(primary_email: str, creds: dict) -> dict:
    import json
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    sa_info = json.loads(creds.get("service_account_json", "{}"))
    admin_email = creds.get("admin_email")
    credentials = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/admin.directory.user.readonly"],
        subject=admin_email,
    )

    def _sync():
        service = build("admin", "directory_v1", credentials=credentials)
        user = service.users().get(userKey=primary_email).execute()
        return user.get("suspended", False)

    suspended = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {"is_disabled": suspended, "suspended": suspended}


async def _check_github(login: str, creds: dict) -> dict:
    import httpx
    token = creds.get("token")
    org = creds.get("org")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    async with httpx.AsyncClient(base_url="https://api.github.com") as client:
        resp = await client.get(f"/orgs/{org}/members/{login}", headers=headers)
        # 404 = no longer a member (removed successfully), 204 = still active
        is_removed = resp.status_code == 404

    return {"is_disabled": is_removed, "membership_status": "removed" if is_removed else "active"}


async def _check_slack(member_id: str, creds: dict) -> dict:
    import httpx
    token = creds.get("bot_token") or creds.get("token")
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url="https://slack.com/api") as client:
        resp = await client.get("/users.info", headers=headers, params={"user": member_id})
        resp.raise_for_status()
        deleted = resp.json().get("user", {}).get("deleted", False)

    return {"is_disabled": deleted, "deleted": deleted}


async def _check_crowdstrike(device_ids_str: str, creds: dict) -> dict:
    import httpx
    device_ids = [d for d in device_ids_str.split(",") if d]
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")
    base_url = creds.get("base_url", "https://api.crowdstrike.com")

    async with httpx.AsyncClient(base_url=base_url) as client:
        token_resp = await client.post("/oauth2/token", data={"client_id": client_id, "client_secret": client_secret})
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(
            "/devices/entities/devices/v2",
            headers=headers,
            params=[("ids", d) for d in device_ids],
        )
        resp.raise_for_status()
        devices = resp.json().get("resources", [])
        all_isolated = all(d.get("status") == "contained" for d in devices)

    return {"is_disabled": all_isolated, "device_count": len(devices), "all_isolated": all_isolated}


_CHECKERS = {
    "active_directory": _check_ad,
    "okta": _check_okta,
    "entra_id": _check_entra,
    "google_workspace": _check_google,
    "github": _check_github,
    "slack": _check_slack,
    "crowdstrike": _check_crowdstrike,
}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"action": "verify_disabled_rollback", "skipped": True, "reason": "verification is read-only"}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend && python -m pytest app/tests/test_offboard_verify_disabled.py -v
```
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/offboard_user/steps/verify_disabled.py \
        backend/app/tests/test_offboard_verify_disabled.py
git commit -m "feat: offboard_user — verification step with exponential backoff retry"
```

---

### Task 3: Update `__init__.py` + `offboarding_report.py` — wire discovery into plan builder

**Files:**
- Modify: `backend/app/connectors/executors/offboard_user/__init__.py`
- Modify: `backend/app/connectors/executors/offboard_user/steps/offboarding_report.py`
- Modify: `backend/app/tests/test_offboard_build_plan.py`

**Interfaces:**
- Consumes: `resolved_connectors` items now include `"account_identifier": str | None` (set by Task 4 before calling `build_plan`)
- Produces: updated `build_plan(payload, resolved_connectors)` signature unchanged but now generates Phase 5 verify steps and Phase 6 report (was Phase 5). Task 4 calls this function.

- [ ] **Step 1: Update the unit tests first**

Replace the full contents of `backend/app/tests/test_offboard_build_plan.py`:

```python
# backend/app/tests/test_offboard_build_plan.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from app.connectors.executors.offboard_user import build_plan, DEFINITION


def _connector(ctype, account_identifier=None):
    return {
        "connector_id": uuid.uuid4(),
        "connector_type": ctype,
        "asset_id": uuid.uuid4(),
        "display_name": "Alice",
        "account_status": "active",
        "account_identifier": account_identifier or f"alice-{ctype}",
    }


@pytest.mark.asyncio
async def test_definition_has_required_keys():
    assert DEFINITION["name"] == "offboard_user"
    assert DEFINITION["rollback_supported"] is True


@pytest.mark.asyncio
async def test_build_plan_empty_connectors_still_has_report():
    """Empty discovered connectors still returns a report (Phase 6) step."""
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, [])
    assert len(steps) == 1
    assert steps[0]["action"] == "generate_offboarding_report"
    assert steps[0]["phase"] == 6


@pytest.mark.asyncio
async def test_build_plan_phases_ordered():
    connectors = [
        _connector("okta"),
        _connector("active_directory"),
        _connector("slack"),
    ]
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, connectors)
    phases = [s["phase"] for s in steps]
    assert phases == sorted(phases), "Steps must be ordered by phase"


@pytest.mark.asyncio
async def test_build_plan_report_is_last_and_phase_6():
    connectors = [_connector("okta"), _connector("active_directory")]
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, connectors)
    assert steps[-1]["action"] == "generate_offboarding_report"
    assert steps[-1]["phase"] == 6


@pytest.mark.asyncio
async def test_build_plan_verify_steps_in_phase_5():
    connectors = [
        _connector("active_directory", account_identifier="alice"),
        _connector("okta", account_identifier="okta-user-id-123"),
    ]
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, connectors)
    verify_steps = [s for s in steps if s["phase"] == 5]
    assert len(verify_steps) == 2
    verify_actions = [s["action"] for s in verify_steps]
    assert "verify_active_directory_disabled" in verify_actions
    assert "verify_okta_disabled" in verify_actions
    for vs in verify_steps:
        assert "account_identifier" in vs["parameters"]


@pytest.mark.asyncio
async def test_verify_steps_have_no_rollback():
    connectors = [_connector("active_directory", account_identifier="alice")]
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, connectors)
    verify_steps = [s for s in steps if s["phase"] == 5]
    for vs in verify_steps:
        assert vs["rollback_action"] is None


@pytest.mark.asyncio
async def test_build_plan_crowdstrike_only_when_requested():
    connectors = [_connector("crowdstrike", account_identifier="dev1,dev2")]
    payload = {"target_email": "alice@corp.com", "reason": "termination", "isolate_endpoints": False}
    steps = await build_plan(payload, connectors)
    actions = [s["action"] for s in steps]
    assert "isolate_crowdstrike_endpoints" not in actions


@pytest.mark.asyncio
async def test_build_plan_crowdstrike_verify_only_when_isolate_requested():
    connectors = [_connector("crowdstrike", account_identifier="dev1")]
    payload = {"target_email": "alice@corp.com", "reason": "termination", "isolate_endpoints": True}
    steps = await build_plan(payload, connectors)
    verify_steps = [s for s in steps if s["phase"] == 5]
    assert any("crowdstrike" in s["action"] for s in verify_steps)
```

- [ ] **Step 2: Run updated tests to verify they fail**

```
cd backend && python -m pytest app/tests/test_offboard_build_plan.py -v
```
Expected: `test_build_plan_empty_connectors_still_has_report` fails (current code returns `phase=5` for report); `test_build_plan_verify_steps_in_phase_5` fails (no verify steps exist yet); `test_build_plan_report_is_last_and_phase_6` fails.

- [ ] **Step 3: Update `__init__.py`**

Replace the full contents of `backend/app/connectors/executors/offboard_user/__init__.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Offboard User change type definition and plan builder.

Phase ordering:
  Phase 1 — Session revocation (Okta, Entra ID, Google Workspace) — parallel
  Phase 2 — Account disable (AD, Okta, Entra ID, Google Workspace) — parallel
  Phase 3 — Workspace/org removal (GitHub, Slack) — parallel
  Phase 4 — Endpoint isolation (CrowdStrike) — sequential, opt-in only
  Phase 5 — Verification (one step per connector that ran in phases 1–4)
  Phase 6 — Offboarding report — always last
"""

DEFINITION = {
    "name": "offboard_user",
    "display_name": "Offboard User",
    "description": (
        "Disable a user across all connected identity systems in a single "
        "coordinated change request. One step is generated per connector "
        "that has an account for the target email address."
    ),
    "payload_schema": "OffboardUserPayload",
    "rollback_supported": True,
    "parallel_steps": False,
}

_SESSION_REVOKE_TYPES = {"okta", "entra_id", "google_workspace"}
_ACCOUNT_DISABLE_TYPES = {"active_directory", "okta", "entra_id", "google_workspace"}
_REMOVAL_TYPES = {"github", "slack"}
# Connector types that have verification support in verify_disabled.py
_VERIFY_TYPES = {"active_directory", "okta", "entra_id", "google_workspace", "github", "slack", "crowdstrike"}


async def build_plan(payload: dict, resolved_connectors: list[dict]) -> list[dict]:
    """
    Returns a list of step dicts (not Pydantic models, for SQLite test compatibility).
    Each dict: {name, action, connector_id, parameters, phase, rollback_action, status}

    resolved_connectors items must include:
      connector_id, connector_type, asset_id, account_identifier (from discovery)
    """
    steps = []
    # Track which connectors ran action steps (for phase 5 verify generation)
    _action_connectors: list[dict] = []

    # Phase 1: session revocation
    for c in resolved_connectors:
        if c["connector_type"] in _SESSION_REVOKE_TYPES:
            steps.append({
                "name": f"Revoke {c['connector_type']} sessions",
                "action": f"revoke_{c['connector_type']}_sessions",
                "connector_id": str(c["connector_id"]),
                "parameters": {
                    "target_email": payload["target_email"],
                    "asset_id": str(c["asset_id"]) if c.get("asset_id") else None,
                },
                "phase": 1,
                "rollback_action": None,
                "status": "pending",
            })

    # Phase 2: account disable
    for c in resolved_connectors:
        if c["connector_type"] in _ACCOUNT_DISABLE_TYPES:
            steps.append({
                "name": f"Disable {c['connector_type']} account",
                "action": f"disable_{c['connector_type']}_account",
                "connector_id": str(c["connector_id"]),
                "parameters": {
                    "target_email": payload["target_email"],
                    "asset_id": str(c["asset_id"]) if c.get("asset_id") else None,
                },
                "phase": 2,
                "rollback_action": f"enable_{c['connector_type']}_account",
                "status": "pending",
            })
            _action_connectors.append(c)

    # Phase 3: removal from collaborative tools
    for c in resolved_connectors:
        if c["connector_type"] in _REMOVAL_TYPES:
            steps.append({
                "name": f"Remove from {c['connector_type']}",
                "action": f"remove_{c['connector_type']}_member",
                "connector_id": str(c["connector_id"]),
                "parameters": {
                    "target_email": payload["target_email"],
                    "asset_id": str(c["asset_id"]) if c.get("asset_id") else None,
                },
                "phase": 3,
                "rollback_action": f"reinstate_{c['connector_type']}_member",
                "status": "pending",
            })
            _action_connectors.append(c)

    # Phase 4: CrowdStrike isolation (opt-in)
    if payload.get("isolate_endpoints"):
        for c in resolved_connectors:
            if c["connector_type"] == "crowdstrike":
                steps.append({
                    "name": "Isolate CrowdStrike-managed endpoints",
                    "action": "isolate_crowdstrike_endpoints",
                    "connector_id": str(c["connector_id"]),
                    "parameters": {
                        "target_email": payload["target_email"],
                    },
                    "phase": 4,
                    "rollback_action": "lift_crowdstrike_isolation",
                    "status": "pending",
                })
                _action_connectors.append(c)

    # Phase 5: verification (one step per connector that had an action step)
    seen_verify = set()
    for c in _action_connectors:
        ct = c["connector_type"]
        conn_id = str(c["connector_id"])
        if conn_id in seen_verify or ct not in _VERIFY_TYPES:
            continue
        seen_verify.add(conn_id)
        steps.append({
            "name": f"Verify {ct} account disabled",
            "action": f"verify_{ct}_disabled",
            "connector_id": conn_id,
            "parameters": {
                "target_email": payload["target_email"],
                "connector_type": ct,
                "account_identifier": c.get("account_identifier"),
            },
            "phase": 5,
            "rollback_action": None,
            "status": "pending",
        })

    # Phase 6: report (always last)
    steps.append({
        "name": "Generate offboarding report",
        "action": "generate_offboarding_report",
        "connector_id": None,
        "parameters": {
            "target_email": payload["target_email"],
            "reason": payload.get("reason"),
            "notify_manager": payload.get("notify_manager", True),
            "manager_email": payload.get("manager_email"),
            "discovery_manifest": payload.get("_discovery_manifest", []),
        },
        "phase": 6,
        "rollback_action": None,
        "status": "pending",
    })

    return steps
```

- [ ] **Step 4: Update `offboarding_report.py`** to include verification results

Replace the full contents of `backend/app/connectors/executors/offboard_user/steps/offboarding_report.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Generate structured offboarding report including discovery manifest and verification results."""
    target_email = parameters["target_email"]
    reason = parameters.get("reason", "unspecified")
    notify_manager = parameters.get("notify_manager", True)
    manager_email = parameters.get("manager_email")
    discovery_manifest = parameters.get("discovery_manifest", [])
    completed_at = datetime.now(timezone.utc).isoformat()

    report = {
        "report_type": "offboarding",
        "target_email": target_email,
        "reason": reason,
        "completed_at": completed_at,
        "manager_notified": False,
        "discovery_manifest": discovery_manifest,
        "systems_offboarded": [
            r["connector_type"] for r in discovery_manifest if r.get("found")
        ],
    }

    if notify_manager and manager_email:
        report["manager_notified"] = True
        report["manager_email"] = manager_email

    return {
        "action": "generate_offboarding_report",
        "report": report,
        "executed_at": completed_at,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"action": "offboarding_report_rollback", "skipped": True, "reason": "reports are not reversible"}
```

- [ ] **Step 5: Run tests to verify they pass**

```
cd backend && python -m pytest app/tests/test_offboard_build_plan.py -v
```
Expected: all 8 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/offboard_user/__init__.py \
        backend/app/connectors/executors/offboard_user/steps/offboarding_report.py \
        backend/app/tests/test_offboard_build_plan.py
git commit -m "feat: offboard_user — Phase 5 verify steps + Phase 6 report with discovery manifest"
```

---

### Task 4: Update `change_plan_service.py` — discovery hook + offboard plan integration

**Files:**
- Modify: `backend/app/services/change_plan_service.py`
- Create: `backend/app/tests/test_offboard_plan_cr.py`

**Interfaces:**
- Consumes: `discover_accounts.execute` (Task 1), `build_plan` (Task 3)
- Produces: `plan_cr` now runs discovery for `offboard_user` CRs and raises `PlanBlockedError` if zero accounts found; builds `ChangePlanData` with multi-step plan. The smoke test (Task 5) calls `POST /change-requests/{id}/plan` and observes the resulting plan.

- [ ] **Step 1: Write the failing tests**

```python
# backend/app/tests/test_offboard_plan_cr.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.change_plan_service import PlanBlockedError
from app.services import change_plan_service


def _make_cr(target_email="alice@corp.com"):
    cr = MagicMock()
    cr.change_type = MagicMock()
    cr.change_type.value = "offboard_user"
    cr.change_type.__eq__ = lambda self, other: str(other) == "offboard_user" or getattr(other, "value", None) == "offboard_user"
    cr.desired_outcome = {"target_email": target_email, "reason": "termination"}
    cr.organization_id = uuid.uuid4()
    cr.id = uuid.uuid4()
    cr.target_asset_ids = []
    return cr


@pytest.mark.asyncio
async def test_run_offboard_discovery_no_connectors(monkeypatch):
    """With no matching connectors in DB, discovery returns empty list."""
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=mock_result)

    manifest = await change_plan_service._run_offboard_discovery(db, "alice@corp.com", uuid.uuid4())
    assert manifest == []


@pytest.mark.asyncio
async def test_run_offboard_discovery_returns_manifest(monkeypatch):
    """With one AD connector, discovery calls discover_accounts.execute and returns manifest."""
    db = AsyncMock()

    fake_connector = MagicMock()
    fake_connector.id = uuid.uuid4()
    fake_connector.connector_type = MagicMock()
    fake_connector.connector_type.value = "active_directory"
    fake_connector.credentials = {"base_dn": "DC=corp,DC=local"}

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [fake_connector]
    db.execute = AsyncMock(return_value=mock_result)

    async def _mock_discover(params, connector):
        return {"found": True, "account_identifier": "alice", "connector_type": "active_directory", "details": {}}

    monkeypatch.setattr(
        "app.services.change_plan_service._discover_account_on_connector",
        _mock_discover,
    )

    manifest = await change_plan_service._run_offboard_discovery(db, "alice@corp.com", fake_connector.id)
    assert len(manifest) == 1
    assert manifest[0]["found"] is True
    assert manifest[0]["account_identifier"] == "alice"
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && python -m pytest app/tests/test_offboard_plan_cr.py -v
```
Expected: `AttributeError` — `_run_offboard_discovery` doesn't exist yet.

- [ ] **Step 3: Add the discovery hook to `change_plan_service.py`**

After the existing imports, add at the top of the file (after existing imports):

```python
from app.models.change_request import ChangeType  # add to existing import if needed

_OFFBOARD_DISCOVERY_TYPES = {
    "active_directory", "okta", "entra_id", "google_workspace",
    "github", "slack", "crowdstrike",
}
```

Add these two new functions before `plan_cr`:

```python
async def _discover_account_on_connector(parameters: dict, connector) -> dict:
    """Call discover_accounts.execute for a single connector. Isolated for testability."""
    from app.connectors.executors.offboard_user.steps import discover_accounts
    return await discover_accounts.execute(parameters, connector)


async def _run_offboard_discovery(db: AsyncSession, target_email: str, organization_id) -> list[dict]:
    """Query all identity connectors in the org and discover whether target_email has an account."""
    from app.models.connector import Connector, ConnectorType
    from sqlalchemy import select

    result = await db.execute(
        select(Connector).where(
            Connector.organization_id == organization_id,
            Connector.connector_type.in_([
                ConnectorType(t) for t in _OFFBOARD_DISCOVERY_TYPES
            ]),
        )
    )
    connectors = list(result.scalars().all())

    manifest = []
    for connector in connectors:
        ct = connector.connector_type.value if hasattr(connector.connector_type, "value") else str(connector.connector_type)
        try:
            discovery_result = await _discover_account_on_connector(
                {"target_email": target_email, "connector_type": ct},
                connector,
            )
            manifest.append({
                "connector_id": str(connector.id),
                "connector_type": ct,
                "found": discovery_result.get("found", False),
                "account_identifier": discovery_result.get("account_identifier"),
                "details": discovery_result.get("details", {}),
            })
        except Exception as exc:
            log.warning("Discovery failed for connector %s (%s): %s", connector.id, ct, exc)
            manifest.append({
                "connector_id": str(connector.id),
                "connector_type": ct,
                "found": False,
                "account_identifier": None,
                "details": {"error": str(exc)},
            })

    return manifest
```

Then modify `plan_cr` to handle `offboard_user` before calling `generate_plan`. Add this block after the asset loading and safety check, immediately before `plan_data = generate_plan(cr, assets, safety_result)`:

```python
    # offboard_user: run discovery before plan generation, build plan directly
    from app.models.change_request import ChangeType as _CT
    if cr.change_type == _CT.offboard_user:
        desired = cr.desired_outcome or {}
        target_email = desired.get("target_email")
        if not target_email:
            raise PlanBlockedError(["offboard_user requires 'target_email' in desired_outcome"])

        discovery_manifest = await _run_offboard_discovery(db, target_email, cr.organization_id)
        discovered = [r for r in discovery_manifest if r.get("found")]

        if not discovered:
            n = len(discovery_manifest)
            raise PlanBlockedError([
                f"No accounts found for {target_email} across {n} connected system(s). "
                "Ensure identity connectors are configured and credentials are valid."
            ])

        # Resolved connectors for build_plan — only found ones, with account_identifier
        resolved_connectors = [
            {
                "connector_id": r["connector_id"],
                "connector_type": r["connector_type"],
                "asset_id": None,
                "account_identifier": r["account_identifier"],
            }
            for r in discovered
        ]

        # Inject discovery manifest so report step can include it
        payload = {**desired, "_discovery_manifest": discovery_manifest}

        from app.connectors.executors.offboard_user import build_plan as _offboard_build_plan
        from app.models.change_plan import ChangePlan, PlanGeneratedBy
        from app.models.change_request import ChangeRequestStatus
        import asyncio as _asyncio

        generated_steps = await _offboard_build_plan(payload, resolved_connectors)

        from app.services.planning_engine import ChangePlanData, _calculate_blast_radius
        plan_data = ChangePlanData(
            generated_steps=generated_steps,
            preflight_checks=[],
            blast_radius=_calculate_blast_radius(cr, assets, safety_result, steps=generated_steps),
            rollback_plan={
                "rollback_capability": "full",
                "filo_order": "phases 4→3→2→1; phases 5 and 6 have no rollback",
            },
            verification_plan={"phase": 5, "checks": ["account_disabled_per_connector"]},
        )

        if cr.change_plan:
            plan = cr.change_plan
            plan.generated_steps = plan_data.generated_steps
            plan.preflight_checks = plan_data.preflight_checks
            plan.blast_radius = plan_data.blast_radius
            plan.rollback_plan = plan_data.rollback_plan
            plan.verification_plan = plan_data.verification_plan
        else:
            plan = ChangePlan(
                change_request_id=cr.id,
                generated_steps=plan_data.generated_steps,
                preflight_checks=plan_data.preflight_checks,
                blast_radius=plan_data.blast_radius,
                rollback_plan=plan_data.rollback_plan,
                verification_plan=plan_data.verification_plan,
                generated_by=PlanGeneratedBy.system,
            )
            db.add(plan)

        cr.risk_level = safety_result.risk_level
        cr.status = ChangeRequestStatus.planned
        cr.updated_at = datetime.now(timezone.utc)
        await db.flush()

        return PlanResult(
            plan=plan,
            risk_level=safety_result.risk_level.value,
            risk_score=float(safety_result.risk_score),
            risk_factors=[f.name for f in safety_result.risk_factors],
            warnings=list(safety_result.warnings or []),
            blocking_issues=[],
        )
```

The `plan_cr` function then continues with its existing code (the `plan_data = generate_plan(...)` line and everything after) for all other change types — the `offboard_user` path returns early above.

- [ ] **Step 4: Check that `ChangePlanData` and `_calculate_blast_radius` are importable from `planning_engine`**

```
cd backend && python -c "from app.services.planning_engine import ChangePlanData, _calculate_blast_radius; print('OK')"
```

If `ChangePlanData` is not exported from `planning_engine`, find where it's defined:
```
grep -n "class ChangePlanData\|ChangePlanData" backend/app/services/planning_engine.py | head -5
```
Use that import path instead.

- [ ] **Step 5: Run unit tests**

```
cd backend && python -m pytest app/tests/test_offboard_plan_cr.py app/tests/test_offboard_build_plan.py app/tests/test_offboard_discover_accounts.py app/tests/test_offboard_verify_disabled.py -v
```
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/change_plan_service.py \
        backend/app/tests/test_offboard_plan_cr.py
git commit -m "feat: offboard_user — discovery hook in plan_cr, build multi-step plan from found accounts"
```

---

### Task 5: Smoke test — live AD lifecycle with discovery + verification

**Files:**
- Create: `backend/tests/smoke/test_offboard_user_smoke.py`

**Interfaces:**
- Consumes: all prior tasks; live DC AMI `ami-058deb2fa3a1acc14` (same SSM key `_DC_AMI_SSM_KEY = "/nexplane/smoke-amis/dc-smoke-prebuilt/2019"` as `test_ad_tier_zero_cr_smoke.py`)
- Produces: passing smoke phases that prove end-to-end lifecycle works

**Context:** The smoke test boots a Windows DC from the cached AMI, creates a test AD user via LDAP, creates an `offboard_user` CR, runs the full lifecycle (create→plan→submit→approve→execute), asserts the discovery manifest found the AD user, asserts Phase 5 verification passed, asserts the AD account is disabled via direct LDAP query, then rolls back and confirms the account is re-enabled. The test tears down the DC instance at the end.

- [ ] **Step 1: Write the smoke test**

```python
# backend/tests/smoke/test_offboard_user_smoke.py
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""
Live smoke test: offboard_user CR — discovery + verification lifecycle.

Boots a Windows 2019 DC from cached AMI, creates a test AD user, runs
offboard_user CR (create→plan→submit→approve→execute), verifies discovery
found the user, verification passed, AD account is actually disabled, then
rolls back and confirms re-enabled.

Run:
    docker exec nexplane-backend-1 python -m pytest \
        /app/tests/smoke/test_offboard_user_smoke.py -v -s
"""

import os
import sys
import time
import uuid

import boto3
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from smoke_helpers import NexplaneClient, log, get_connector_creds_from_db

BASE_URL = os.environ.get("NEXPLANE_BASE_URL", "http://localhost:8000")
EMAIL = os.environ.get("NEXPLANE_EMAIL", "admin@acme.example")
PASSWORD = os.environ.get("NEXPLANE_PASSWORD", "admin123")

_DC_AMI_SSM_KEY = "/nexplane/smoke-amis/dc-smoke-prebuilt/2019"
_DC_ADMIN_PASSWORD = "SmokeTest1234!"
_DC_DOMAIN = "smoke.nexplane.local"
_DC_NETBIOS = "SMOKE"
_SSM_PROFILE = "nexplane-smoke-ssm"

CR_TIMEOUT = 300
POLL_INTERVAL = 10

_client: NexplaneClient = None
_state: dict = {}


def _get_client() -> NexplaneClient:
    global _client
    if _client is None:
        _client = NexplaneClient(BASE_URL, EMAIL, PASSWORD)
    return _client


def _api(method, path, **kwargs):
    return getattr(_get_client(), method)(path, **kwargs)


def _poll_cr(cr_id, timeout_s=CR_TIMEOUT, interval_s=POLL_INTERVAL):
    terminal = ("completed", "failed", "rolled_back", "rolled_back_with_warnings")
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        cr = _api("get", f"/change-requests/{cr_id}")
        if cr["status"] in terminal:
            return cr
        time.sleep(interval_s)
    raise TimeoutError(f"CR {cr_id} did not reach terminal status in {timeout_s}s")


def _execution_result(cr: dict) -> dict:
    runs = cr.get("execution_runs") or []
    if runs:
        run_result = runs[0].get("result") or {}
        steps = run_result.get("execution", {}).get("steps", [])
        if steps:
            return steps[0].get("result") or {}
    return {}


def _step_results(cr: dict) -> list[dict]:
    """Return all step results from execution_runs."""
    runs = cr.get("execution_runs") or []
    if runs:
        run_result = runs[0].get("result") or {}
        return run_result.get("execution", {}).get("steps", [])
    return []


class TestOffboardUserSmoke:

    @classmethod
    def setup_class(cls):
        log("=== offboard_user smoke: setup ===")

        # --- Provision DC from cached AMI ---
        aws_creds = get_connector_creds_from_db("aws")
        if not aws_creds:
            pytest.skip("No AWS connector credentials — cannot provision DC")

        ssm = boto3.client("ssm", region_name="us-east-1",
                           aws_access_key_id=aws_creds["access_key_id"],
                           aws_secret_access_key=aws_creds["secret_access_key"])
        try:
            ami_id = ssm.get_parameter(Name=_DC_AMI_SSM_KEY)["Parameter"]["Value"]
            log(f"Using cached DC AMI: {ami_id}")
        except ssm.exceptions.ParameterNotFound:
            pytest.skip(f"No cached DC AMI at SSM {_DC_AMI_SSM_KEY}")

        ec2 = boto3.client("ec2", region_name="us-east-1",
                           aws_access_key_id=aws_creds["access_key_id"],
                           aws_secret_access_key=aws_creds["secret_access_key"])

        sg_id = aws_creds.get("smoke_sg_id", "sg-08891be3823c0e4ce")
        subnet_id = aws_creds.get("smoke_subnet_id")
        run_kwargs = {
            "ImageId": ami_id,
            "InstanceType": "t3.medium",
            "MinCount": 1, "MaxCount": 1,
            "SecurityGroupIds": [sg_id],
            "TagSpecifications": [{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": "smoke-offboard-dc"}]}],
        }
        if subnet_id:
            run_kwargs["SubnetId"] = subnet_id

        resp = ec2.run_instances(**run_kwargs)
        instance_id = resp["Instances"][0]["InstanceId"]
        _state["instance_id"] = instance_id
        _state["ec2"] = ec2
        log(f"DC instance launched: {instance_id}")

        # Wait for running + private IP
        waiter = ec2.get_waiter("instance_running")
        waiter.wait(InstanceIds=[instance_id])
        desc = ec2.describe_instances(InstanceIds=[instance_id])
        private_ip = desc["Reservations"][0]["Instances"][0]["PrivateIpAddress"]
        _state["private_ip"] = private_ip
        log(f"DC private IP: {private_ip}")

        # Wait for WinRM (port 5985)
        import socket
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            try:
                s = socket.create_connection((private_ip, 5985), timeout=5)
                s.close()
                log("WinRM port 5985 open")
                break
            except OSError:
                time.sleep(10)
        else:
            pytest.fail(f"WinRM never opened on {private_ip}:5985 after 360s")

        # --- Register AD connector ---
        ad_creds_db = get_connector_creds_from_db("active_directory")
        if ad_creds_db:
            conn_id = ad_creds_db["connector_id"]
        else:
            # Create new connector
            conn = _api("post", "/connectors", json={"name": "smoke-offboard-ad", "connector_type": "active_directory"})
            conn_id = conn["id"]

        base_dn = ",".join(f"DC={part}" for part in _DC_DOMAIN.split("."))
        bind_dn = f"CN=Administrator,CN=Users,{base_dn}"
        _api("put", f"/connectors/{conn_id}/credentials", json={
            "credentials": {
                "server": private_ip, "port": "389",
                "base_dn": base_dn, "bind_dn": bind_dn,
                "bind_password": _DC_ADMIN_PASSWORD, "use_ssl": "false",
                "winrm_hostname": private_ip, "winrm_port": "5985",
                "winrm_username": f"{_DC_NETBIOS}\\Administrator",
                "winrm_password": _DC_ADMIN_PASSWORD, "winrm_use_ssl": "false",
            }
        })
        _state["conn_id"] = conn_id
        _state["base_dn"] = base_dn

        # --- Create test AD user via LDAP ---
        import asyncio
        from ldap3 import Server, Connection, ALL, MODIFY_REPLACE, SUBTREE

        run_id = uuid.uuid4().hex[:6]
        test_user_sam = f"smoke-{run_id}"
        test_user_email = f"{test_user_sam}@{_DC_DOMAIN}"
        test_user_upn = test_user_email
        test_user_dn = f"CN={test_user_sam},CN=Users,{base_dn}"

        def _create_user():
            srv = Server(private_ip, port=389, get_info=ALL)
            conn = Connection(srv, user=bind_dn, password=_DC_ADMIN_PASSWORD, auto_bind=True)
            conn.add(test_user_dn, ["top", "person", "organizationalPerson", "user"], {
                "sAMAccountName": test_user_sam,
                "userPrincipalName": test_user_upn,
                "mail": test_user_email,
                "displayName": f"Smoke Offboard {run_id}",
                "userAccountControl": 512,  # enabled, normal account
            })
            result = conn.result
            conn.unbind()
            return result

        create_result = _create_user()
        assert create_result.get("result") == 0, f"Failed to create AD user: {create_result}"
        _state["test_user_sam"] = test_user_sam
        _state["test_user_email"] = test_user_email
        _state["test_user_dn"] = test_user_dn
        log(f"Created test AD user: {test_user_sam} ({test_user_email})")

    @classmethod
    def teardown_class(cls):
        log("=== offboard_user smoke: teardown ===")
        ec2 = _state.get("ec2")
        instance_id = _state.get("instance_id")
        if ec2 and instance_id:
            try:
                ec2.terminate_instances(InstanceIds=[instance_id])
                log(f"Terminated DC instance {instance_id}")
            except Exception as exc:
                log(f"WARNING: failed to terminate {instance_id}: {exc}")

    def test_phase1_discovery_accuracy(self):
        """Plan-only: discovery manifest must find the test AD user with correct sAMAccountName."""
        log("Phase 1: discovery accuracy")

        cr = _api("post", "/change-requests", json={
            "title": f"smoke-offboard-discovery-{uuid.uuid4().hex[:6]}",
            "change_type": "offboard_user",
            "target_asset_ids": [],
            "desired_outcome": {
                "summary": "Smoke: offboard_user discovery phase",
                "target_email": _state["test_user_email"],
                "reason": "smoke_test",
            },
        })
        cr_id = cr["id"]
        _state["phase1_cr_id"] = cr_id

        # Plan only (does not execute)
        plan = _api("post", f"/change-requests/{cr_id}/plan")

        # The plan steps should include an AD disable step
        steps = plan.get("generated_steps", [])
        ad_steps = [s for s in steps if "active_directory" in (s.get("connector_type", "") or s.get("action", ""))]
        assert len(ad_steps) > 0, f"No AD steps in plan. Steps: {[s.get('action') for s in steps]}"

        # Verify step for AD should be present with account_identifier set
        verify_steps = [s for s in steps if s.get("phase") == 5]
        ad_verify = [s for s in verify_steps if "active_directory" in s.get("action", "")]
        assert len(ad_verify) == 1, f"Expected 1 AD verify step, got {ad_verify}"
        assert ad_verify[0]["parameters"].get("account_identifier") == _state["test_user_sam"], \
            f"Expected account_identifier={_state['test_user_sam']!r}, got {ad_verify[0]['parameters'].get('account_identifier')!r}"

        log(f"Phase 1 PASSED: discovery found {_state['test_user_sam']} in AD")

    def test_phase2_full_lifecycle_with_verification(self):
        """Full lifecycle: create→plan→submit→approve→execute. Assert verification passes, AD disabled, rollback re-enables."""
        log("Phase 2: full lifecycle with verification")

        cr = _api("post", "/change-requests", json={
            "title": f"smoke-offboard-full-{uuid.uuid4().hex[:6]}",
            "change_type": "offboard_user",
            "target_asset_ids": [],
            "desired_outcome": {
                "summary": "Smoke: offboard_user full lifecycle",
                "target_email": _state["test_user_email"],
                "reason": "smoke_test_termination",
            },
        })
        cr_id = cr["id"]

        _api("post", f"/change-requests/{cr_id}/plan")
        _api("post", f"/change-requests/{cr_id}/submit-for-approval")
        _api("post", f"/change-requests/{cr_id}/approve",
             json={"decision": "approved", "comment": "offboard smoke"})
        _api("post", f"/change-requests/{cr_id}/execute")

        cr = _poll_cr(cr_id)
        assert cr["status"] == "completed", f"CR failed: {cr.get('failure_reason')}"

        # Assert Phase 5 verification steps passed
        step_results = _step_results(cr)
        verify_results = [s for s in step_results if "verify" in (s.get("action", "") or "")]
        assert len(verify_results) > 0, "No verification steps found in execution result"
        for vr in verify_results:
            result = vr.get("result", {})
            assert result.get("verified") is True, \
                f"Verification failed for {result.get('connector_type')}: {result.get('error')}"

        # Assert the report was generated (Phase 6)
        report_results = [s for s in step_results if "report" in (s.get("action", "") or "")]
        assert len(report_results) == 1
        report = report_results[0].get("result", {}).get("report", {})
        assert "discovery_manifest" in report
        assert _state["test_user_email"] in report.get("target_email", "")

        # Assert AD account actually disabled via direct LDAP
        self._assert_ad_account_disabled(_state["test_user_sam"])

        # Rollback
        _api("post", f"/change-requests/{cr_id}/rollback")
        cr = _poll_cr(cr_id)
        assert cr["status"] in ("rolled_back", "rolled_back_with_warnings"), \
            f"Rollback did not complete: {cr['status']}"

        # Assert AD account re-enabled after rollback
        self._assert_ad_account_enabled(_state["test_user_sam"])

        log("Phase 2 PASSED: full lifecycle + verification + rollback re-enables account")

    def _assert_ad_account_disabled(self, sam: str):
        from ldap3 import Server, Connection, ALL
        private_ip = _state["private_ip"]
        base_dn = _state["base_dn"]
        bind_dn = f"CN=Administrator,CN=Users,{base_dn}"

        srv = Server(private_ip, port=389, get_info=ALL)
        conn = Connection(srv, user=bind_dn, password=_DC_ADMIN_PASSWORD, auto_bind=True)
        conn.search(base_dn, f"(sAMAccountName={sam})", attributes=["userAccountControl"])
        assert conn.entries, f"User {sam} not found in AD"
        uac = int(conn.entries[0].userAccountControl.value)
        conn.unbind()
        assert uac & 2, f"Expected AD account disabled (UAC bit 2 set), got UAC={uac}"

    def _assert_ad_account_enabled(self, sam: str):
        from ldap3 import Server, Connection, ALL
        private_ip = _state["private_ip"]
        base_dn = _state["base_dn"]
        bind_dn = f"CN=Administrator,CN=Users,{base_dn}"

        srv = Server(private_ip, port=389, get_info=ALL)
        conn = Connection(srv, user=bind_dn, password=_DC_ADMIN_PASSWORD, auto_bind=True)
        conn.search(base_dn, f"(sAMAccountName={sam})", attributes=["userAccountControl"])
        assert conn.entries, f"User {sam} not found in AD"
        uac = int(conn.entries[0].userAccountControl.value)
        conn.unbind()
        assert not (uac & 2), f"Expected AD account enabled (UAC bit 2 clear), got UAC={uac}"
```

- [ ] **Step 2: Verify the smoke test is syntactically valid**

```
cd backend && python -m py_compile tests/smoke/test_offboard_user_smoke.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 3: Run the smoke test on EC2**

From the laptop:
```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && git pull && \
   find backend/tests/smoke -name '__pycache__' -exec rm -rf {} + 2>/dev/null; \
   docker exec nexplane-backend-1 python -m pytest /app/tests/smoke/test_offboard_user_smoke.py -v -s 2>&1 | tee /tmp/offboard_smoke1.log; \
   echo SMOKE_DONE >> /tmp/offboard_smoke1.log"
```

Poll until `SMOKE_DONE` appears in `/tmp/offboard_smoke1.log`. Both phases should pass.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_offboard_user_smoke.py
git commit -m "test: offboard_user smoke — discovery accuracy + full lifecycle with AD verification"
```

---

## Self-Review

**Spec coverage:**
- ✅ Pre-execution discovery (Task 1 + Task 4) — `discover_accounts.py` + `_run_offboard_discovery` in `plan_cr`
- ✅ Plan builder filters to found connectors (Task 4) — only `discovered` connectors reach `build_plan`
- ✅ Approval view shows only found systems (Task 3 + 4) — steps generated only for discovered accounts
- ✅ Phase 5 verification with exponential backoff (Task 2) — `_DELAYS = [2, 4, 8, 16, 30]`, immediate check first
- ✅ Hard fail on verification failure (Task 2) — `verified=False` → step fails → CR fails
- ✅ Report is Phase 6, only runs if Phase 5 passes (Task 3) — report renumbered to Phase 6
- ✅ Report includes discovery manifest (Task 3 + 4) — `_discovery_manifest` injected into payload
- ✅ `PlanBlockedError` if zero accounts found (Task 4) — checked in `plan_cr` before `build_plan`
- ✅ FILO rollback Phase 4→3→2→1, Phases 5+6 have no rollback (Task 3) — `rollback_action: None` on verify + report
- ✅ Smoke test against live AD (Task 5) — 2 phases, DC AMI, LDAP direct verification
- ✅ Existing step executor files not modified (Tasks 1–5) — only new files and `__init__.py`/`offboarding_report.py`/`change_plan_service.py` modified

**Placeholder scan:** None found.

**Type consistency:**
- `account_identifier` — used in `resolved_connectors[n]["account_identifier"]` (Task 4), consumed as `parameters["account_identifier"]` (Task 2), set in verify step parameters as `c.get("account_identifier")` (Task 3). ✅ Consistent.
- `_DELAYS` — defined in `verify_disabled.py` as module-level list, monkeypatched in tests. ✅ Consistent.
- `_discovery_manifest` — injected by Task 4 into payload, read from `payload.get("_discovery_manifest", [])` in Task 3, passed to report parameters as `"discovery_manifest"`. ✅ Consistent.
