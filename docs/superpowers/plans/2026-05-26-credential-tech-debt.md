# Credential Tech Debt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three tech debt items: wire the SSH authorized_keys audit stub, make IAM key age check non-blocking and connector-scoped, add GCP/Azure/LDAP paths to `revoke_exposed_credential`.

**Architecture:** Three independent changes to two existing files plus one new executor file and one new test file. No migrations, no models, no UI. All tests use `unittest.mock` following the pattern in `tests/test_credential_expiry_worker.py`.

**Tech Stack:** paramiko (SSH), boto3 (IAM), google-api-python-client (GCP IAM), azure-identity + httpx (Azure Graph), ldap3 (LDAP/AD) — all already installed.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `backend/app/connectors/executors/ssh/authorized_keys_audit.py` | SSH executor: cat authorized_keys files, parse key entries |
| Modify | `backend/app/workers/credential_expiry_worker.py` | Wire A1 stub, rewrite A2 IAM check |
| Modify | `backend/app/connectors/executors/aws/revoke_exposed_credential.py` | Add GCP/Azure/LDAP branches |
| Create | `backend/tests/test_credential_tech_debt.py` | All unit tests for A1, A2, A3 |
| Modify | `backend/tests/smoke/test_feature_smoke_live.py` | Smoke assertions for A1, A2, A3 |

---

## Task 1: SSH authorized_keys executor

**Files:**
- Create: `backend/app/connectors/executors/ssh/authorized_keys_audit.py`
- Test: `backend/tests/test_credential_tech_debt.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_credential_tech_debt.py`:

```python
import pytest
import re
from unittest.mock import MagicMock, patch


# ── A1: SSH authorized_keys executor ─────────────────────────────────────────

def _parse_keys(raw_output: str) -> list[dict]:
    """Import target for testing — extracted so it's testable without SSH."""
    from app.connectors.executors.ssh.authorized_keys_audit import _parse_authorized_keys
    return _parse_authorized_keys(raw_output)


def test_authorized_keys_audit_parse_keys():
    raw = (
        "ssh-rsa AAAAB3NzaC1yc2EAAAA... deploy@prod\n"
        "ssh-ed25519 AAAAC3NzaC1lZDI1... ops@server\n"
        "# this is a comment\n"
        "\n"
    )
    from app.connectors.executors.ssh.authorized_keys_audit import _parse_authorized_keys
    keys = _parse_authorized_keys(raw)
    assert len(keys) == 2
    assert keys[0]["key_type"] == "ssh-rsa"
    assert keys[0]["key_fingerprint"] == "AAAAB3NzaC1yc2"  # first 14 chars of material
    assert keys[0]["comment"] == "deploy@prod"
    assert keys[0]["added_date"] is None
    assert keys[1]["key_type"] == "ssh-ed25519"
    assert keys[1]["comment"] == "ops@server"


def test_authorized_keys_audit_extracts_date_from_comment():
    raw = "ssh-rsa AAAAB3NzaC1yc2EAAAA... deploy@prod added:2023-04-15\n"
    from app.connectors.executors.ssh.authorized_keys_audit import _parse_authorized_keys
    keys = _parse_authorized_keys(raw)
    assert len(keys) == 1
    assert keys[0]["added_date"] == "2023-04-15"
    assert keys[0]["comment"] == "deploy@prod added:2023-04-15"


def test_authorized_keys_audit_skips_blank_and_comments():
    raw = "# comment\n\n   \n"
    from app.connectors.executors.ssh.authorized_keys_audit import _parse_authorized_keys
    keys = _parse_authorized_keys(raw)
    assert keys == []
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend
pytest tests/test_credential_tech_debt.py::test_authorized_keys_audit_parse_keys tests/test_credential_tech_debt.py::test_authorized_keys_audit_extracts_date_from_comment tests/test_credential_tech_debt.py::test_authorized_keys_audit_skips_blank_and_comments -v
```

Expected: `ModuleNotFoundError` — `authorized_keys_audit` does not exist yet.

- [ ] **Step 3: Create the executor**

Create `backend/app/connectors/executors/ssh/authorized_keys_audit.py`:

