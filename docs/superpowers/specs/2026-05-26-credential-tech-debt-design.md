# Credential Tech Debt Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix three tech debt items from the 2026-05-26 credential lifecycle session: wire the SSH authorized_keys audit stub, fix the blocking IAM key age check, and add GCP/Azure/LDAP credential revocation paths.

**Scope:** Three independent fixes, shipped as one plan. No new models, no migrations, no UI changes.

---

## A1 — SSH authorized_keys Audit

### Problem
`_run_ssh_authorized_keys_audit(asset)` in `credential_expiry_worker.py` is a stub that returns `[]`. No SSH executor file exists for this operation.

### Design

**New file:** `backend/app/connectors/executors/ssh/authorized_keys_audit.py`

Uses the existing `get_ssh_client(creds)` pattern (same as `check_prerequisites.py`, `install_agent.py`). Runs:
```
cat /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys 2>/dev/null
```
Parses each non-comment line into:
```python
{"key_type": str, "key_fingerprint": str, "comment": str, "added_date": str | None}
```
`added_date` is extracted from `comment` if it contains a `YYYY-MM-DD` pattern (common convention for tracked keys). `key_fingerprint` is the key's public material truncated to 16 chars (not a cryptographic hash — just for display). Returns `{"keys": [...], "host": hostname}`. Rollback: read-only, returns `{"rolled_back": False, "reason": "read-only"}`.

**Worker wiring:** `_run_ssh_authorized_keys_audit(asset, db)` gains a `db` parameter. It:
1. Loads the SSH connector via `asset.connector_id` using `db.get(Connector, asset.connector_id)`
2. If no connector or connector type is not `ssh`, returns `[]`
3. Calls the executor's `execute({}, [str(asset.id)], connector)` directly (same pattern as `_check_vault_leases` calling `VaultClient.from_connector`)
4. Returns `result.get("keys", [])`

`_check_ssh_key_age(db)` updated to pass `db` to `_run_ssh_authorized_keys_audit`.

### Credential key names (SSH connector)
`creds["host"]`, `creds["username"]`, `creds["password"]` or `creds["private_key"]` — same as all other SSH executors via `get_ssh_client`.

---

## A2 — IAM Key Age Check: Non-Blocking + Connector-Scoped

### Problem
`_check_iam_key_age` uses a raw ambient `boto3.client("iam")` — it ignores registered AWS connectors and their scoped credentials. It also runs sync boto3 paginator calls directly in the async event loop, blocking uvicorn for the duration.

### Design

**Rewrite `_check_iam_key_age(db)`:**

