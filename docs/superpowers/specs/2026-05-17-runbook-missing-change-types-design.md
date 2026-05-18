# Runbook Missing Change Types — Implementation Design

## Goal

Implement the 9 change types referenced in seeded runbook templates that currently have no executor, no catalog entry, and no ChangeType enum value. All 9 must have real connector implementations and rollback handlers. Delivery includes two levels of smoke coverage per connector group and per runbook template.

## Background

Three seeded runbook templates reference change types that don't exist yet:

| Template | Missing Steps |
|---|---|
| Engineer Onboarding | `create_ad_account`, `assign_okta_groups`, `add_github_org_member`, `send_welcome_email` |
| Incident Response: Account Compromise | `preserve_cloudtrail_logs`, `force_password_reset`, `close_incident_ticket` |
| Patch Campaign | `check_fleet_health`, `check_compliance` |

`close_incident_ticket` maps to the existing `close_incident.py` executor — it only needs an enum entry and catalog wiring.

## Architecture

Implementation is grouped by connector. Each group ships: ChangeType enum entries, executor file(s), rollback handler, catalog JSON update, and a connector-level smoke phase. After all groups are done, three runbook end-to-end smoke phases validate the full orchestration.

**Executor pattern** (all executors follow this exactly):
```python
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "...", "simulated": True}
    return await _real_execute(parameters, creds)

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    ...

async def _real_execute(parameters: dict, creds: dict) -> dict:
    # blocking I/O wrapped in run_in_executor
    ...
```

## Connector Groups

### Group 1 — Active Directory: `create_ad_account`

**New file:** `backend/app/connectors/executors/active_directory/create_user.py`

- Creates user via ldap3 `ADD` with attributes: `cn`, `sn`, `givenName`, `userPrincipalName`, `sAMAccountName`, `unicodePwd`, `userAccountControl=512`
- Parameters: `username`, `first_name`, `last_name`, `ou` (target OU DN), `temp_password`
- Returns: `{dn, username, created_at}`
- Rollback: `ldap3 DELETE` on `dn` from execution result — removes the account entirely
- Verify after create: `SEARCH` by `sAMAccountName` to confirm entry exists before returning

**Catalog update:** `backend/app/connectors/catalog/active_directory.json`
- Add action: `action_id: create_user`, `generic_action: create_ad_account`

**ChangeType enum:** add `create_ad_account`

---

### Group 2 — Okta: `assign_okta_groups`, `force_password_reset`

**New file:** `backend/app/connectors/executors/okta/assign_groups.py`

- For each group_id in `parameters["group_ids"]`: PUT `/api/v1/groups/{groupId}/users/{userId}`
- Parameters: `user_id`, `group_ids: list[str]`
- Returns: `{user_id, assigned_groups: [...], assigned_at}`
- Rollback: DELETE `/api/v1/groups/{groupId}/users/{userId}` for each group in `execution_result["assigned_groups"]`
- Uses existing `_client.py` `OktaClient`

**New file:** `backend/app/connectors/executors/okta/force_password_reset.py`

- POST `/api/v1/users/{userId}/lifecycle/reset_password?sendEmail=false`
- Parameters: `user_id` (Okta user ID or login)
- Returns: `{user_id, reset_at, reset_url}` (Okta returns a one-time reset URL)
- Rollback: `{"rolled_back": True, "note": "password reset cannot be undone; account remains accessible"}` — `NoRollbackNeeded` semantics; the reset itself is not harmful

**Catalog update:** `backend/app/connectors/catalog/okta.json`
- Add: `assign_groups -> assign_okta_groups`
- Add: `force_password_reset` already exists as action_id — verify `generic_action` is `force_password_reset`; add if missing

**ChangeType enum:** add `assign_okta_groups`, `force_password_reset`

---

### Group 3 — GitHub: `add_github_org_member`

**New file:** `backend/app/connectors/executors/github/add_org_member.py`

- Mirror of existing `remove_org_member.py`
- PUT `/orgs/{org}/memberships/{username}` with `{"role": parameters.get("role", "member")}`
- Parameters: `username`, `org`, `role` (default `"member"`)
- Returns: `{username, org, role, state, added_at}`
- Rollback: DELETE `/orgs/{org}/memberships/{username}` — removes membership

**Catalog update:** `backend/app/connectors/catalog/github.json`
- Add: `add_org_member -> add_github_org_member`

**ChangeType enum:** add `add_github_org_member`

---

### Group 4 — SMTP: `send_welcome_email`

**New connector directory:** `backend/app/connectors/executors/smtp/`

**New files:**
- `__init__.py` (empty)
- `_client.py` — builds `smtplib.SMTP` or `smtplib.SMTP_SSL` connection from creds
- `send_welcome_email.py` — sends onboarding email