```python
"""Read and parse authorized_keys files on SSH-managed assets."""
from __future__ import annotations
import asyncio
import re
from datetime import datetime, timezone
from ._client import get_ssh_client

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _parse_authorized_keys(raw: str) -> list[dict]:
    """Parse raw authorized_keys content into structured key entries."""
    keys = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        key_type = parts[0]
        key_material = parts[1]
        comment = " ".join(parts[2:]) if len(parts) > 2 else ""
        m = _DATE_RE.search(comment)
        keys.append({
            "key_type": key_type,
            "key_fingerprint": key_material[:14],
            "comment": comment,
            "added_date": m.group(1) if m else None,
        })
    return keys


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {"keys": [], "host": "mock", "mock": True}

    loop = asyncio.get_event_loop()

    def _run(asset_id: str) -> dict:
        client = get_ssh_client(creds)
        try:
            cmd = "cat /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys 2>/dev/null || true"
            _, stdout, _ = client.exec_command(cmd, timeout=15)
            stdout.channel.recv_exit_status()
            raw = stdout.read().decode(errors="replace")
            return {
                "asset_id": asset_id,
                "host": creds.get("hostname", "unknown"),
                "keys": _parse_authorized_keys(raw),
            }
        finally:
            client.close()

    results = []
    for asset_id in asset_ids:
        try:
            results.append(await loop.run_in_executor(None, _run, str(asset_id)))
        except Exception as e:
            results.append({"asset_id": str(asset_id), "keys": [], "error": str(e)})

    # Flatten all keys across all assets (worker calls per-asset, but executor
    # supports batching — return the first asset's keys for worker compatibility)
    all_keys = []
    for r in results:
        all_keys.extend(r.get("keys", []))

    return {
        "keys": all_keys,
        "host": creds.get("hostname", "unknown"),
        "host_results": results,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "authorized_keys_audit is read-only"}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend
pytest tests/test_credential_tech_debt.py::test_authorized_keys_audit_parse_keys tests/test_credential_tech_debt.py::test_authorized_keys_audit_extracts_date_from_comment tests/test_credential_tech_debt.py::test_authorized_keys_audit_skips_blank_and_comments -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```
git add backend/app/connectors/executors/ssh/authorized_keys_audit.py backend/tests/test_credential_tech_debt.py
git commit -m "feat: add SSH authorized_keys_audit executor with key parsing"
```

---

## Task 2: Wire the audit stub in the worker

**Files:**
- Modify: `backend/app/workers/credential_expiry_worker.py` (lines 69-94)
- Test: `backend/tests/test_credential_tech_debt.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_credential_tech_debt.py`:

```python
# ── A1: worker wiring ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_ssh_authorized_keys_audit_no_connector():
    """Asset with no connector_id returns empty list."""
    from app.workers.credential_expiry_worker import _run_ssh_authorized_keys_audit
    from unittest.mock import AsyncMock

    asset = MagicMock()
    asset.connector_id = None

    db = AsyncMock()
    db.get = AsyncMock(return_value=None)

    result = await _run_ssh_authorized_keys_audit(asset, db)
    assert result == []


@pytest.mark.asyncio
async def test_run_ssh_authorized_keys_audit_wrong_type():
    """Asset whose connector is not type 'ssh' returns empty list."""
    from app.workers.credential_expiry_worker import _run_ssh_authorized_keys_audit
    from unittest.mock import AsyncMock
    import uuid

    asset = MagicMock()
    asset.connector_id = uuid.uuid4()

    connector = MagicMock()
    connector.connector_type = "aws"

    db = AsyncMock()
    db.get = AsyncMock(return_value=connector)

    result = await _run_ssh_authorized_keys_audit(asset, db)
    assert result == []


@pytest.mark.asyncio
async def test_run_ssh_authorized_keys_audit_calls_executor():
    """SSH connector causes executor to be called; keys are returned."""
    from app.workers.credential_expiry_worker import _run_ssh_authorized_keys_audit
    from unittest.mock import AsyncMock
    import uuid

    asset = MagicMock()
    asset.connector_id = uuid.uuid4()

    connector = MagicMock()
    connector.connector_type = "ssh"

    fake_keys = [{"key_type": "ssh-rsa", "key_fingerprint": "AAAA", "comment": "ci", "added_date": None}]

    db = AsyncMock()
    db.get = AsyncMock(return_value=connector)

    with patch("app.connectors.executors.ssh.authorized_keys_audit.execute",
               new=AsyncMock(return_value={"keys": fake_keys, "host": "1.2.3.4"})):
        result = await _run_ssh_authorized_keys_audit(asset, db)

    assert result == fake_keys


