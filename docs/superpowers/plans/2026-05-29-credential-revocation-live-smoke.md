# Credential Revocation Live Smoke + AWS Reconstitution Rollback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove credential revocation CRs execute and roll back correctly across GCP, Azure AD, LDAP, OCI, and AWS connectors using throwaway accounts, while enhancing the AWS `revoke_exposed_credential` executor with reconstitution rollback.

**Architecture:** Four sequential tasks: (1) wire `revoke_exposed_credential` into the catalog/planning engine so it can run through the CR workflow, (2) add `ldap_disable_user` as a proper ChangeType with catalog and planning definitions, (3) enhance the AWS executor with reconstitution rollback + unit tests, (4) add the `phase_credential_revocation_live` smoke phase across all 5 connectors.

**Tech Stack:** Python, boto3, google-cloud-iam, azure-identity/msgraph (via existing clients), oci SDK, ldap3, pytest-asyncio, NexplaneClient smoke harness.

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `backend/app/connectors/catalog/aws.json` | Modify | Add `revoke_exposed_credential` action entry |
| `backend/app/connectors/change_type_definitions/revoke_exposed_credential.json` | Modify | Add `steps` key so planning engine can generate a plan |
| `backend/app/models/change_request.py` | Modify | Add `ldap_disable_user` to `ChangeType` enum |
| `backend/app/connectors/catalog/ldap.json` | Modify (already has action) | Verify `ldap_disable_user` action has rollback wiring |
| `backend/app/connectors/change_type_definitions/ldap_disable_user.json` | Create | New change type definition for LDAP disable |
| `backend/app/connectors/executors/aws/revoke_exposed_credential.py` | Modify | Reconstitution rollback for `aws_iam_key` |
| `backend/app/tests/test_revoke_credential_rollback.py` | Create | Unit tests for new rollback logic |
| `backend/tests/smoke/test_feature_smoke_live.py` | Modify | Add `phase_credential_revocation_live` |

---

## Task 1: Wire `revoke_exposed_credential` into the Planning Engine

The change type exists in the `ChangeType` enum and the executor exists, but the change type definition JSON lacks `steps` (planning engine requires it) and the AWS catalog has no `revoke_exposed_credential` action entry (catalog lookup would return no options, causing the plan step to use `connector_type: "unknown"`).

**Files:**
- Modify: `backend/app/connectors/catalog/aws.json`
- Modify: `backend/app/connectors/change_type_definitions/revoke_exposed_credential.json`

- [ ] **Step 1: Add the action to the AWS catalog**

Open `backend/app/connectors/catalog/aws.json`. Before the closing `]` of the `"actions"` array (just before the final `}`), append:

```json
    ,{
      "action_id": "revoke_exposed_credential",
      "generic_action": "revoke_exposed_credential",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Revoke Exposed Credential",
      "description": "Permanently revoke a compromised IAM access key. Rollback reconstitutes access by creating a new key for the same user.",
      "applicable_asset_types": ["cloud_account", "iam_user"],
      "parameters": [
        {"name": "credential_type", "type": "string", "required": true},
        {"name": "credential_id", "type": "string", "required": true}
      ],
      "executor": "aws.revoke_exposed_credential",
      "rollback_action": "revoke_exposed_credential",
      "rollback_connector_type": "aws",
      "estimated_duration_seconds": 15,
      "blast_radius_hint": "iam_key_permanently_deleted"
    }
```

- [ ] **Step 2: Add `steps` to the change type definition**

Replace the entire contents of `backend/app/connectors/change_type_definitions/revoke_exposed_credential.json` with:

```json
{
  "change_type": "revoke_exposed_credential",
  "display_name": "Revoke Exposed Credential",
  "description": "Immediately revoke a known-compromised credential. For aws_iam_key: rollback reconstitutes access by creating a new key for the same IAM user.",
  "steps": [
    {
      "generic_action": "revoke_exposed_credential",
      "purpose": "execute",
      "required": true
    }
  ],
  "preflight_checks": [
    "connector_reachable"
  ],
  "verification_methods": [
    "api_check"
  ],
  "rollback_action": "revoke_exposed_credential",
  "rollback_connector_type": "aws"
}
```

- [ ] **Step 3: Verify the planning engine can load the definition**

Run from inside the backend container:

```bash
docker exec nexplane-backend-1 python -c "
from app.services.planning_engine import _load_change_type_def
from app.models.change_request import ChangeType
d = _load_change_type_def(ChangeType.revoke_exposed_credential)
print('steps:', d['steps'])
print('OK')
"
```

Expected: prints `steps: [{'generic_action': 'revoke_exposed_credential', ...}]` and `OK`.

- [ ] **Step 4: Verify the catalog lookup finds the AWS action**

```bash
docker exec nexplane-backend-1 python -c "
from app.connectors.catalog_service import get_catalog_service
catalog = get_catalog_service()
opts = catalog.get_options_for_action('revoke_exposed_credential')
print('options:', [(o.connector_type, o.action_id) for o in opts])
"
```

