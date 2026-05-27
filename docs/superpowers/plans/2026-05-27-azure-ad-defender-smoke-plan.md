# Azure AD + Defender Endpoint Smoke Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add live smoke coverage for `azure_ad` and `defender_endpoint` connectors — two new executors for azure_ad, plus Phase AZURE_AD and Phase DEFENDER_ENDPOINT smoke functions in `test_aws_live.py`.

**Architecture:** Two new executors (`discover_users`, `get_group_membership`) are added to the azure_ad connector. Both phases run entirely against live Microsoft APIs — no EC2, no mock server. Credentials are fetched from the platform DB at runtime by listing connectors filtered by type. Both phases follow the established CR pipeline pattern (create connector → register asset → run CRs → assert → cleanup).

**Tech Stack:** Python 3.12, httpx (Microsoft Graph API), msal (Defender token), existing `NexplaneClient` smoke helpers, `ChangeType` enum, change_type_definition JSON files.

---

## Codebase Context (read before starting)

- **Smoke test file:** `backend/tests/smoke/test_aws_live.py` — 20,000+ lines. New `run_phase_*` functions go at the bottom before `if __name__ == "__main__": main()`. Phase wiring goes in `main()` around line 16124 after the `CHEF_INSPEC` block.
- **Pattern to follow:** `run_phase_checkov()` (line ~20757) and `run_phase_chef_inspec()` (line ~20845) — exact same structure.
- **Fetching live creds from DB:** `client.get("/connectors")` returns all connectors; filter by `connector_type`. Then use the matching connector's credentials when registering the smoke connector.
- **`_locked_connector_type` hint:** Always include `{"_locked_connector_type": "azure_ad"}` (or `"defender_endpoint"`) in `desired_outcome` for all `run_cr()` calls to prevent planning engine routing collisions.
- **`client.put()` wrapper:** Use `client.put(f"/connectors/{id}/credentials", json={"credentials": {...}})` — this wrapper includes auth headers. Do NOT use `client.client.put(...)`.
- **ChangeType enum:** `backend/app/models/change_request.py` — append new entries at the end of the enum.
- **change_type_definitions:** `backend/app/connectors/change_type_definitions/` — one JSON per change_type. `generic_action` must be unique across all actions in a connector to avoid routing collisions.
- **azure_ad catalog:** `backend/app/connectors/catalog/azure_ad.json` — existing actions: `disable_user` (generic_action: `"disable_user"`), `create_user` (generic_action: `"create_user"`). New actions must use different generic_action strings.
- **defender_endpoint catalog:** `backend/app/connectors/catalog/defender_endpoint.json` — existing `discover_machines` has generic_action `"discover"`, `isolate_machine` has `"isolate_endpoint"`, `unisolate_machine` has `"reconnect_endpoint"`. No new actions needed.
- **Graph API base:** `https://graph.microsoft.com/v1.0` — already in `AzureADClient.GRAPH_BASE`.
- **AzureADClient:** `backend/app/connectors/executors/azure_ad/azure_ad_client.py` — has `_get_token()`, `_headers()`, `disable_user()`, `enable_user()`, `create_user()`, `delete_user()`. New executors use the same pattern.

---

## Task 1: discover_users and get_group_membership executors + catalog wiring

**Files:**
- Create: `backend/app/connectors/executors/azure_ad/discover_users.py`
- Create: `backend/app/connectors/executors/azure_ad/get_group_membership.py`
- Modify: `backend/app/connectors/catalog/azure_ad.json`
- Modify: `backend/app/models/change_request.py`
- Create: `backend/app/connectors/change_type_definitions/discover_users.json`
- Create: `backend/app/connectors/change_type_definitions/get_group_membership.json`
- Test: `backend/tests/unit/test_azure_ad_new_executors.py`

- [ ] **Step 1: Write failing unit tests**