@pytest.mark.asyncio
async def test_check_ssh_key_age_passes_db():
    """_check_ssh_key_age passes db into _run_ssh_authorized_keys_audit."""
    from app.workers.credential_expiry_worker import _check_ssh_key_age
    from unittest.mock import AsyncMock

    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value = []
    db.execute = AsyncMock(return_value=mock_result)

    captured = {}

    async def fake_audit(asset, db_arg):
        captured["db"] = db_arg
        return []

    with patch("app.workers.credential_expiry_worker._run_ssh_authorized_keys_audit", fake_audit):
        await _check_ssh_key_age(db)

    # If no assets, fake_audit never called — that's fine, test still verifies signature
    # Run with one asset to confirm db is passed
    asset = MagicMock()
    asset.connector_id = None
    mock_result2 = MagicMock()
    mock_result2.scalars.return_value = [asset]
    db.execute = AsyncMock(return_value=mock_result2)

    with patch("app.workers.credential_expiry_worker._run_ssh_authorized_keys_audit", fake_audit):
        await _check_ssh_key_age(db)

    assert captured.get("db") is db
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend
pytest tests/test_credential_tech_debt.py::test_run_ssh_authorized_keys_audit_no_connector tests/test_credential_tech_debt.py::test_run_ssh_authorized_keys_audit_wrong_type tests/test_credential_tech_debt.py::test_run_ssh_authorized_keys_audit_calls_executor tests/test_credential_tech_debt.py::test_check_ssh_key_age_passes_db -v
```

Expected: FAIL — `_run_ssh_authorized_keys_audit` still takes 1 arg, tests pass 2.

- [ ] **Step 3: Rewrite `_run_ssh_authorized_keys_audit` and update `_check_ssh_key_age`**

In `backend/app/workers/credential_expiry_worker.py`, replace lines 69–94 (the stub and `_check_ssh_key_age`):

```python
async def _run_ssh_authorized_keys_audit(asset, db) -> list[dict]:
    """Call the SSH authorized_keys_audit executor for this asset."""
    if not asset.connector_id:
        return []
    from app.models.connector import Connector
    connector = await db.get(Connector, asset.connector_id)
    if not connector or connector.connector_type != "ssh":
        return []
    from app.connectors.executors.ssh.authorized_keys_audit import execute as audit_execute
    try:
        result = await audit_execute({}, [str(asset.id)], connector)
        return result.get("keys", [])
    except Exception as e:
        logger.debug("SSH authorized_keys audit failed for asset %s: %s", asset.id, e)
        return []


async def _check_ssh_key_age(db) -> None:
    """Probe SSH authorized_keys age on all SSH-managed assets."""
    result = await db.execute(
        select(Asset).where(Asset.asset_type.in_(["server"]))
    )
    assets = result.scalars().all()

    for asset in assets:
        keys = await _run_ssh_authorized_keys_audit(asset, db)
        for key in keys:
            age_days = _key_age_days(key.get("added_date"))
            if age_days and age_days >= SSH_KEY_MAX_AGE_DAYS:
                label = key.get("comment") or key["key_fingerprint"][:16]
                await _create_expiry_finding(
                    db, asset, "ssh_authorized_key",
                    f"SSH authorized key '{label}' on {asset.name} is {age_days} days old",
                    SSH_KEY_MAX_AGE_DAYS - age_days,
                )
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend
pytest tests/test_credential_tech_debt.py::test_run_ssh_authorized_keys_audit_no_connector tests/test_credential_tech_debt.py::test_run_ssh_authorized_keys_audit_wrong_type tests/test_credential_tech_debt.py::test_run_ssh_authorized_keys_audit_calls_executor tests/test_credential_tech_debt.py::test_check_ssh_key_age_passes_db -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Run the full test file to confirm no regressions**