Expected: prints `options: [('aws', 'revoke_exposed_credential')]`

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/catalog/aws.json \
        backend/app/connectors/change_type_definitions/revoke_exposed_credential.json
git commit -m "feat: wire revoke_exposed_credential into catalog and planning engine"
```

---

## Task 2: Add `ldap_disable_user` as a First-Class Change Type

The executor (`ldap.disable_user`) and catalog action (`ldap_disable_user` in `ldap.json`) exist. What's missing: a `ChangeType` enum entry and a change type definition JSON file. Without these the planning engine cannot generate a plan for this CR type.

**Files:**
- Modify: `backend/app/models/change_request.py`
- Create: `backend/app/connectors/change_type_definitions/ldap_disable_user.json`

- [ ] **Step 1: Add `ldap_disable_user` to the ChangeType enum**

Open `backend/app/models/change_request.py`. Find the line:

```python
    azure_ad_disable_user = "azure_ad_disable_user"
```

Add immediately after it:

```python
    ldap_disable_user = "ldap_disable_user"
```

- [ ] **Step 2: Create the change type definition**

Create `backend/app/connectors/change_type_definitions/ldap_disable_user.json`:

```json
{
  "change_type": "ldap_disable_user",
  "display_name": "Disable LDAP User",
  "description": "Disables an LDAP/Active Directory user account by setting pwdAccountLockedTime. Rollback clears the lock to re-enable the account.",
  "steps": [
    {
      "generic_action": "ldap_disable_user",
      "purpose": "execute",
      "required": true
    }
  ],
  "preflight_checks": [
    "connector_reachable"
  ],
  "verification_methods": [
    "api_check"
  ],
  "rollback_action": "ldap_disable_user",
  "rollback_connector_type": "ldap"
}
```

- [ ] **Step 3: Verify enum and planning engine**

```bash
docker exec nexplane-backend-1 python -c "
from app.models.change_request import ChangeType
print(ChangeType.ldap_disable_user.value)
from app.services.planning_engine import _load_change_type_def
d = _load_change_type_def(ChangeType.ldap_disable_user)
print('steps:', d['steps'])
print('OK')
"
```

Expected: `ldap_disable_user` then `steps: [...]` then `OK`.

- [ ] **Step 4: Run existing tests to ensure no enum regression**

```bash
docker exec nexplane-backend-1 python -m pytest app/tests/test_vuln_mitigation_cr_types.py -v -x
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/change_request.py \
        backend/app/connectors/change_type_definitions/ldap_disable_user.json
git commit -m "feat: add ldap_disable_user as first-class ChangeType with planning definition"
```

---

## Task 3: AWS Reconstitution Rollback + Unit Tests

Enhance `revoke_exposed_credential.py` so the `aws_iam_key` path captures `username` and `user_arn` before deleting, returns `rollback_available: True`, and the rollback function creates a new access key for the same user.

**Files:**
- Modify: `backend/app/connectors/executors/aws/revoke_exposed_credential.py`
- Create: `backend/app/tests/test_revoke_credential_rollback.py`

- [ ] **Step 1: Write the failing unit tests first**

Create `backend/app/tests/test_revoke_credential_rollback.py`:

```python
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


def _make_connector(creds: dict):
    c = MagicMock()
    c.creds = creds
    return c


@pytest.mark.asyncio
async def test_aws_iam_key_execute_captures_rollback_params():
    """Execute must save username + user_arn into rollback_params before deleting the key."""
    fake_creds = {
        "aws_access_key_id": "AKIATEST",
        "aws_secret_access_key": "secret",
        "region": "us-east-1",
    }
    connector = _make_connector(fake_creds)

    mock_iam = MagicMock()
    mock_iam.get_access_key_last_used.return_value = {"UserName": "smoke-user"}
    mock_iam.get_user.return_value = {"User": {"Arn": "arn:aws:iam::123:user/smoke-user"}}
    mock_iam.delete_access_key.return_value = {}

    with patch("boto3.client", return_value=mock_iam):
        from app.connectors.executors.aws.revoke_exposed_credential import execute
        result = await execute(
            {"credential_type": "aws_iam_key", "credential_id": "AKIACOMPROMISED"},
            [],
            connector,
        )

    assert result["success"] is True
    assert result["rollback_available"] is True
    assert result["rollback_type"] == "reconstitution"
    rp = result["rollback_params"]
    assert rp["username"] == "smoke-user"
    assert rp["user_arn"] == "arn:aws:iam::123:user/smoke-user"
    assert rp["credential_type"] == "aws_iam_key"
    mock_iam.delete_access_key.assert_called_once_with(AccessKeyId="AKIACOMPROMISED")