Create `backend/tests/unit/test_azure_ad_new_executors.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_connector(creds=None):
    c = MagicMock()
    c.credentials = creds or {"tenant_id": "t", "client_id": "c", "client_secret": "s"}
    return c


@pytest.mark.asyncio
async def test_discover_users_returns_users():
    from backend.app.connectors.executors.azure_ad.discover_users import execute
    mock_client = AsyncMock()
    mock_client.list_users = AsyncMock(return_value=[
        {"id": "u1", "displayName": "Alice", "userPrincipalName": "alice@test.com", "accountEnabled": True}
    ])
    with patch("backend.app.connectors.executors.azure_ad.discover_users.get_azure_ad_client", return_value=mock_client):
        result = await execute({}, ["asset-1"], _make_connector())
    assert result["users"][0]["id"] == "u1"
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_discover_users_no_creds_skipped():
    from backend.app.connectors.executors.azure_ad.discover_users import execute
    with patch("backend.app.connectors.executors.azure_ad.discover_users.get_azure_ad_client", return_value=None):
        result = await execute({}, [], _make_connector(creds={}))
    assert result["status"] == "skipped"


@pytest.mark.asyncio
async def test_get_group_membership_returns_groups():
    from backend.app.connectors.executors.azure_ad.get_group_membership import execute
    mock_client = AsyncMock()
    mock_client.get_group_membership = AsyncMock(return_value=[
        {"id": "g1", "displayName": "Security Team"}
    ])
    with patch("backend.app.connectors.executors.azure_ad.get_group_membership.get_azure_ad_client", return_value=mock_client):
        result = await execute({"user_id": "u1"}, ["asset-1"], _make_connector())
    assert result["groups"][0]["id"] == "g1"
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_get_group_membership_no_user_id_raises():
    from backend.app.connectors.executors.azure_ad.get_group_membership import execute
    mock_client = AsyncMock()
    with patch("backend.app.connectors.executors.azure_ad.get_group_membership.get_azure_ad_client", return_value=mock_client):
        with pytest.raises(ValueError, match="user_id is required"):
            await execute({}, [], _make_connector())


@pytest.mark.asyncio
async def test_get_group_membership_no_creds_skipped():
    from backend.app.connectors.executors.azure_ad.get_group_membership import execute
    with patch("backend.app.connectors.executors.azure_ad.get_group_membership.get_azure_ad_client", return_value=None):
        result = await execute({"user_id": "u1"}, [], _make_connector(creds={}))
    assert result["status"] == "skipped"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 python -m pytest backend/tests/unit/test_azure_ad_new_executors.py -v 2>&1 | tail -20
```