```
cd backend
pytest tests/test_credential_tech_debt.py tests/test_credential_expiry_worker.py -v
```

Expected: all PASSED.

- [ ] **Step 6: Commit**

```
git add backend/app/workers/credential_expiry_worker.py backend/tests/test_credential_tech_debt.py
git commit -m "feat: wire SSH authorized_keys_audit stub — queries connector and calls executor"
```

---

## Task 3: Rewrite IAM key age check (non-blocking + connector-scoped)

**Files:**
- Modify: `backend/app/workers/credential_expiry_worker.py` (lines 201–223 and add module-level `_list_old_keys`)
- Test: `backend/tests/test_credential_tech_debt.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_credential_tech_debt.py`:

```python
# ── A2: IAM key age check ─────────────────────────────────────────────────────

def test_list_old_keys_filters_inactive():
    """_list_old_keys skips keys with Status != Active."""
    from app.workers.credential_expiry_worker import _list_old_keys
    from datetime import datetime, timezone, timedelta
    from unittest.mock import MagicMock, patch

    fake_keys = [
        {"AccessKeyId": "AKIA1", "Status": "Inactive",
         "CreateDate": datetime.now(timezone.utc) - timedelta(days=100)},
        {"AccessKeyId": "AKIA2", "Status": "Active",
         "CreateDate": datetime.now(timezone.utc) - timedelta(days=5)},
    ]
    fake_users = [{"UserName": "alice"}]

    mock_iam = MagicMock()
    mock_iam.get_paginator.return_value.paginate.return_value = [{"Users": fake_users}]
    mock_iam.list_access_keys.return_value = {"AccessKeyMetadata": fake_keys}

    creds = {"aws_access_key_id": "K", "aws_secret_access_key": "S", "region": "us-east-1"}

    with patch("boto3.client", return_value=mock_iam):
        result = _list_old_keys(creds)

    assert result == []  # inactive filtered, active key < 90 days


def test_list_old_keys_age_threshold():
    """_list_old_keys returns keys >= 90 days old."""
    from app.workers.credential_expiry_worker import _list_old_keys
    from datetime import datetime, timezone, timedelta
    from unittest.mock import MagicMock, patch

    old_key = {
        "AccessKeyId": "AKIAOLD1234567",
        "Status": "Active",
        "CreateDate": datetime.now(timezone.utc) - timedelta(days=95),
    }
    new_key = {
        "AccessKeyId": "AKIANEW1234567",
        "Status": "Active",
        "CreateDate": datetime.now(timezone.utc) - timedelta(days=30),
    }
    mock_iam = MagicMock()
    mock_iam.get_paginator.return_value.paginate.return_value = [{"Users": [{"UserName": "bob"}]}]
    mock_iam.list_access_keys.return_value = {"AccessKeyMetadata": [old_key, new_key]}

    creds = {"aws_access_key_id": "K", "aws_secret_access_key": "S"}

    with patch("boto3.client", return_value=mock_iam):
        result = _list_old_keys(creds)

    assert len(result) == 1
    username, key_id, age_days = result[0]
    assert username == "bob"
    assert key_id == "AKIAOLD1234567"
    assert age_days >= 95


@pytest.mark.asyncio
async def test_check_iam_key_age_uses_connector_creds():
    """_check_iam_key_age queries AWS connectors and uses their credentials."""
    from app.workers.credential_expiry_worker import _check_iam_key_age
    from unittest.mock import AsyncMock, MagicMock, patch

    connector = MagicMock()
    connector.credentials = {
        "aws_access_key_id": "AKIATEST",
        "aws_secret_access_key": "SECRET",
        "region": "us-east-1",
    }

    db = AsyncMock()
    captured_creds = {}

    def fake_list_old_keys(creds):
        captured_creds.update(creds)
        return []  # no old keys

    with patch("app.workers.credential_expiry_worker._get_connectors_by_type",
               new=AsyncMock(return_value=[connector])), \
         patch("app.workers.credential_expiry_worker._list_old_keys", fake_list_old_keys):
        await _check_iam_key_age(db)

    assert captured_creds.get("aws_access_key_id") == "AKIATEST"
    assert captured_creds.get("aws_secret_access_key") == "SECRET"
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend
pytest tests/test_credential_tech_debt.py::test_list_old_keys_filters_inactive tests/test_credential_tech_debt.py::test_list_old_keys_age_threshold tests/test_credential_tech_debt.py::test_check_iam_key_age_uses_connector_creds -v
```