@pytest.mark.asyncio
async def test_aws_iam_key_rollback_creates_new_key():
    """Rollback must call create_access_key and return the new key ID."""
    fake_creds = {
        "aws_access_key_id": "AKIATEST",
        "aws_secret_access_key": "secret",
        "region": "us-east-1",
    }
    connector = _make_connector(fake_creds)

    mock_iam = MagicMock()
    mock_iam.create_access_key.return_value = {
        "AccessKey": {
            "AccessKeyId": "AKIANEWKEY",
            "SecretAccessKey": "newsecret",
            "UserName": "smoke-user",
        }
    }

    execution_result = {
        "success": True,
        "rollback_available": True,
        "rollback_type": "reconstitution",
        "rollback_params": {
            "credential_type": "aws_iam_key",
            "username": "smoke-user",
            "user_arn": "arn:aws:iam::123:user/smoke-user",
        },
    }

    with patch("boto3.client", return_value=mock_iam):
        from app.connectors.executors.aws.revoke_exposed_credential import rollback
        result = await rollback({}, execution_result, connector)

    assert result["rolled_back"] is True
    assert result["rollback_type"] == "reconstitution"
    assert result["new_access_key_id"] == "AKIANEWKEY"
    mock_iam.create_access_key.assert_called_once_with(UserName="smoke-user")


@pytest.mark.asyncio
async def test_other_credential_types_remain_no_rollback():
    """vault_token and other types must still return rollback_available: False."""
    connector = _make_connector(None)

    from app.connectors.executors.aws.revoke_exposed_credential import rollback
    result = await rollback({}, {"success": True, "rollback_available": False}, connector)
    assert result["rolled_back"] is False


