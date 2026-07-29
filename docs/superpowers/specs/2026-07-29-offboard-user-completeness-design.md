# Offboard User — Discovery + Verification Design

## Goal

Extend the existing `offboard_user` CR type with pre-execution account discovery and post-execution verification. Discovery gates plan generation so the approval view shows only systems where the user actually exists. Verification confirms every disable took effect before the report runs, with exponential backoff retry to tolerate propagation delays. Hard fail if any system still shows the account active after the retry window.

## Scope

Existing connector set only: Active Directory, Okta, Entra ID, Google Workspace, GitHub, Slack, CrowdStrike. Wiring in additional connector types (AWS IAM, GitLab, Teleport, Zscaler, etc.) is deferred.

## Architecture

```
Discovery → Phase 1 (session revoke) → Phase 2 (account disable)
         → Phase 3 (workspace removal) → Phase 4 (endpoint isolation)
         → Phase 5 (verification) → Phase 6 (report)
```

### Discovery (pre-plan)

Runs before steps are generated. Queries every connected system for the target email. Each connector returns a discovery result:

```python
{
    "connector_id": str,
    "connector_type": str,
    "found": bool,
    "account_identifier": str | None,   # UPN, username, user ID — whatever the system uses
    "details": dict,                     # display name, status, last login, etc.
}
```

The plan builder only emits action steps for connectors where `found=True`. If no connectors find the user, the CR fails at planning with a clear error: `"No accounts found for {email} across {N} connected systems."` The discovery manifest is stored in the CR's execution state and surfaced in the approval view.

### Phases 1–4 (unchanged)

Steps are generated exactly as today, but only for connectors present in the discovery manifest with `found=True`.

- **Phase 1** — Session revocation: Okta, Entra ID, Google Workspace (parallel)
- **Phase 2** — Account disable: AD, Okta, Entra ID, Google Workspace (parallel)
- **Phase 3** — Workspace/org removal: GitHub, Slack (parallel)
- **Phase 4** — CrowdStrike endpoint isolation (opt-in, sequential)

### Verification (Phase 5)

One verification step per connector that ran in phases 1–4. Each step:

1. Re-queries the system to check account state.
2. If the account is not yet disabled, retries with exponential backoff: 2s → 4s → 8s → 16s → 30s (≈60s total max per connector).
3. If the account is still active after all retries, the step fails and the CR hard-fails. No further steps execute. The report is not generated.
4. If the account is confirmed disabled, the step passes.

Verification steps run in parallel across connectors (same as their corresponding disable steps).

### Report (Phase 6)

Only executes if all Phase 5 verifications pass. The report now includes:

- **Discovery manifest** — which systems were queried, which had accounts, account identifiers found
- **Verification results** — per-connector confirmation timestamps and final account state
- **Existing fields** — reason, manager notification, completed_at

### Rollback

Rollback is FILO (phases 4 → 3 → 2 → 1). Verification and report phases have no rollback action (reports are not reversible; verification is read-only). Discovery has no rollback action.

If rollback is triggered after partial execution, only steps that completed (status=`completed`) are rolled back. Each connector's rollback re-enables/re-instates the account.

## Files

| File | Change |
|------|--------|
| `backend/app/connectors/executors/offboard_user/__init__.py` | Add discovery pass before plan build; add Phase 5 verification steps; renumber report to Phase 6 |
| `backend/app/connectors/executors/offboard_user/steps/discover_accounts.py` | New — per-connector discovery; routes to connector-specific query logic |
| `backend/app/connectors/executors/offboard_user/steps/verify_disabled.py` | New — per-connector verification with exponential backoff retry |
| `backend/app/connectors/executors/offboard_user/steps/offboarding_report.py` | Add discovery manifest and verification results to report output |
| `backend/tests/smoke/test_offboard_user_smoke.py` | New — full CR lifecycle smoke against live connectors |

## Discovery implementation

`discover_accounts.py` routes by connector type to a per-connector query:

| Connector | Query method | Account identifier |
|-----------|-------------|-------------------|
| `active_directory` | LDAP search by `mail` or `userPrincipalName` | sAMAccountName |
| `okta` | `GET /api/v1/users?q={email}` | Okta user ID |
| `entra_id` | MS Graph `GET /users/{email}` | Object ID |
| `google_workspace` | Admin SDK `users.get(userKey=email)` | primaryEmail |
| `github` | Search org members by email (requires org:read scope) | GitHub login |
| `slack` | `users.lookupByEmail` | Slack member ID |
| `crowdstrike` | `GET /devices/queries/devices/v1` filtered by assigned user email | Device IDs (list) |

CrowdStrike discovery returns a list of device IDs assigned to the user, not a single account. `found=True` if at least one device is found.

## Verification implementation

`verify_disabled.py` routes by connector type to a per-connector state check:

| Connector | Check method | Expected state |
|-----------|-------------|----------------|
| `active_directory` | LDAP `userAccountControl` bit 2 (ACCOUNTDISABLE) | bit set |
| `okta` | `GET /api/v1/users/{id}` → `status` | `DEPROVISIONED` or `SUSPENDED` |
| `entra_id` | MS Graph `GET /users/{id}` → `accountEnabled` | `false` |
| `google_workspace` | Admin SDK `users.get` → `suspended` | `true` |
| `github` | Org membership state | `pending` removal or not found |
| `slack` | `users.info` → `deleted` | `true` |
| `crowdstrike` | Device isolation status per device ID | all isolated |

Retry loop:

```python
delays = [2, 4, 8, 16, 30]
for delay in delays:
    state = await _query_state(connector, account_identifier)
    if _is_disabled(connector_type, state):
        return {"verified": True, "attempts": attempt, "final_state": state}
    await asyncio.sleep(delay)
# All retries exhausted
return {"verified": False, "final_state": state, "error": "Account still active after 60s retry window"}
```

## Smoke test

`test_offboard_user_smoke.py` — two phases:

**Phase 1 — Discovery accuracy:** Create a test AD user (via LDAP or existing `create_ad_account` CR). Run `offboard_user` CR through plan only (do not execute). Assert the discovery manifest contains the AD connector with `found=True` and the correct `account_identifier`. Assert connectors with no account (Okta, GitHub, etc. if not configured) are absent from the plan.

**Phase 2 — Full lifecycle with verification:** Execute the CR to completion. Assert Phase 5 verification steps all pass. Assert the report includes `discovery_manifest` and `verification_results`. Assert the AD account is actually disabled via direct LDAP query. Roll back the CR and assert the AD account is re-enabled.

Live connectors used: Active Directory (DC AMI — `ami-058deb2fa3a1acc14`). Okta, GitHub, etc. wired in only if credentials are present in the platform; otherwise those connectors are absent from discovery and the test verifies the plan contains only the AD step.

## Global Constraints

- Existing phases 1–4 step executor files (`disable_ad.py`, `revoke_okta.py`, `suspend_entra.py`, `suspend_google.py`, `remove_github.py`, `deactivate_slack.py`, `isolate_crowdstrike.py`) are not modified.
- Verification retry total wall-clock per connector must not exceed 60 seconds.
- Hard fail on verification failure — the report phase must not execute if any verification step fails.
- Plan builder must return an error (not an empty plan) if discovery finds zero accounts across all connected systems.
- FILO rollback order: Phase 4 → 3 → 2 → 1. Phases 5 and 6 have no rollback action.
- Smoke test must run against live AD (DC AMI); other connectors are optional based on available credentials.