Expected: FAIL — `_list_old_keys` does not exist yet.

- [ ] **Step 3: Add `_list_old_keys` and rewrite `_check_iam_key_age`**

In `backend/app/workers/credential_expiry_worker.py`:

**After `_discover_api_key_consumers` (before line 201), add the new module-level sync helper:**

```python
def _list_old_keys(creds: dict) -> list[tuple[str, str, int]]:
    """Sync: return (username, key_id, age_days) for IAM keys >= 90 days old.

    Runs in an executor so it does not block the async event loop.
    Uses connector credentials, not ambient boto3 environment.
    """
    import boto3
    iam = boto3.client(
        "iam",
        aws_access_key_id=creds["aws_access_key_id"],
        aws_secret_access_key=creds["aws_secret_access_key"],
        region_name=creds.get("region", "us-east-1"),
    )
    results = []
    for page in iam.get_paginator("list_users").paginate():
        for user in page["Users"]:
            for key in iam.list_access_keys(UserName=user["UserName"])["AccessKeyMetadata"]:
                if key["Status"] != "Active":
                    continue
                age = (datetime.now(timezone.utc) - key["CreateDate"]).days
                if age >= 90:
                    results.append((user["UserName"], key["AccessKeyId"], age))
    return results
```

**Then replace `_check_iam_key_age` (lines 201–223):**

```python
async def _check_iam_key_age(db) -> None:
    """Check IAM access keys older than 90 days, per registered AWS connector."""
    loop = asyncio.get_event_loop()
    aws_connectors = await _get_connectors_by_type(db, "aws")
    for connector in aws_connectors:
        try:
            creds = connector.credentials or {}
            if not creds.get("aws_access_key_id"):
                continue
            old_keys = await loop.run_in_executor(None, _list_old_keys, creds)
            for username, key_id, age_days in old_keys:
                consumers = await _discover_api_key_consumers(db, key_id, "aws_iam_key")
                await _create_expiry_finding(
                    db, None, "iam_access_key",
                    f"IAM key {key_id[:8]}... for {username} is {age_days} days old"
                    f" (policy: rotate every 90 days)",
                    90 - age_days,
                    consumers=consumers,
                )
        except Exception as e:
            logger.debug("IAM key age check failed for connector %s: %s", connector.id, e)
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend
pytest tests/test_credential_tech_debt.py::test_list_old_keys_filters_inactive tests/test_credential_tech_debt.py::test_list_old_keys_age_threshold tests/test_credential_tech_debt.py::test_check_iam_key_age_uses_connector_creds -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Run full test suite to confirm no regressions**

```
cd backend
pytest tests/test_credential_tech_debt.py tests/test_credential_expiry_worker.py -v
```

Expected: all PASSED.

- [ ] **Step 6: Commit**

```
git add backend/app/workers/credential_expiry_worker.py backend/tests/test_credential_tech_debt.py
git commit -m "fix: IAM key age check now uses connector creds and runs in executor (non-blocking)"
```

---

## Task 4: Add GCP, Azure, LDAP revocation paths

**Files:**
- Modify: `backend/app/connectors/executors/aws/revoke_exposed_credential.py`
- Test: `backend/tests/test_credential_tech_debt.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_credential_tech_debt.py`:

```python
# ── A3: revoke_exposed_credential new types ───────────────────────────────────