1. Query all `aws` connectors: `await _get_connectors_by_type(db, "aws")`
2. For each connector, extract `creds = connector.credentials` and build a sync helper:
   ```python
   def _list_old_keys(creds: dict) -> list[tuple[str, str, int]]:
       """Returns list of (username, key_id, age_days) for keys >= 90 days old."""
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
3. Run it off the event loop: `old_keys = await loop.run_in_executor(None, _list_old_keys, creds)`
4. For each old key, call `_discover_api_key_consumers` and `_create_expiry_finding` as before.
5. Exceptions caught per-connector (same pattern as `_check_vault_leases`).

`_list_old_keys` is a module-level function (not nested) so it's easily unit-testable.

---

## A3 — revoke_exposed_credential: GCP, Azure, LDAP

### Problem
`revoke_exposed_credential.py` raises `ValueError` for any `credential_type` other than `aws_iam_key` and `vault_token`.

### Design

Three new branches added before the final `raise ValueError`:

**`gcp_service_account_key`**
Uses `googleapiclient.discovery.build("iam", "v1", credentials=...)` with a `google.oauth2.service_account.Credentials` built from `json.loads(creds["service_account_key_json"])`. Calls:
```python
service.projects().serviceAccounts().keys().delete(
    name=f"projects/-/serviceAccounts/-/keys/{credential_id}"
).execute()
```
Returns `{"success": True, "rolled_back_available": False}`.

**`azure_client_secret`**
Uses `azure.identity.ClientSecretCredential(creds["tenant_id"], creds["client_id"], creds["client_secret"])` + raw `httpx` DELETE to the Graph API (avoids adding `msgraph-sdk` dependency since the Azure executors already use raw httpx for Graph calls — see `rotate_service_principal_secret.py`):
```
DELETE https://graph.microsoft.com/v1.0/applications/{credential_id}/passwordCredentials/{key_id}
```
`credential_id` is expected as `"{app_id}/{key_id}"` (slash-separated). Returns `{"success": True, "rolled_back_available": False}`.

**`ldap_password`**
Uses `ldap3` (already in codebase). Builds connection via `creds["server"]`, `creds["bind_dn"]`, `creds["bind_password"]`. `credential_id` is the user DN. Disables the account by setting `userAccountControl` to `514` (disabled) via `connection.modify(dn, {"userAccountControl": [(MODIFY_REPLACE, [514])]})`. Returns `{"success": True, "rolled_back_available": False}`.

All three return the same shape. Rollback already returns `{"rolled_back": False, "reason": "Credential revocation is permanent — no rollback available"}` — no change needed.

### Credential key names
- GCP: `creds["service_account_key_json"]` (JSON string) — from `gcp/_client.py`
- Azure: `creds["tenant_id"]`, `creds["client_id"]`, `creds["client_secret"]` — from `azure/tag_resource.py`
- LDAP/AD: `creds["server"]`, `creds["bind_dn"]`, `creds["bind_password"]` — from `active_directory/_client.py`

---

## Testing

### Unit tests (new file: `tests/test_credential_tech_debt.py`)

**A1:**
- `test_authorized_keys_audit_parse_keys` — mock SSH client returning sample authorized_keys content; verify parsed output shape
- `test_authorized_keys_audit_extracts_date_from_comment` — verify YYYY-MM-DD extraction from comment
- `test_run_ssh_authorized_keys_audit_no_connector` — asset with no connector_id returns `[]`
- `test_check_ssh_key_age_passes_db` — verify `_check_ssh_key_age` passes `db` to `_run_ssh_authorized_keys_audit`

**A2:**
- `test_list_old_keys_filters_inactive` — `_list_old_keys` skips inactive keys
- `test_list_old_keys_age_threshold` — keys < 90 days not returned
- `test_check_iam_key_age_uses_connector_creds` — verify boto3 client is built from connector creds, not ambient
- `test_check_iam_key_age_runs_in_executor` — verify sync call is wrapped in `run_in_executor`

**A3:**
- `test_revoke_gcp_service_account_key` — mock `googleapiclient.discovery.build`; verify `.delete().execute()` called with correct key name
- `test_revoke_azure_client_secret` — mock httpx DELETE; verify Graph URL and auth token
- `test_revoke_ldap_password` — mock ldap3 Connection; verify `modify` called with `userAccountControl: 514`
- `test_revoke_unsupported_type_still_raises` — `ValueError` for unknown types

### Smoke
- A1: `_run_ssh_authorized_keys_audit` called against a live SSH-connected asset; no-crash + returns a list (may be empty if no authorized_keys files)
- A2: `_check_iam_key_age` called against the live AWS connector; no-crash + runs in < 30s (proves non-blocking)
- A3: Mock-path smoke only — all three new types tested with `connector.credentials = None` (mock mode returns `{"success": True, "mock": True}`); no `ValueError` raised

The A3 smoke uses mock mode because GCP/Azure/LDAP test instances are not provisioned. Full live smoke for these would require provisioned connector instances — deferred to connector-specific smoke sessions.

---

## Files Changed

| Action | Path |
|--------|------|
| Create | `backend/app/connectors/executors/ssh/authorized_keys_audit.py` |
| Modify | `backend/app/workers/credential_expiry_worker.py` |
| Modify | `backend/app/connectors/executors/aws/revoke_exposed_credential.py` |
| Create | `backend/tests/test_credential_tech_debt.py` |
| Modify | `backend/tests/smoke/test_feature_smoke_live.py` (add A1/A2/A3 smoke assertions to CREDENTIAL_EXPIRY phase) |