Expected: 4 errors (ImportError — modules don't exist yet).

- [ ] **Step 3: Add list_users and get_group_membership to AzureADClient**

Edit `backend/app/connectors/executors/azure_ad/azure_ad_client.py` — append these two methods before the closing of the class (before `get_azure_ad_client`):

```python
    async def list_users(self, top: int = 999) -> list[dict]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.GRAPH_BASE}/users",
                headers=await self._headers(),
                params={"$top": top, "$select": "id,displayName,userPrincipalName,accountEnabled"},
            )
            resp.raise_for_status()
            return resp.json().get("value", [])

    async def get_group_membership(self, user_id: str) -> list[dict]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.GRAPH_BASE}/users/{user_id}/memberOf",
                headers=await self._headers(),
                params={"$select": "id,displayName"},
            )
            resp.raise_for_status()
            return resp.json().get("value", [])

    async def get_primary_domain(self) -> str:
        """Return the tenant's .onmicrosoft.com domain for smoke user UPN construction."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.GRAPH_BASE}/domains",
                headers=await self._headers(),
            )
            resp.raise_for_status()
            domains = resp.json().get("value", [])
        for d in domains:
            if d.get("id", "").endswith(".onmicrosoft.com"):
                return d["id"]
        raise RuntimeError("No .onmicrosoft.com domain found in tenant")
```

- [ ] **Step 4: Create discover_users.py**

Create `backend/app/connectors/executors/azure_ad/discover_users.py`:

```python
from .azure_ad_client import get_azure_ad_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    client = get_azure_ad_client(connector)
    if not client:
        return {"action": "azure_ad_discover_users", "status": "skipped", "reason": "no_azure_ad_credentials"}
    users = await client.list_users()
    return {
        "action": "azure_ad_discover_users",
        "users": users,
        "count": len(users),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

- [ ] **Step 5: Create get_group_membership.py**

Create `backend/app/connectors/executors/azure_ad/get_group_membership.py`:

```python
from .azure_ad_client import get_azure_ad_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    user_id = parameters.get("user_id", "")
    if not user_id:
        raise ValueError("user_id is required")
    client = get_azure_ad_client(connector)
    if not client:
        return {"action": "azure_ad_get_group_membership", "status": "skipped", "reason": "no_azure_ad_credentials"}
    groups = await client.get_group_membership(user_id)
    return {
        "action": "azure_ad_get_group_membership",
        "user_id": user_id,
        "groups": groups,
        "count": len(groups),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
docker exec nexplane-backend-1 python -m pytest backend/tests/unit/test_azure_ad_new_executors.py -v 2>&1 | tail -15
```

Expected: 4 tests PASSED.

- [ ] **Step 7: Add ChangeType entries**

Edit `backend/app/models/change_request.py` — append after the last existing entry (after `discover_compliance_results = "discover_compliance_results"`):

```python
    discover_users = "discover_users"
    get_group_membership = "get_group_membership"
```

- [ ] **Step 8: Create change_type_definition JSONs**

Create `backend/app/connectors/change_type_definitions/discover_users.json`:

```json
{
  "change_type": "discover_users",
  "display_name": "Discover Azure AD Users",
  "steps": [
    {"generic_action": "discover_users", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": []
}
```

Create `backend/app/connectors/change_type_definitions/get_group_membership.json`:

```json
{
  "change_type": "get_group_membership",
  "display_name": "Get Azure AD Group Membership",
  "steps": [
    {"generic_action": "get_group_membership", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": []
}
```

- [ ] **Step 9: Add actions to azure_ad.json catalog**

Edit `backend/app/connectors/catalog/azure_ad.json` — add two new entries to the `"actions"` array after `create_user`:

```json
    {"action_id": "discover_users", "generic_action": "discover_users", "action_type": "ingest", "execution_tier": 1, "display_name": "Discover Users", "description": "List all Azure AD users with id, displayName, UPN, and accountEnabled status.", "applicable_asset_types": ["identity"], "parameters": [], "executor": "azure_ad.discover_users", "estimated_duration_seconds": 10},
    {"action_id": "get_group_membership", "generic_action": "get_group_membership", "action_type": "ingest", "execution_tier": 1, "display_name": "Get Group Membership", "description": "Return all groups a user belongs to.", "applicable_asset_types": ["identity"], "parameters": [{"name": "user_id", "type": "string", "required": true, "description": "Azure AD object ID of the user"}], "executor": "azure_ad.get_group_membership", "estimated_duration_seconds": 5}
```

- [ ] **Step 10: Verify catalog JSON is valid**

```bash
python -c "import json; json.load(open('backend/app/connectors/catalog/azure_ad.json')); print('OK')"
```

Expected: `OK`

- [ ] **Step 11: Commit**

```bash
git add backend/tests/unit/test_azure_ad_new_executors.py \
        backend/app/connectors/executors/azure_ad/discover_users.py \
        backend/app/connectors/executors/azure_ad/get_group_membership.py \
        backend/app/connectors/executors/azure_ad/azure_ad_client.py \
        backend/app/connectors/catalog/azure_ad.json \
        backend/app/models/change_request.py \
        backend/app/connectors/change_type_definitions/discover_users.json \
        backend/app/connectors/change_type_definitions/get_group_membership.json
git commit -m "feat: azure_ad discover_users + get_group_membership executors with catalog wiring"
```

---

## Task 2: Phase AZURE_AD smoke function

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py` — add `run_phase_azure_ad()` and wire into `main()`

**Context:** The function fetches live azure_ad connector credentials from the platform DB, creates a smoke connector with those creds, runs 5 CRs, and cleans up a smoke user in `finally`. The pattern exactly mirrors `run_phase_checkov()`.

- [ ] **Step 1: Add run_phase_azure_ad() to test_aws_live.py**

Insert the following function in `backend/tests/smoke/test_aws_live.py` immediately before the `if __name__ == "__main__": main()` line at the end of the file (after `run_phase_chef_inspec`):

```python
def run_phase_azure_ad(client, cloud_account_id: str) -> None:
    """Phase AZURE_AD: discover_users → get_group_membership → create_user → disable_user → rollback via CR pipeline."""
    import secrets as _sec
    print("\n[Phase AZURE_AD] Azure AD lifecycle (discover_users→get_group_membership→create→disable→rollback)")
    suffix = _sec.token_hex(4)
    azure_conn_id = None
    azure_asset_id = None
    smoke_upn = None

    # Fetch live credentials from platform DB
    all_conns = client.get("/connectors")
    source = next((c for c in all_conns if c.get("connector_type") == "azure_ad"), None)
    if not source:
        fail("[AZURE_AD] No azure_ad connector found in platform DB — register one with live credentials first")
    source_creds_resp = client.get(f"/connectors/{source['id']}/credentials")
    live_creds = source_creds_resp.get("credentials", {})
    if not live_creds.get("tenant_id"):
        fail("[AZURE_AD] azure_ad connector has no credentials stored")

    try:
        # Register smoke connector
        azure_conn = client.post("/connectors", json={
            "connector_type": "azure_ad",
            "name": f"nexplane-smoke-azure-ad-{suffix}",
            "display_name": f"nexplane-smoke-azure-ad-{suffix}",
        })
        azure_conn_id = azure_conn.get("id")
        client.put(f"/connectors/{azure_conn_id}/credentials", json={"credentials": live_creds})
        log("AZURE_AD connector registered: " + str(azure_conn_id))
        azure_asset_id = client.register_asset_for_connector(
            f"nexplane-smoke-azure-ad-{suffix}", azure_conn_id, asset_type="identity"
        )
        log("AZURE_AD asset registered: " + str(azure_asset_id))

        _hint = {"_locked_connector_type": "azure_ad"}

        # CR 1: discover_users
        cr_discover = client.run_cr(
            "[AZURE_AD] discover_users", "discover_users", azure_asset_id,
            {**_hint}, connector_id=azure_conn_id,
        )
        result_discover = client.get_cr_step_result(cr_discover)
        users = result_discover.get("users", [])
        log("  AZURE_AD: discover_users count=" + str(len(users)))
        if not users:
            fail("[AZURE_AD] discover_users returned empty list — expected at least one user in tenant")
        target_user_id = users[0]["id"]
        log("  AZURE_AD: discover_users ✓ (first user id=" + target_user_id + ")")

        # CR 2: get_group_membership for first user
        cr_groups = client.run_cr(
            "[AZURE_AD] get_group_membership", "get_group_membership", azure_asset_id,
            {**_hint, "user_id": target_user_id}, connector_id=azure_conn_id,
        )
        result_groups = client.get_cr_step_result(cr_groups)
        if "groups" not in result_groups:
            fail("[AZURE_AD] get_group_membership result missing 'groups' key: " + str(result_groups))
        log("  AZURE_AD: get_group_membership groups=" + str(result_groups.get("count")) + " ✓")

        # Derive onmicrosoft.com domain for smoke UPN
        import httpx as _httpx
        _token_resp = _httpx.post(
            f"https://login.microsoftonline.com/{live_creds['tenant_id']}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": live_creds["client_id"],
                "client_secret": live_creds["client_secret"],
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        _token_resp.raise_for_status()
        _token = _token_resp.json()["access_token"]
        _domains_resp = _httpx.get(
            "https://graph.microsoft.com/v1.0/domains",
            headers={"Authorization": f"Bearer {_token}"},
        )
        _domains_resp.raise_for_status()
        _primary_domain = next(
            (d["id"] for d in _domains_resp.json().get("value", []) if d["id"].endswith(".onmicrosoft.com")),
            None,
        )
        if not _primary_domain:
            fail("[AZURE_AD] Could not derive .onmicrosoft.com domain from tenant")
        smoke_upn = f"nexplane-smoke-{suffix}@{_primary_domain}"
        log("  AZURE_AD: smoke UPN will be " + smoke_upn)

        # CR 3: create_user
        cr_create = client.run_cr(
            "[AZURE_AD] create_user", "create_user", azure_asset_id,
            {**_hint, "user_principal_name": smoke_upn, "display_name": f"Nexplane Smoke {suffix}"},
            connector_id=azure_conn_id,
        )
        result_create = client.get_cr_step_result(cr_create)
        if not result_create.get("id"):
            fail("[AZURE_AD] create_user did not return user id: " + str(result_create))
        log("  AZURE_AD: create_user id=" + str(result_create.get("id")) + " ✓")

        # CR 4: disable_user
        cr_disable = client.run_cr(
            "[AZURE_AD] disable_user", "disable_user", azure_asset_id,
            {**_hint, "user_identifier": smoke_upn}, connector_id=azure_conn_id,
        )
        result_disable = client.get_cr_step_result(cr_disable)
        if result_disable.get("accountEnabled") is not False:
            fail("[AZURE_AD] disable_user did not return accountEnabled=false: " + str(result_disable))
        log("  AZURE_AD: disable_user accountEnabled=false ✓")

        # CR 5: rollback disable_user (re-enables user)
        client.rollback_cr(cr_disable)
        log("  AZURE_AD: rollback disable_user (re-enabled) ✓")

        log("Phase AZURE_AD PASSED")

    except Exception as e:
        print("\n[FAIL] Phase AZURE_AD failed: " + str(e))
        raise
    finally:
        # Always delete smoke user
        if smoke_upn:
            try:
                import httpx as _httpx2
                _token_resp2 = _httpx2.post(
                    f"https://login.microsoftonline.com/{live_creds['tenant_id']}/oauth2/v2.0/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": live_creds["client_id"],
                        "client_secret": live_creds["client_secret"],
                        "scope": "https://graph.microsoft.com/.default",
                    },
                )
                _token_resp2.raise_for_status()
                _token2 = _token_resp2.json()["access_token"]
                _del_resp = _httpx2.delete(
                    f"https://graph.microsoft.com/v1.0/users/{smoke_upn}",
                    headers={"Authorization": f"Bearer {_token2}"},
                )
                if _del_resp.status_code not in (200, 204, 404):
                    log("  AZURE_AD: warning — smoke user delete returned " + str(_del_resp.status_code))
                else:
                    log("  AZURE_AD: smoke user deleted")
            except Exception:
                pass
        # Delete smoke connector
        if azure_conn_id:
            try:
                client.client.delete(f"{client.base}/connectors/{azure_conn_id}")
            except Exception:
                pass
```

- [ ] **Step 2: Check that client.rollback_cr exists**

```bash
grep -n "def rollback_cr" backend/tests/smoke/smoke_helpers.py
```

If `rollback_cr` does not exist, replace CR 5 in `run_phase_azure_ad` with a direct rollback call instead:

```python
        # CR 5: rollback disable_user via the CR rollback endpoint
        client.post(f"/change-requests/{cr_disable}/rollback", json={})
        log("  AZURE_AD: rollback disable_user (re-enabled) ✓")
```

Check which path applies and use it.

- [ ] **Step 3: Wire AZURE_AD into main()**

In `main()` around line 16124 (after the `CHEF_INSPEC` block), add:

```python
        if "AZURE_AD" in phases:
            run_phase_azure_ad(client, cloud_account_id)
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "smoke: Phase AZURE_AD — discover_users + get_group_membership + create + disable + rollback via CR pipeline"
```

---

## Task 3: Phase DEFENDER_ENDPOINT smoke function

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py` — add `run_phase_defender_endpoint()` and wire into `main()`

**Context:** All defender_endpoint executors already exist. Credentials fetched from platform DB. `unisolate_machine` runs in `finally` to guarantee the machine is released even if the isolate assertion fails.

- [ ] **Step 1: Add run_phase_defender_endpoint() to test_aws_live.py**

Insert immediately before `if __name__ == "__main__": main()` (after `run_phase_azure_ad`):

```python
def run_phase_defender_endpoint(client, cloud_account_id: str) -> None:
    """Phase DEFENDER_ENDPOINT: discover_machines → isolate_machine → unisolate_machine via CR pipeline."""
    import secrets as _sec
    print("\n[Phase DEFENDER_ENDPOINT] Defender lifecycle (discover_machines→isolate→unisolate)")
    suffix = _sec.token_hex(4)
    defender_conn_id = None
    defender_asset_id = None
    machine_id = None

    # Fetch live credentials from platform DB
    all_conns = client.get("/connectors")
    source = next((c for c in all_conns if c.get("connector_type") == "defender_endpoint"), None)
    if not source:
        fail("[DEFENDER] No defender_endpoint connector found in platform DB — register one with live credentials first")
    source_creds_resp = client.get(f"/connectors/{source['id']}/credentials")
    live_creds = source_creds_resp.get("credentials", {})
    if not live_creds.get("tenant_id"):
        fail("[DEFENDER] defender_endpoint connector has no credentials stored")

    try:
        # Register smoke connector
        defender_conn = client.post("/connectors", json={
            "connector_type": "defender_endpoint",
            "name": f"nexplane-smoke-defender-{suffix}",
            "display_name": f"nexplane-smoke-defender-{suffix}",
        })
        defender_conn_id = defender_conn.get("id")
        client.put(f"/connectors/{defender_conn_id}/credentials", json={"credentials": live_creds})
        log("DEFENDER connector registered: " + str(defender_conn_id))
        defender_asset_id = client.register_asset_for_connector(
            f"nexplane-smoke-defender-{suffix}", defender_conn_id, asset_type="server"
        )
        log("DEFENDER asset registered: " + str(defender_asset_id))

        _hint = {"_locked_connector_type": "defender_endpoint"}

        # CR 1: discover_machines
        cr_discover = client.run_cr(
            "[DEFENDER] discover_machines", "discover_machines", defender_asset_id,
            {**_hint}, connector_id=defender_conn_id,
        )
        result_discover = client.get_cr_step_result(cr_discover)
        machines = result_discover.get("machines", [])
        log("  DEFENDER: discover_machines count=" + str(len(machines)))
        if not machines:
            fail("[DEFENDER] discover_machines returned empty list — no enrolled machines in tenant")
        machine_id = machines[0]["id"]
        log("  DEFENDER: discovered machine_id=" + str(machine_id) + " ✓")

    except Exception as e:
        print("\n[FAIL] Phase DEFENDER_ENDPOINT failed: " + str(e))
        raise
    finally:
        # Always unisolate if we isolated (machine_id set means discover succeeded)
        if machine_id and defender_conn_id and defender_asset_id:
            try:
                _hint2 = {"_locked_connector_type": "defender_endpoint"}
                cr_iso = None
                try:
                    cr_iso = client.run_cr(
                        "[DEFENDER] isolate_machine", "isolate_machine", defender_asset_id,
                        {**_hint2, "machine_id": machine_id,
                         "comment": "Nexplane smoke test — unisolate follows immediately"},
                        connector_id=defender_conn_id,
                    )
                    result_iso = client.get_cr_step_result(cr_iso)
                    iso_status = result_iso.get("status", "")
                    log("  DEFENDER: isolate_machine status=" + iso_status)
                    if iso_status not in ("Pending", "Succeeded"):
                        fail("[DEFENDER] isolate_machine unexpected status: " + str(result_iso))
                    log("  DEFENDER: isolate_machine ✓")
                except Exception as _iso_e:
                    log("  DEFENDER: isolate_machine failed: " + str(_iso_e))
                    raise
                finally:
                    # Unisolate unconditionally
                    try:
                        cr_uniso = client.run_cr(
                            "[DEFENDER] unisolate_machine", "unisolate_machine", defender_asset_id,
                            {**_hint2, "machine_id": machine_id},
                            connector_id=defender_conn_id,
                        )
                        result_uniso = client.get_cr_step_result(cr_uniso)
                        uniso_status = result_uniso.get("status", "")
                        if uniso_status not in ("Pending", "Succeeded"):
                            log("  DEFENDER: warning — unisolate_machine status=" + uniso_status)
                        else:
                            log("  DEFENDER: unisolate_machine ✓")
                    except Exception as _uniso_e:
                        log("  DEFENDER: unisolate_machine error (machine may still be isolated!): " + str(_uniso_e))
            except Exception as e2:
                print("\n[FAIL] Phase DEFENDER_ENDPOINT failed: " + str(e2))
                raise

        # Delete smoke connector
        if defender_conn_id:
            try:
                client.client.delete(f"{client.base}/connectors/{defender_conn_id}")
            except Exception:
                pass

    log("Phase DEFENDER_ENDPOINT PASSED")
```

- [ ] **Step 2: Wire DEFENDER_ENDPOINT into main()**

In `main()` after the `AZURE_AD` block, add:

```python
        if "DEFENDER_ENDPOINT" in phases:
            run_phase_defender_endpoint(client, cloud_account_id)
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "smoke: Phase DEFENDER_ENDPOINT — discover_machines + isolate + unisolate via CR pipeline"
```

---

## Task 4: Push, sync EC2, run smoke

- [ ] **Step 1: Push to origin**

```bash
git push origin master
```

- [ ] **Step 2: Sync EC2 and rebuild backend**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && git pull origin master && docker compose up -d --build backend 2>&1 | tail -8"
```

Wait ~30s for backend to start, then verify:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker exec nexplane-backend-1 curl -s http://localhost:8000/health"
```

Expected: `{"status":"ok","service":"nexplane"}`

- [ ] **Step 3: Run Phase AZURE_AD**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker exec nexplane-backend-1 bash /app/tests/smoke/run_smoke.sh AZURE_AD 2>&1 | tail -30"
```

Expected: `✅ ALL SELECTED PHASES PASSED` with individual ✅ lines for each CR.

- [ ] **Step 4: Run Phase DEFENDER_ENDPOINT**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker exec nexplane-backend-1 bash /app/tests/smoke/run_smoke.sh DEFENDER_ENDPOINT 2>&1 | tail -30"
```

Expected: `✅ ALL SELECTED PHASES PASSED` with isolate + unisolate both showing ✓.

- [ ] **Step 5: Commit smoke results note (if both pass)**

No code change needed — smoke passage is the completion criterion per the smoke-test-driven-development principle.

---

## Self-Review

**Spec coverage check:**
- ✅ Phase AZURE_AD: discover_users → get_group_membership → create_user → disable_user → rollback — covered in Task 2
- ✅ Phase DEFENDER_ENDPOINT: discover_machines → isolate → unisolate — covered in Task 3
- ✅ New executors (discover_users, get_group_membership) — Task 1
- ✅ Catalog wiring — Task 1
- ✅ ChangeType entries — Task 1
- ✅ change_type_definition JSONs — Task 1
- ✅ Smoke UPN derived from `.onmicrosoft.com` domain — Task 2
- ✅ unisolate in finally block — Task 3
- ✅ smoke user deleted in finally block — Task 2

**Placeholder scan:** None found. All code blocks are complete.

**Type consistency:**
- `target_user_id` set from `users[0]["id"]` in Task 2 → passed as `"user_id"` parameter to `get_group_membership` CR → executor reads `parameters.get("user_id")` ✓
- `machine_id` set from `machines[0]["id"]` in Task 3 → passed as `"machine_id"` to isolate/unisolate CRs → executor reads `parameters["machine_id"]` ✓
- `generic_action` values: `"discover_users"` and `"get_group_membership"` — unique, no collision with existing `"disable_user"`, `"create_user"` ✓