**Credential record fields** (stored in platform connector credentials):
- `host`, `port` (int), `username`, `password`, `use_tls` (bool), `from_address`

**`send_welcome_email.py` parameters:**
- `to_address`, `recipient_name`, `temp_password`, `login_url`
- `account_dn` (optional — used by rollback to reference the AD account for removal)
- `okta_user_id` (optional — used by rollback for Okta deactivation)

**Execute:**
```python
def _sync_send(creds, to_address, subject, body):
    if creds.get("use_tls"):
        smtp = smtplib.SMTP_SSL(creds["host"], creds["port"])
    else:
        smtp = smtplib.SMTP(creds["host"], creds["port"])
        smtp.starttls()
    smtp.login(creds["username"], creds["password"])
    msg = EmailMessage()
    msg["From"] = creds["from_address"]
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.set_content(body)
    smtp.send_message(msg)
    smtp.quit()
```

Returns: `{to_address, sent_at, subject}`

**Rollback:** The email itself cannot be unsent. Rollback disables/removes the account that was created upstream:
- If `execution_result["account_dn"]` present: call AD `delete_entry(dn)` (same ldap3 DELETE as Group 1 rollback)
- If `execution_result["okta_user_id"]` present: POST `/api/v1/users/{userId}/lifecycle/deactivate`
- Returns: `{rolled_back: True, account_removed: True/False, okta_deactivated: True/False}`

**New catalog file:** `backend/app/connectors/catalog/smtp.json`
```json
{
  "connector_type": "smtp",
  "display_name": "SMTP Email",
  "actions": [
    {
      "action_id": "send_welcome_email",
      "generic_action": "send_welcome_email",
      "action_type": "change",
      "execution_tier": 1,
      "applicable_asset_types": ["identity"]
    }
  ]
}
```

**ChangeType enum:** add `send_welcome_email`

---

### Group 5 — AWS: `preserve_cloudtrail_logs`

**New file:** `backend/app/connectors/executors/aws/preserve_cloudtrail_logs.py`

- Applies S3 Object Lock (Legal Hold ON) to all objects under `s3://{bucket}/{prefix}` for the account/region
- Parameters: `bucket`, `prefix` (default `""`), `account_id` (for audit), `region`
- Uses `boto3` S3 client: `put_object_legal_hold(Bucket=bucket, Key=key, LegalHold={"Status": "ON"})` for each object under prefix
- Returns: `{bucket, prefix, objects_locked: int, locked_at}`
- Rollback: same iteration with `LegalHold={"Status": "OFF"}`

**Catalog update:** `backend/app/connectors/catalog/aws.json`
- Add: `preserve_cloudtrail_logs -> preserve_cloudtrail_logs`

**ChangeType enum:** add `preserve_cloudtrail_logs`

---

### Group 6 — ServiceNow: `close_incident_ticket`

**No new executor needed** — `backend/app/connectors/executors/servicenow/close_incident.py` already exists.

**Only needed:**
- Add `close_incident_ticket` to ChangeType enum
- Add catalog entry to `backend/app/connectors/catalog/servicenow.json`: `close_incident -> close_incident_ticket` (new generic_action alias; `close_incident` action_id stays the same)
- Verify `close_incident.py` has a `rollback` function; if missing, add one that calls `update_incident` to reopen with `state=1`

---

### Group 7 — Agent Dispatch: `check_fleet_health`, `check_compliance`

**Pattern:** prefer live agent, fall back to inventory query. Both are read-only; `rollback` returns `NoRollbackNeeded`.

**New file:** `backend/app/connectors/executors/agent/check_fleet_health.py`

- Parameters: `asset_ids: list[str]`, `tags: dict` (alternative selector), `environment: str`
- Attempt: dispatch agent task to each online agent in asset set; collect results with 60s timeout
- Fallback: query inventory for asset health status fields if no agents respond
- Returns: `{healthy: int, degraded: int, unreachable: int, details: [...], checked_at}`

**New file:** `backend/app/connectors/executors/agent/check_compliance.py`

- Parameters: `asset_ids: list[str]`, `tags: dict`, `framework: str` (e.g. `"cis"`, `"soc2"`)
- Attempt: dispatch compliance check agent task; collect results with 60s timeout
- Fallback: query inventory for last compliance scan results
- Returns: `{compliant: int, non_compliant: int, unknown: int, details: [...], checked_at}`

**Catalog update:** `backend/app/connectors/catalog/` — these actions route through the agent connector. Check if `agent.json` exists; add entries there. If no agent catalog exists, create `backend/app/connectors/catalog/agent.json`.

**ChangeType enum:** add `check_fleet_health`, `check_compliance`

---

## Smoke Tests

### Level 1 — Connector Smoke Phases (7 phases)

All live in `backend/tests/smoke/test_runbook_connectors_live.py`.