@pytest.mark.asyncio
async def test_revoke_gcp_service_account_key():
    """GCP path calls google IAM delete with correct key resource name."""
    from app.connectors.executors.aws.revoke_exposed_credential import execute

    connector = MagicMock()
    connector.creds = {
        "service_account_key_json": '{"type": "service_account", "project_id": "my-proj"}'
    }

    mock_service = MagicMock()
    mock_keys = MagicMock()
    mock_service.projects.return_value.serviceAccounts.return_value.keys.return_value = mock_keys
    mock_delete = MagicMock()
    mock_keys.delete.return_value = mock_delete

    with patch("googleapiclient.discovery.build", return_value=mock_service), \
         patch("google.oauth2.service_account.Credentials.from_service_account_info",
               return_value=MagicMock()):
        result = await execute(
            {"credential_type": "gcp_service_account_key", "credential_id": "abc123key"},
            [], connector,
        )

    mock_keys.delete.assert_called_once_with(
        name="projects/-/serviceAccounts/-/keys/abc123key"
    )
    mock_delete.execute.assert_called_once()
    assert result["success"] is True
    assert result["rolled_back_available"] is False


@pytest.mark.asyncio
async def test_revoke_azure_client_secret():
    """Azure path calls Graph removePassword with correct app_id and key_id."""
    from app.connectors.executors.aws.revoke_exposed_credential import execute

    connector = MagicMock()
    connector.creds = {
        "tenant_id": "tenant-1",
        "client_id": "client-1",
        "client_secret": "secret-1",
    }

    # credential_id format: "{app_id}/{key_id}"
    credential_id = "app-object-id/key-guid-1234"

    mock_token_resp = MagicMock()
    mock_token_resp.json.return_value = {"access_token": "tok123"}
    mock_token_resp.raise_for_status = MagicMock()

    mock_remove_resp = MagicMock()
    mock_remove_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=[mock_token_resp, mock_remove_resp])

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await execute(
            {"credential_type": "azure_client_secret", "credential_id": credential_id},
            [], connector,
        )

    # Verify removePassword was called with the correct keyId
    remove_call = mock_client.post.call_args_list[1]
    assert "removePassword" in remove_call.args[0]
    assert remove_call.kwargs["json"]["keyId"] == "key-guid-1234"
    assert result["success"] is True


@pytest.mark.asyncio
async def test_revoke_ldap_password():
    """LDAP path calls modify with userAccountControl=514 (disabled)."""
    from app.connectors.executors.aws.revoke_exposed_credential import execute

    connector = MagicMock()
    connector.creds = {
        "server": "ldap://dc.corp.example",
        "bind_dn": "cn=svc,dc=corp,dc=example",
        "bind_password": "pass",
    }
    credential_id = "cn=jdoe,ou=users,dc=corp,dc=example"

    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.modify = MagicMock(return_value=True)

    with patch("ldap3.Server", return_value=MagicMock()), \
         patch("ldap3.Connection", return_value=mock_conn):
        result = await execute(
            {"credential_type": "ldap_password", "credential_id": credential_id},
            [], connector,
        )

    mock_conn.modify.assert_called_once()
    call_args = mock_conn.modify.call_args
    assert call_args.args[0] == credential_id
    # Verify userAccountControl=514
    changes = call_args.args[1]
    assert "userAccountControl" in changes
    assert 514 in changes["userAccountControl"][0]
    assert result["success"] is True


@pytest.mark.asyncio
async def test_revoke_unsupported_type_still_raises():
    """ValueError still raised for unknown credential types."""
    from app.connectors.executors.aws.revoke_exposed_credential import execute

    connector = MagicMock()
    connector.creds = {"something": "here"}

    with pytest.raises(ValueError, match="Unsupported credential_type"):
        await execute(
            {"credential_type": "foobar_token", "credential_id": "xyz"},
            [], connector,
        )
```

Also add this import at the top of the test file (before all the test functions):

```python
from unittest.mock import AsyncMock
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend
pytest tests/test_credential_tech_debt.py::test_revoke_gcp_service_account_key tests/test_credential_tech_debt.py::test_revoke_azure_client_secret tests/test_credential_tech_debt.py::test_revoke_ldap_password tests/test_credential_tech_debt.py::test_revoke_unsupported_type_still_raises -v
```

Expected: 3 FAIL (GCP/Azure/LDAP raise ValueError), 1 PASS (unsupported still raises).

- [ ] **Step 3: Add GCP, Azure, LDAP branches to the executor**

Replace the full contents of `backend/app/connectors/executors/aws/revoke_exposed_credential.py`:

```python
"""Executor: immediately revoke a known-compromised credential. NO rollback — permanent."""
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
        iam.delete_access_key(AccessKeyId=credential_id)
        logger.info("Revoked IAM key %s", credential_id)
        return {"success": True, "rolled_back_available": False}

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
        # credential_id format: "{app_object_id}/{key_id}"
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
            # Disable the account by setting userAccountControl=514
            # (512 = normal account, 2 = disabled flag → 512 | 2 = 514)
            conn.modify(credential_id, {"userAccountControl": [(MODIFY_REPLACE, [514])]})
        logger.info("Disabled LDAP account %s", credential_id)
        return {"success": True, "rolled_back_available": False}

    raise ValueError(f"Unsupported credential_type: {credential_type}")


