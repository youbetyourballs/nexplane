# Azure AD + Defender Endpoint Smoke Coverage Design

> **Scope:** Group C of Tier 2 smoke backlog. Two new smoke phases: `azure_ad` (discover_users, get_group_membership, disable_user) and `defender_endpoint` (discover_machines, isolate_machine, unisolate_machine). Groups A (cloudformation, bicep, helm) and B (checkov, chef_inspec) are complete.

**Goal:** Add live smoke coverage for azure_ad and defender_endpoint connectors by running real executors against live Microsoft APIs through the Nexplane CR pipeline.

**Method:** CR pipeline throughout (dogfooding principle). Both connectors are SaaS — no EC2 instances needed. Credentials are fetched from the platform DB at runtime (existing connectors already registered with live creds). The backend container makes outbound HTTPS calls to Microsoft Graph API and Defender Security Center API directly.

---

## Phase AZURE_AD (`test_aws_live.py`)

**Infra:** None. Live Azure AD tenant via Microsoft Graph API (`graph.microsoft.com`). Credentials (tenant_id, client_id, client_secret) fetched from platform DB by querying existing `azure_ad` connector.

**New executors required:**
- `discover_users` — GET `/users?$top=999&$select=id,displayName,userPrincipalName,accountEnabled`
- `get_group_membership` — GET `/users/{user_id}/memberOf?$select=id,displayName`

Both added to `azure_ad.json` catalog with unique `generic_action` strings (`"discover_users"` and `"get_group_membership"`) to avoid routing collisions.

**Connector registration:**
- Fetch live creds by querying platform DB: `GET /connectors?connector_type=azure_ad`, use first result's credentials
- POST `/connectors` with `connector_type: "azure_ad"`, `name: "nexplane-smoke-azure-ad-<suffix>"`
- PUT `/connectors/{id}/credentials` with fetched creds
- Register one asset of type `identity`

**Smoke user UPN domain:**
Call Graph `/domains` endpoint, filter for domain ending in `.onmicrosoft.com` — every Azure AD tenant has exactly one. Use as UPN domain for smoke user: `nexplane-smoke-{suffix}@{onmicrosoft_domain}`.

**CR sequence (all with `_locked_connector_type: "azure_ad"`):**

1. `discover_users` — assert result has `users` list with at least one entry; grab `users[0]["id"]` as `target_user_id`
2. `get_group_membership` — `user_id=target_user_id` — assert result contains `groups` key (empty list is acceptable — proves API call succeeded)
3. `create_user` — UPN `nexplane-smoke-{suffix}@{onmicrosoft_domain}`, display_name `"Nexplane Smoke {suffix}"`, random initial password — assert result contains `id`
4. `disable_user` — `user_identifier=<smoke UPN>` — assert `accountEnabled: false` in result
5. Rollback `disable_user` CR — assert rolled back (user re-enabled)

**Cleanup:** `azure_ad_client.delete_user(smoke_upn)` in `finally` block — runs unconditionally regardless of assertion failures.

---

## Phase DEFENDER_ENDPOINT (`test_aws_live.py`)

**Infra:** None. Live Microsoft Defender for Endpoint via `api.securitycenter.microsoft.com`. Credentials fetched from platform DB by querying existing `defender_endpoint` connector.

All required executors already exist (`discover_machines`, `isolate_machine`, `unisolate_machine`).

**Connector registration:**
- Fetch live creds: `GET /connectors?connector_type=defender_endpoint`, use first result's credentials
- POST `/connectors` with `connector_type: "defender_endpoint"`, `name: "nexplane-smoke-defender-<suffix>"`
- PUT `/connectors/{id}/credentials` with fetched creds
- Register one asset of type `server`

**CR sequence (all with `_locked_connector_type: "defender_endpoint"`):**

1. `discover_machines` — assert result has `machines` list with at least one entry; grab `machines[0]["id"]` as `machine_id`
2. `isolate_machine` — `machine_id=<from step 1>`, `comment="Nexplane smoke test — unisolate follows immediately"` — assert `status` is `"Pending"` or `"Succeeded"` (Defender returns `Pending` for async machine actions)
3. `unisolate_machine` — `machine_id=<same>` — assert `status` is `"Pending"` or `"Succeeded"`

**Safety:** `unisolate_machine` CR runs in a `finally` block so isolation is always released even if assertions in step 2 fail. Machine is isolated for ~10–30s (duration of one CR round-trip).

**Cleanup:** Connector + asset deletion only (no EC2 to terminate).

---

## Files Modified

| Action | Path |
|--------|------|
| Modify | `backend/tests/smoke/test_aws_live.py` — add `run_phase_azure_ad()` and `run_phase_defender_endpoint()`, wire into `main()` |
| Create | `backend/app/connectors/executors/azure_ad/discover_users.py` |
| Create | `backend/app/connectors/executors/azure_ad/get_group_membership.py` |
| Modify | `backend/app/connectors/catalog/azure_ad.json` — add `discover_users` and `get_group_membership` actions |
| Modify | `backend/app/models/change_request.py` — add `discover_users`, `get_group_membership` ChangeType entries |
| Create | `backend/app/connectors/change_type_definitions/discover_users.json` |
| Create | `backend/app/connectors/change_type_definitions/get_group_membership.json` |

No migrations. No new connector types. No EC2.

---

## Non-Goals

- Group A (cloudformation, bicep, helm) — complete
- Group B (checkov, chef_inspec) — complete
- `create_user` executor smoke in isolation — covered as part of the disable_user lifecycle above
- Defender alerts/vulnerabilities/software discovery smoke — deferred (read-only, lower risk priority)
- `restrict_app_execution`, `run_antivirus_scan`, `initiate_investigation` smoke — deferred (potentially disruptive on real machines)
- GCP/Azure/LDAP revocation live smoke — blocked pending connector instances