Each phase follows the pattern: execute CR → verify side effect via direct API/SDK call → execute rollback CR → verify rollback.

| Phase | Change Type | Execute Verify | Rollback Verify |
|---|---|---|---|
| `AD_CREATE` | `create_ad_account` | LDAP search by sAMAccountName returns entry | LDAP search returns nothing |
| `OKTA_GROUPS` | `assign_okta_groups` | GET `/api/v1/groups/{id}/users` includes user | GET confirms user removed |
| `OKTA_PWRESET` | `force_password_reset` | GET `/api/v1/users/{id}` shows `PASSWORD_EXPIRED` status | N/A (NoRollbackNeeded) |
| `GITHUB_MEMBER` | `add_github_org_member` | GET `/orgs/{org}/memberships/{user}` returns `active` | GET returns 404 |
| `SMTP_EMAIL` | `send_welcome_email` | SMTP delivery confirmed (check inbox via IMAP or use Mailhog) | AD/Okta account deactivated confirmed |
| `CLOUDTRAIL` | `preserve_cloudtrail_logs` | `get_object_legal_hold` returns `ON` | `get_object_legal_hold` returns `OFF` |
| `SNOW_CLOSE` | `close_incident_ticket` | GET incident from ServiceNow, state=7 (closed) | GET incident state back to open |

For `SMTP_EMAIL`: use [Mailhog](https://github.com/mailhog/MailHog) as the SMTP target in CI (runs in Docker, exposes SMTP port + HTTP API for delivery verification). On EC2 smoke runner, start Mailhog container before the phase.

### Level 2 — Runbook End-to-End Smoke Phases (3 phases)

All live in `backend/tests/smoke/test_runbook_e2e_live.py`.

Each phase:
1. Creates a `RunbookExecution` by triggering the seeded template via `POST /api/runbooks/{id}/trigger`
2. Polls `GET /api/executions/{id}` until `status in (completed, failed)` or 5 min timeout
3. Asserts each step's CR executed successfully (no `failed` step statuses)
4. Calls `POST /api/executions/{id}/rollback` (or equivalent)
5. Verifies rollback side effects via direct connector/SDK calls

| Phase | Template | Steps Verified |
|---|---|---|
| `RB_ONBOARDING` | Engineer Onboarding | AD user created → Okta groups assigned → GitHub member added → email sent; rollback removes AD user, removes Okta groups, removes GitHub member |
| `RB_INCIDENT` | Incident Response: Account Compromise | CloudTrail locked → password reset → incident closed; rollback releases lock, reopens incident |
| `RB_PATCH` | Patch Campaign | Fleet health check returns result → compliance check returns result; read-only, no rollback needed |

**Rollback ordering** for `RB_ONBOARDING`: rollback must execute in reverse step order — email (account removal), GitHub, Okta groups, AD user. The runbook execution engine handles this via step ordering; the smoke test verifies the final state after rollback completes.

---

## File Map

**New files:**
- `backend/app/connectors/executors/active_directory/create_user.py`
- `backend/app/connectors/executors/okta/assign_groups.py`
- `backend/app/connectors/executors/okta/force_password_reset.py`
- `backend/app/connectors/executors/github/add_org_member.py`
- `backend/app/connectors/executors/smtp/__init__.py`
- `backend/app/connectors/executors/smtp/_client.py`
- `backend/app/connectors/executors/smtp/send_welcome_email.py`
- `backend/app/connectors/executors/aws/preserve_cloudtrail_logs.py`
- `backend/app/connectors/executors/agent/check_fleet_health.py`
- `backend/app/connectors/executors/agent/check_compliance.py`
- `backend/app/connectors/catalog/smtp.json`
- `backend/tests/smoke/test_runbook_connectors_live.py`
- `backend/tests/smoke/test_runbook_e2e_live.py`

**Modified files:**
- `backend/app/models/change_request.py` — add 9 ChangeType values
- `backend/app/connectors/catalog/active_directory.json` — add `create_user`
- `backend/app/connectors/catalog/okta.json` — add `assign_groups`, verify `force_password_reset`
- `backend/app/connectors/catalog/github.json` — add `add_org_member`
- `backend/app/connectors/catalog/aws.json` — add `preserve_cloudtrail_logs`
- `backend/app/connectors/catalog/servicenow.json` — add `close_incident_ticket` generic_action alias
- `backend/app/connectors/catalog/agent.json` (or create) — add `check_fleet_health`, `check_compliance`

---

## Credential-Gated Backlog

These alternatives are NOT in scope and go to backlog:
- SendGrid, AWS SES, Mailgun as email transport alternatives to SMTP
- These require API key credentials not in the platform today

---

## Out of Scope

- Vulnerability remediation workflows (separate future project)
- Any new runbook templates beyond fixing the existing 3