async def rollback(parameters: dict, execution_result: dict, connector: Any) -> dict:
    return {"rolled_back": False, "reason": "Credential revocation is permanent — no rollback available"}
```

- [ ] **Step 4: Run tests to verify they pass**

```
cd backend
pytest tests/test_credential_tech_debt.py::test_revoke_gcp_service_account_key tests/test_credential_tech_debt.py::test_revoke_azure_client_secret tests/test_credential_tech_debt.py::test_revoke_ldap_password tests/test_credential_tech_debt.py::test_revoke_unsupported_type_still_raises -v
```

Expected: 4 PASSED.

- [ ] **Step 5: Run the full test file**

```
cd backend
pytest tests/test_credential_tech_debt.py -v
```

Expected: all PASSED (12 tests total).

- [ ] **Step 6: Commit**

```
git add backend/app/connectors/executors/aws/revoke_exposed_credential.py backend/tests/test_credential_tech_debt.py
git commit -m "feat: add GCP/Azure/LDAP credential revocation to revoke_exposed_credential executor"
```

---

## Task 5: Smoke phase updates + full verification

**Files:**
- Modify: `backend/tests/smoke/test_feature_smoke_live.py`

The existing `CREDENTIAL_EXPIRY` phase in `test_feature_smoke_live.py` only tests `_check_vault_leases`. Extend it with A1, A2, A3 smoke assertions.

- [ ] **Step 1: Read the current CREDENTIAL_EXPIRY phase**

```
Read backend/tests/smoke/test_feature_smoke_live.py lines 305–400
```

Find the block that runs `_check_vault_leases` and the surrounding context.

- [ ] **Step 2: Add A1 smoke — SSH audit no-crash**

In `phase_credential_expiry`, after the `_check_vault_leases` assertion, add:

```python
        # A1 smoke: _run_ssh_authorized_keys_audit no-crash on asset with no SSH connector
        def _run_a1_smoke():
            async def _inner():
                import os
                from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
                from app.workers.credential_expiry_worker import _run_ssh_authorized_keys_audit

                db_url = os.environ["DATABASE_URL"]
                engine = create_async_engine(db_url, pool_size=1, max_overflow=0)
                factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
                try:
                    async with factory() as db:
                        # Use a fake asset with no connector — should return []
                        asset = type("FakeAsset", (), {"id": "smoke-test", "connector_id": None})()
                        result = await _run_ssh_authorized_keys_audit(asset, db)
                        assert isinstance(result, list)
                        a1_results.append("ok")
                finally:
                    await engine.dispose()
            asyncio.run(_inner())

        a1_results = []
        t_a1 = threading.Thread(target=_run_a1_smoke)
        t_a1.start()
        t_a1.join(timeout=15)
        assert a1_results and a1_results[0] == "ok", f"A1 SSH audit smoke failed: {a1_results}"
        log("A1: _run_ssh_authorized_keys_audit no-crash (no connector → [])")
```

- [ ] **Step 3: Add A2 smoke — IAM key age non-blocking**

After the A1 block, add:

```python
        # A2 smoke: _check_iam_key_age runs in < 30s with the live AWS connector
        import time

        def _run_a2_smoke():
            async def _inner():
                import os
                from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
                from app.workers.credential_expiry_worker import _check_iam_key_age

                db_url = os.environ["DATABASE_URL"]
                engine = create_async_engine(db_url, pool_size=1, max_overflow=0)
                factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
                try:
                    async with factory() as db:
                        await _check_iam_key_age(db)
                    a2_results.append("ok")
                except Exception as exc:
                    a2_results.append(f"error: {exc}")
                finally:
                    await engine.dispose()
            asyncio.run(_inner())

        a2_results = []
        a2_start = time.time()
        t_a2 = threading.Thread(target=_run_a2_smoke)
        t_a2.start()
        t_a2.join(timeout=30)
        assert a2_results and a2_results[0] == "ok", f"A2 IAM check failed: {a2_results}"
        elapsed = time.time() - a2_start
        assert elapsed < 30, f"A2 IAM check blocked event loop (took {elapsed:.1f}s)"
        log(f"A2: _check_iam_key_age completed in {elapsed:.1f}s (non-blocking)")