@pytest.mark.asyncio
async def test_mock_mode_unchanged():
    """When no creds, mock mode must still work and not attempt rollback."""
    connector = _make_connector(None)

    from app.connectors.executors.aws.revoke_exposed_credential import execute
    result = await execute(
        {"credential_type": "aws_iam_key", "credential_id": "AKIAMOCK"},
        [],
        connector,
    )
    assert result["mock"] is True
    assert result["rolled_back_available"] is False
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
docker exec nexplane-backend-1 python -m pytest app/tests/test_revoke_credential_rollback.py -v 2>&1 | head -40
```

Expected: 3 tests fail (execute/rollback behavior not implemented yet), 1 passes (`test_other_credential_types_remain_no_rollback`).

- [ ] **Step 3: Implement reconstitution rollback in the executor**

Replace the entire contents of `backend/app/connectors/executors/aws/revoke_exposed_credential.py`:

```python
"""Executor: immediately revoke a known-compromised credential.
For aws_iam_key: reconstitution rollback — saves username before delete, creates new key on rollback.
Other credential types: permanent, no rollback.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def execute(parameters: dict, asset_ids: list[str], connector: Any) -> dict:
    credential_type = parameters["credential_type"]
    credential_id = parameters["credential_id"]
    creds = getattr(connector, "creds", None)

    if not creds:
        logger.info("[mock] Would revoke %s %s", credential_type, credential_id)
        return {"success": True, "mock": True, "rolled_back_available": False}

    if credential_type == "aws_iam_key":
        import boto3
        iam = boto3.client(
            "iam",
            aws_access_key_id=creds["aws_access_key_id"],
            aws_secret_access_key=creds["aws_secret_access_key"],
            region_name=creds.get("region", "us-east-1"),
        )
        # Capture state before deletion for reconstitution rollback
        key_info = iam.get_access_key_last_used(AccessKeyId=credential_id)
        username = key_info["UserName"]
        user_info = iam.get_user(UserName=username)
        user_arn = user_info["User"]["Arn"]

        iam.delete_access_key(AccessKeyId=credential_id)
        logger.info("Revoked IAM key %s for user %s", credential_id, username)
        return {
            "success": True,
            "rollback_available": True,
            "rollback_type": "reconstitution",
            "rollback_params": {
                "credential_type": "aws_iam_key",
                "username": username,
                "user_arn": user_arn,
            },
        }

    if credential_type == "vault_token":
        import httpx
        vault_addr = creds.get("vault_addr", "http://localhost:8200")
        vault_token = creds.get("vault_token")
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{vault_addr}/v1/auth/token/revoke",
                headers={"X-Vault-Token": vault_token},
                json={"token": credential_id},
            )
            resp.raise_for_status()
        return {"success": True, "rolled_back_available": False}

    if credential_type == "gcp_service_account_key":
        import json
        import googleapiclient.discovery
        from google.oauth2 import service_account

        sa_info = json.loads(creds["service_account_key_json"])
        gcp_creds = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        service = googleapiclient.discovery.build("iam", "v1", credentials=gcp_creds)
        service.projects().serviceAccounts().keys().delete(
            name=f"projects/-/serviceAccounts/-/keys/{credential_id}"
        ).execute()
        logger.info("Revoked GCP service account key %s", credential_id)
        return {"success": True, "rolled_back_available": False}

    if credential_type == "azure_client_secret":
        import httpx
        parts = credential_id.split("/", 1)
        if len(parts) != 2:
            raise ValueError(
                "azure_client_secret credential_id must be '{app_object_id}/{key_id}'"
            )
        app_id, key_id = parts
        async with httpx.AsyncClient() as client:
            token_resp = await client.post(
                f"https://login.microsoftonline.com/{creds['tenant_id']}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": creds["client_id"],
                    "client_secret": creds["client_secret"],
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            token_resp.raise_for_status()
            token = token_resp.json()["access_token"]
            remove_resp = await client.post(
                f"https://graph.microsoft.com/v1.0/applications/{app_id}/removePassword",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"keyId": key_id},
            )
            remove_resp.raise_for_status()
        logger.info("Revoked Azure client secret key %s from app %s", key_id, app_id)
        return {"success": True, "rolled_back_available": False}

    if credential_type == "ldap_password":
        import ldap3
        from ldap3 import MODIFY_REPLACE
        server = ldap3.Server(creds["server"])
        with ldap3.Connection(
            server,
            user=creds["bind_dn"],
            password=creds["bind_password"],
            auto_bind=True,
        ) as conn:
            conn.modify(credential_id, {"userAccountControl": [(MODIFY_REPLACE, [514])]})
        logger.info("Disabled LDAP account %s", credential_id)
        return {"success": True, "rolled_back_available": False}

    raise ValueError(f"Unsupported credential_type: {credential_type}")


async def rollback(parameters: dict, execution_result: dict, connector: Any) -> dict:
    rp = execution_result.get("rollback_params", {})
    if rp.get("credential_type") == "aws_iam_key":
        creds = getattr(connector, "creds", None)
        if not creds:
            return {"rolled_back": False, "reason": "no credentials available for reconstitution"}
        import boto3
        iam = boto3.client(
            "iam",
            aws_access_key_id=creds["aws_access_key_id"],
            aws_secret_access_key=creds["aws_secret_access_key"],
            region_name=creds.get("region", "us-east-1"),
        )
        new_key = iam.create_access_key(UserName=rp["username"])["AccessKey"]
        logger.info("Reconstituted access key %s for user %s", new_key["AccessKeyId"], rp["username"])
        return {
            "rolled_back": True,
            "rollback_type": "reconstitution",
            "new_access_key_id": new_key["AccessKeyId"],
            "username": rp["username"],
            "note": "Original key is permanently deleted. New key created for same user.",
        }
    return {"rolled_back": False, "reason": "Credential revocation is permanent — no rollback available"}
```

- [ ] **Step 4: Run the unit tests**

```bash
docker exec nexplane-backend-1 python -m pytest app/tests/test_revoke_credential_rollback.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Run the existing revoke tests to check for regressions**

```bash
docker exec nexplane-backend-1 python -m pytest app/tests/test_vuln_mitigation_cr_types.py -v -x
```

Expected: all pass. Note: `test_revoke_exposed_credential_rollback_always_false` will need to be updated — it now tests the non-aws_iam_key rollback path. If it fails, update it to call rollback with an `execution_result` that has no `rollback_params` (simulating vault_token revocation):

```python
@pytest.mark.asyncio
async def test_revoke_exposed_credential_rollback_always_false():
    from app.connectors.executors.aws.revoke_exposed_credential import rollback
    # Non-aws_iam_key types (vault_token, gcp_sa_key, etc.) remain non-rollbackable
    result = await rollback({}, {"success": True, "rolled_back_available": False}, None)
    assert result["rolled_back"] is False
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/aws/revoke_exposed_credential.py \
        backend/app/tests/test_revoke_credential_rollback.py \
        backend/app/tests/test_vuln_mitigation_cr_types.py
git commit -m "feat: aws revoke_exposed_credential reconstitution rollback + unit tests"
```

---

## Task 4: Live Smoke Phase — `phase_credential_revocation_live`

Add a new smoke phase to `backend/tests/smoke/test_feature_smoke_live.py` covering all 5 connectors. Each sub-phase: create throwaway → run CR via platform lifecycle → verify disabled → rollback → verify restored → cleanup.

Credentials for throwaway account creation are loaded from the DB via `get_connector_creds_from_db`. The connector IDs used for CRs are resolved at runtime via `client.get("/connectors")`.

**Files:**
- Modify: `backend/tests/smoke/test_feature_smoke_live.py`

- [ ] **Step 1: Add the GCP sub-phase helper**

In `test_feature_smoke_live.py`, add this function before the `ALL_PHASES` line:

```python
# ── Phase: CREDENTIAL_REVOCATION ──────────────────────────────────────────────

def _get_connector_id(client: NexplaneClient, connector_type: str) -> str:
    """Return ID of first connector with matching connector_type."""
    connectors = client.get("/connectors")
    matches = [c for c in connectors if c.get("connector_type") == connector_type]
    if not matches:
        fail(f"No {connector_type} connector found")
    return matches[0]["id"]


def _get_any_asset_id(client: NexplaneClient) -> str:
    """Return any asset ID — used as placeholder target for credential CRs."""
    assets = client.get("/assets", params={"limit": 1})
    items = assets if isinstance(assets, list) else assets.get("items", [])
    if not items:
        fail("No assets found — run discovery first")
    return items[0]["id"]


def _sub_phase_gcp(client: NexplaneClient, asset_id: str) -> None:
    import asyncio
    import threading
    import uuid as _uuid

    creds = get_connector_creds_from_db("gcp")
    if not creds:
        fail("GCP credentials not found in DB")

    connector_id = _get_connector_id(client, "gcp")
    suffix = str(_uuid.uuid4())[:8]
    sa_name = f"nexplane-smoke-{suffix}"
    project = creds.get("project_id", "")
    sa_email = f"{sa_name}@{project}.iam.gserviceaccount.com"

    # Create temp SA
    temp_sa_holder = [None]

    def _create_sa():
        import json as _json
        from google.oauth2 import service_account as _sa
        from google.cloud import iam_admin_v1
        key_json = _json.loads(creds["service_account_key_json"])
        gcp_creds = _sa.Credentials.from_service_account_info(
            key_json, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        iam_client = iam_admin_v1.IAMClient(credentials=gcp_creds)
        sa = iam_client.create_service_account(request={
            "name": f"projects/{project}",
            "account_id": sa_name,
            "service_account": {"display_name": "nexplane-smoke-temp"},
        })
        temp_sa_holder[0] = (iam_client, gcp_creds, sa.email)

    t = threading.Thread(target=lambda: asyncio.run(_create_sa()) if asyncio.iscoroutinefunction(_create_sa) else _create_sa())
    t.start(); t.join()
    iam_client, gcp_creds, created_email = temp_sa_holder[0]
    print(f"  GCP: created temp SA {created_email}", flush=True)

    try:
        # Run disable CR
        cr_id = client.create_cr(
            "Smoke: disable GCP service account",
            "gcp_disable_service_account",
            asset_id,
            {"service_account_email": created_email, "_locked_connector_type": "gcp"},
            connector_id=connector_id,
        )
        client.post(f"/change-requests/{cr_id}/plan")
        client.post(f"/change-requests/{cr_id}/submit-for-approval")
        client.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke"})
        client.post(f"/change-requests/{cr_id}/execute")
        client._wait_timeout(cr_id, "GCP disable SA", 120)

        # Verify disabled
        def _verify_disabled():
            from google.cloud import iam_admin_v1
            c = iam_admin_v1.IAMClient(credentials=gcp_creds)
            sa = c.get_service_account(request={"name": f"projects/{project}/serviceAccounts/{created_email}"})
            assert sa.disabled, f"SA {created_email} should be disabled"
        _verify_disabled()
        log("GCP SA disabled ✓")

        # Rollback
        client.rollback_cr(cr_id, "GCP restore SA")

        # Verify re-enabled
        def _verify_enabled():
            from google.cloud import iam_admin_v1
            c = iam_admin_v1.IAMClient(credentials=gcp_creds)
            sa = c.get_service_account(request={"name": f"projects/{project}/serviceAccounts/{created_email}"})
            assert not sa.disabled, f"SA {created_email} should be re-enabled after rollback"
        _verify_enabled()
        log("GCP SA re-enabled after rollback ✓")

    finally:
        # Cleanup
        try:
            iam_client.delete_service_account(request={"name": f"projects/{project}/serviceAccounts/{created_email}"})
            print(f"  GCP: deleted temp SA {created_email}", flush=True)
        except Exception as e:
            print(f"  GCP: cleanup warning: {e}", flush=True)
```

- [ ] **Step 2: Add the Azure AD sub-phase helper**

Add immediately after the GCP sub-phase function:

```python
def _sub_phase_azure_ad(client: NexplaneClient, asset_id: str) -> None:
    import asyncio
    import threading
    import uuid as _uuid

    creds = get_connector_creds_from_db("azure_ad")
    if not creds:
        fail("Azure AD credentials not found in DB")

    connector_id = _get_connector_id(client, "azure_ad")
    suffix = str(_uuid.uuid4())[:8]

    from app.connectors.executors.azure_ad.azure_ad_client import AzureADClient
    az_client = AzureADClient(creds["tenant_id"], creds["client_id"], creds["client_secret"])

    # Resolve tenant domain for UPN
    domain_holder = [None]
    def _get_domain():
        import asyncio as _aio
        async def _fetch():
            token = await az_client._get_token()
            import httpx
            async with httpx.AsyncClient() as hc:
                r = await hc.get(
                    "https://graph.microsoft.com/v1.0/organization",
                    headers={"Authorization": f"Bearer {token}"},
                )
                r.raise_for_status()
                domains = r.json().get("value", [{}])[0].get("verifiedDomains", [])
                default = next((d["name"] for d in domains if d.get("isDefault")), None)
                return default or domains[0]["name"]
        domain_holder[0] = _aio.run(_fetch())
    t = threading.Thread(target=_get_domain)
    t.start(); t.join()
    domain = domain_holder[0]

    upn = f"nexplane-smoke-{suffix}@{domain}"
    user_id_holder = [None]

    def _create_user():
        async def _c():
            user = await az_client.create_user(
                display_name=f"nexplane-smoke-{suffix}",
                upn=upn,
                password=f"NxSmoke{suffix}!",
                force_change_password=False,
            )
            user_id_holder[0] = user["id"]
        asyncio.run(_c())
    t = threading.Thread(target=_create_user)
    t.start(); t.join()
    user_id = user_id_holder[0]
    print(f"  Azure AD: created temp user {upn} ({user_id})", flush=True)

    try:
        # Run disable CR
        cr_id = client.create_cr(
            "Smoke: disable Azure AD user",
            "azure_ad_disable_user",
            asset_id,
            {"user_identifier": user_id, "_locked_connector_type": "azure_ad"},
            connector_id=connector_id,
        )
        client.post(f"/change-requests/{cr_id}/plan")
        client.post(f"/change-requests/{cr_id}/submit-for-approval")
        client.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke"})
        client.post(f"/change-requests/{cr_id}/execute")
        client._wait_timeout(cr_id, "Azure AD disable user", 120)

        # Verify disabled
        def _verify_disabled():
            async def _v():
                u = await az_client.get_user(user_id)
                assert not u.get("accountEnabled"), f"User {user_id} should be disabled"
            asyncio.run(_v())
        _verify_disabled()
        log("Azure AD user disabled ✓")

        # Rollback
        client.rollback_cr(cr_id, "Azure AD restore user")

        # Verify re-enabled
        def _verify_enabled():
            async def _v():
                u = await az_client.get_user(user_id)
                assert u.get("accountEnabled"), f"User {user_id} should be re-enabled"
            asyncio.run(_v())
        _verify_enabled()
        log("Azure AD user re-enabled after rollback ✓")

    finally:
        def _delete():
            async def _d():
                await az_client.delete_user(user_id)
            asyncio.run(_d())
        t = threading.Thread(target=_delete)
        t.start(); t.join()
        print(f"  Azure AD: deleted temp user {upn}", flush=True)
```

- [ ] **Step 3: Add the LDAP sub-phase helper**

```python
def _sub_phase_ldap(client: NexplaneClient, asset_id: str) -> None:
    import uuid as _uuid

    creds = get_connector_creds_from_db("ldap")
    if not creds:
        fail("LDAP credentials not found in DB")

    connector_id = _get_connector_id(client, "ldap")
    suffix = str(_uuid.uuid4())[:8]
    username = f"nxsmoke{suffix}"
    password = f"NxSmoke{suffix}!"

    from app.connectors.executors.ldap._client import LDAPClient
    ldap_client = LDAPClient(
        host=creds.get("host") or creds.get("hostname"),
        port=int(creds.get("port", 389)),
        bind_dn=creds.get("bind_dn", ""),
        bind_password=creds.get("bind_password") or creds.get("password", ""),
        base_dn=creds.get("base_dn", "dc=example,dc=com"),
        use_ssl=creds.get("use_ssl", False),
    )

    # Create temp user
    result = ldap_client.create_user(username=username, display_name=f"nexplane-smoke-{suffix}", password=password)
    if not result.get("success"):
        fail(f"LDAP: failed to create temp user: {result}")
    print(f"  LDAP: created temp user {username}", flush=True)

    try:
        # Run disable CR
        cr_id = client.create_cr(
            "Smoke: disable LDAP user",
            "ldap_disable_user",
            asset_id,
            {"username": username, "_locked_connector_type": "ldap"},
            connector_id=connector_id,
        )
        client.post(f"/change-requests/{cr_id}/plan")
        client.post(f"/change-requests/{cr_id}/submit-for-approval")
        client.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke"})
        client.post(f"/change-requests/{cr_id}/execute")
        client._wait_timeout(cr_id, "LDAP disable user", 120)

        # Verify disabled — bind should fail
        can_bind = ldap_client.verify_bind(username, password)
        assert not can_bind, f"LDAP user {username} should not be able to bind after disable"
        log("LDAP user disabled (bind rejected) ✓")

        # Rollback
        client.rollback_cr(cr_id, "LDAP restore user")

        # Verify re-enabled — bind should succeed
        can_bind_after = ldap_client.verify_bind(username, password)
        assert can_bind_after, f"LDAP user {username} should be able to bind after rollback"
        log("LDAP user re-enabled after rollback ✓")

    finally:
        ldap_client.delete_user(username)
        print(f"  LDAP: deleted temp user {username}", flush=True)
```

- [ ] **Step 4: Add the OCI sub-phase helper**

```python
def _sub_phase_oci(client: NexplaneClient, asset_id: str) -> None:
    import uuid as _uuid

    creds = get_connector_creds_from_db("oci")
    if not creds:
        fail("OCI credentials not found in DB")

    connector_id = _get_connector_id(client, "oci")
    suffix = str(_uuid.uuid4())[:8]
    username = f"nexplane-smoke-{suffix}"

    from app.connectors.executors.oci._client import get_identity_client
    oci_client = get_identity_client(creds)

    import oci as _oci
    compartment_id = creds.get("tenancy")

    # Create temp user
    user_resp = oci_client.create_user(_oci.identity.models.CreateUserDetails(
        compartment_id=compartment_id,
        name=username,
        description="nexplane smoke test temp user",
    ))
    user_id = user_resp.data.id
    print(f"  OCI: created temp user {username} ({user_id})", flush=True)

    try:
        # Run disable CR
        cr_id = client.create_cr(
            "Smoke: disable OCI IAM user",
            "oci_iam_user_disable",
            asset_id,
            {"user_id": user_id, "_locked_connector_type": "oci"},
            connector_id=connector_id,
        )
        client.post(f"/change-requests/{cr_id}/plan")
        client.post(f"/change-requests/{cr_id}/submit-for-approval")
        client.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke"})
        client.post(f"/change-requests/{cr_id}/execute")
        client._wait_timeout(cr_id, "OCI disable IAM user", 120)

        # Verify disabled
        user_state = oci_client.get_user(user_id).data
        caps = user_state.capabilities
        assert not caps.can_use_api_keys, f"OCI user {user_id} should have api_keys disabled"
        log("OCI user disabled ✓")

        # Rollback
        client.rollback_cr(cr_id, "OCI restore user")

        # Verify re-enabled
        user_state_after = oci_client.get_user(user_id).data
        caps_after = user_state_after.capabilities
        assert caps_after.can_use_api_keys, f"OCI user {user_id} should have api_keys re-enabled"
        log("OCI user re-enabled after rollback ✓")

    finally:
        try:
            oci_client.delete_user(user_id)
            print(f"  OCI: deleted temp user {username}", flush=True)
        except Exception as e:
            print(f"  OCI: cleanup warning: {e}", flush=True)
```

- [ ] **Step 5: Add the AWS sub-phase helper**

```python
def _sub_phase_aws(client: NexplaneClient, asset_id: str) -> None:
    import uuid as _uuid

    creds = get_connector_creds_from_db("aws")
    if not creds:
        fail("AWS credentials not found in DB")

    connector_id = _get_connector_id(client, "aws")
    suffix = str(_uuid.uuid4())[:8]
    username = f"nexplane-smoke-{suffix}"

    import boto3
    iam = boto3.client(
        "iam",
        aws_access_key_id=creds["aws_access_key_id"],
        aws_secret_access_key=creds["aws_secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )

    # Create temp user + access key
    iam.create_user(UserName=username, Tags=[{"Key": "nxp-smoke-temp", "Value": "true"}])
    key_resp = iam.create_access_key(UserName=username)
    access_key_id = key_resp["AccessKey"]["AccessKeyId"]
    print(f"  AWS: created temp user {username}, key {access_key_id}", flush=True)

    try:
        # Run revoke CR
        cr_id = client.create_cr(
            "Smoke: revoke exposed AWS IAM key",
            "revoke_exposed_credential",
            asset_id,
            {
                "credential_type": "aws_iam_key",
                "credential_id": access_key_id,
                "_locked_connector_type": "aws",
            },
            connector_id=connector_id,
        )
        client.post(f"/change-requests/{cr_id}/plan")
        client.post(f"/change-requests/{cr_id}/submit-for-approval")
        client.post(f"/change-requests/{cr_id}/approve", json={"decision": "approved", "comment": "smoke"})
        client.post(f"/change-requests/{cr_id}/execute")
        cr = client._wait_timeout(cr_id, "AWS revoke IAM key", 120)

        # Verify key is gone
        keys_after = iam.list_access_keys(UserName=username)["AccessKeyMetadata"]
        key_ids_after = [k["AccessKeyId"] for k in keys_after]
        assert access_key_id not in key_ids_after, f"Key {access_key_id} should be deleted"
        log("AWS IAM key revoked ✓")

        # Rollback (reconstitution — creates new key)
        client.rollback_cr(cr_id, "AWS reconstitute access key")

        # Verify a new key exists for the user
        keys_reconstituted = iam.list_access_keys(UserName=username)["AccessKeyMetadata"]
        active_keys = [k for k in keys_reconstituted if k["Status"] == "Active"]
        assert active_keys, f"Expected a new active key for {username} after reconstitution rollback"
        new_key_id = active_keys[0]["AccessKeyId"]
        assert new_key_id != access_key_id, "Reconstitution should create a new key, not restore the old one"
        log(f"AWS access reconstituted via new key {new_key_id} ✓")

    finally:
        # Delete all keys then delete user
        try:
            for k in iam.list_access_keys(UserName=username)["AccessKeyMetadata"]:
                iam.delete_access_key(UserName=username, AccessKeyId=k["AccessKeyId"])
            iam.delete_user(UserName=username)
            print(f"  AWS: deleted temp user {username}", flush=True)
        except Exception as e:
            print(f"  AWS: cleanup warning: {e}", flush=True)
```

- [ ] **Step 6: Add the phase coordinator function**

```python
def phase_credential_revocation_live(client: NexplaneClient) -> None:
    print("\n[CREDENTIAL_REVOCATION] Live credential disable/revoke + rollback across 5 connectors", flush=True)
    asset_id = _get_any_asset_id(client)

    print("\n  [GCP] disable_service_account", flush=True)
    _sub_phase_gcp(client, asset_id)

    print("\n  [Azure AD] azure_ad_disable_user", flush=True)
    _sub_phase_azure_ad(client, asset_id)

    print("\n  [LDAP] ldap_disable_user", flush=True)
    _sub_phase_ldap(client, asset_id)

    print("\n  [OCI] oci_iam_user_disable", flush=True)
    _sub_phase_oci(client, asset_id)

    print("\n  [AWS] revoke_exposed_credential (reconstitution rollback)", flush=True)
    _sub_phase_aws(client, asset_id)

    print("[CREDENTIAL_REVOCATION] PASSED", flush=True)
```

- [ ] **Step 7: Register the phase in ALL_PHASES and PHASE_FNS**

Find and update the `ALL_PHASES` and `PHASE_FNS` blocks:

```python
ALL_PHASES = ["VULN_MITIGATION", "CREDENTIAL_EXPIRY", "MCP_AGENT_TOKENS", "CREDENTIAL_REVOCATION"]

PHASE_FNS = {
    "VULN_MITIGATION": phase_vuln_mitigation,
    "CREDENTIAL_EXPIRY": phase_credential_expiry,
    "MCP_AGENT_TOKENS": phase_mcp_agent_tokens,
    "CREDENTIAL_REVOCATION": phase_credential_revocation_live,
}
```

Also update the `main()` parser description:

```python
parser = make_base_parser("Feature smoke tests: VULN_MITIGATION, CREDENTIAL_EXPIRY, MCP_AGENT_TOKENS, CREDENTIAL_REVOCATION")
```

- [ ] **Step 8: SCP to EC2 and run the smoke phase**

From the EC2 runner, sync the files:

```bash
# From laptop (PowerShell):
scp -i ~/.ssh/id_ed25519 -r "f:/Nexplane/nexplane/backend/" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/"

# From EC2 runner:
cd /home/ec2-user/nexplane
docker compose exec backend python tests/smoke/test_feature_smoke_live.py \
    --base-url http://localhost:8000 \
    --email admin@acme.example \
    --password admin123 \
    --phases CREDENTIAL_REVOCATION
```

Expected output:
```
[CREDENTIAL_REVOCATION] Live credential disable/revoke + rollback across 5 connectors

  [GCP] disable_service_account
  GCP: created temp SA nexplane-smoke-XXXXXXXX@...
  ✓ GCP SA disabled ✓
  ✓ GCP SA re-enabled after rollback ✓
  GCP: deleted temp SA ...

  [Azure AD] azure_ad_disable_user
  ...
  ✓ Azure AD user disabled ✓
  ✓ Azure AD user re-enabled after rollback ✓

  [LDAP] ldap_disable_user
  ...
  ✓ LDAP user disabled (bind rejected) ✓
  ✓ LDAP user re-enabled after rollback ✓

  [OCI] oci_iam_user_disable
  ...
  ✓ OCI user disabled ✓
  ✓ OCI user re-enabled after rollback ✓

  [AWS] revoke_exposed_credential (reconstitution rollback)
  ...
  ✓ AWS IAM key revoked ✓
  ✓ AWS access reconstituted via new key AKIA... ✓

[CREDENTIAL_REVOCATION] PASSED
```

- [ ] **Step 9: Commit**

```bash
git add backend/tests/smoke/test_feature_smoke_live.py
git commit -m "feat: credential revocation live smoke phase — all 5 connectors + reconstitution rollback verified"
```

---

## Self-Review

**Spec coverage check:**
- ✅ AWS executor reconstitution rollback — Task 3
- ✅ Unit tests for rollback logic — Task 3
- ✅ GCP sub-phase (create SA → disable → verify → rollback → verify → delete) — Task 4
- ✅ Azure AD sub-phase — Task 4
- ✅ LDAP sub-phase (including `ldap_disable_user` ChangeType gap fixed) — Tasks 2+4
- ✅ OCI sub-phase — Task 4
- ✅ AWS sub-phase (reconstitution rollback verified live) — Task 4
- ✅ Existing connector credentials never touched — all sub-phases create+delete throwaway accounts
- ✅ Full CR lifecycle (create→plan→approve→execute→rollback) — Task 4

**Gaps fixed beyond spec:**
- `revoke_exposed_credential` had no `steps` in its change type definition, blocking planning engine — fixed in Task 1
- `ldap_disable_user` was not a registered ChangeType — fixed in Task 2