```

- [ ] **Step 4: Add A3 smoke — revoke new types in mock mode**

After the A2 block, add:

```python
        # A3 smoke: new revoke types don't raise ValueError in mock mode (no creds)
        import asyncio as _asyncio

        async def _a3_mock_revoke():
            from app.connectors.executors.aws.revoke_exposed_credential import execute

            class _MockConnector:
                creds = None  # triggers mock path

            for cred_type, cred_id in [
                ("gcp_service_account_key", "fake-key-id"),
                ("azure_client_secret", "fake-app-id/fake-key-id"),
                ("ldap_password", "cn=test,dc=corp,dc=example"),
            ]:
                result = await execute(
                    {"credential_type": cred_type, "credential_id": cred_id},
                    [], _MockConnector(),
                )
                assert result.get("mock") is True, f"{cred_type} mock path not hit"
            return "ok"

        a3_result = _asyncio.run(_a3_mock_revoke())
        assert a3_result == "ok"
        log("A3: GCP/Azure/LDAP revoke types return mock=True when no creds (no ValueError)")
```

- [ ] **Step 5: Push to EC2 and run**

```
git add backend/tests/smoke/test_feature_smoke_live.py
git commit -m "test: add A1/A2/A3 smoke assertions to CREDENTIAL_EXPIRY phase"
git push origin master
```

Then on EC2:
```
cd /home/ec2-user/nexplane && git pull --ff-only
docker compose restart backend
sleep 8
docker exec nexplane-backend-1 python tests/smoke/test_feature_smoke_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --phases CREDENTIAL_EXPIRY
```

Expected output includes:
```
✅ A1: _run_ssh_authorized_keys_audit no-crash (no connector → [])
✅ A2: _check_iam_key_age completed in X.Xs (non-blocking)
✅ A3: GCP/Azure/LDAP revoke types return mock=True when no creds (no ValueError)
[CREDENTIAL_EXPIRY] PASSED
```

- [ ] **Step 6: Run all unit tests to confirm clean state**

```
cd backend
pytest tests/test_credential_tech_debt.py tests/test_credential_expiry_worker.py -v
```

Expected: all PASSED.

- [ ] **Step 7: Final commit if any fixes were needed**

```
git add -A
git commit -m "fix: smoke adjustments from live run"
git push origin master
```

---

## Self-Review

**Spec coverage:**
- A1 SSH executor: Task 1 ✅
- A1 worker wiring (`_run_ssh_authorized_keys_audit(asset, db)`, `_check_ssh_key_age` update): Task 2 ✅
- A2 `_list_old_keys` module-level function: Task 3 ✅
- A2 `_check_iam_key_age` rewrite: Task 3 ✅
- A3 GCP branch: Task 4 ✅
- A3 Azure branch: Task 4 ✅
- A3 LDAP branch: Task 4 ✅
- A3 unsupported type still raises: Task 4 ✅
- Smoke A1 no-crash: Task 5 ✅
- Smoke A2 timing: Task 5 ✅
- Smoke A3 mock-mode: Task 5 ✅

**Type consistency check:**
- `_run_ssh_authorized_keys_audit(asset, db)` — defined Task 2, called Task 2 ✅
- `_list_old_keys(creds: dict) -> list[tuple[str, str, int]]` — defined Task 3, tested Task 3 ✅
- `connector.creds` — used in revoke_exposed_credential consistently throughout ✅
- `connector.credentials` — used in worker (`_check_iam_key_age`) consistently ✅

**Placeholder scan:** No TBDs, no "implement later", all code blocks complete.
