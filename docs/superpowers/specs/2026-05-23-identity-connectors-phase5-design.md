# Identity Connectors Phase 5 — Implementation Design

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task.

**Date:** 2026-05-23
**Status:** Approved for implementation

---

## Strategic Context

Phase 5 closes the identity gap in the platform's incident response story. Today, locking out a user requires separate CRs per system — an operator under pressure during an active incident has to manually identify and action every identity system the user touches. Phase 5 makes that a single CR that fans out across all connected systems simultaneously, with per-system rollback guaranteed by pre-state snapshots.

The `identity_reconstitute` capability is genuinely novel: no current product (Quest Recovery Manager, Semperis ADFR, or any SOAR) performs cross-system identity reconstitution from a baseline snapshot with rollback guarantees. This addresses the post-ransomware scenario where the management infrastructure itself is compromised — accounts may be corrupt or desynchronized across AD, Okta, GitHub, and Entra ID simultaneously. Rebuilding from a known-good snapshot across all systems in one coordinated CR with dry-run preview and rollback is a defensible product position.

---

## Section 1: Identity Graph Data Model

### New Tables

**`identity_profiles`** — one row per person, cross-system:

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `display_name` | str | |
| `primary_email` | str | indexed, used for email-based correlation |
| `source_idp_connector_id` | UUID FK nullable | Okta or Entra ID connector that is authoritative |
| `correlation_method` | str | `"idp"` or `"email"` |
| `last_synced_at` | datetime | |
| `created_at` | datetime | |

**`identity_accounts`** — one row per account per system:

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `identity_profile_id` | UUID FK | → identity_profiles |
| `connector_id` | UUID FK | → connectors |
| `connector_type` | str | `"active_directory"`, `"okta"`, `"github"`, etc. |
| `external_id` | str | system-native user ID (immutable, used for API calls) |
| `username` | str | |
| `email` | str nullable | |
| `raw_attributes` | JSONB | full profile snapshot from last sync |
| `last_synced_at` | datetime | |
| `is_stale` | bool | true if not synced in >24h |

### Correlation Logic

Run during connector sync in priority order:

1. **IdP-primary:** If an Okta or Entra ID connector exists, it is the authoritative source. These IdPs store cross-system account references in user profile attributes (AD UPN, GitHub login, etc.). Extract these to build the graph directly — no platform-side matching needed.

2. **Email fallback:** For connectors not covered by the IdP (FreeIPA, LDAP, Gitea, Teleport), match returned accounts to existing `identity_profiles` by `primary_email`. Create new profiles for unmatched accounts, marked `correlation_method: "email"`.

3. **Uncorrelated accounts:** Accounts that cannot be matched appear in the UI as uncorrelated — visible to operators, excluded from fan-out until resolved.

---

## Section 2: Sync Engine

### Triggers

**Scheduled sync** — APScheduler job, configurable per org, default every 4 hours. For each identity connector:
1. Calls the connector's existing `discover_users` / `discover_identities` action
2. For IdP connectors: extracts cross-system references from profile attributes, upserts `identity_accounts`
3. For non-IdP connectors: email-matches to existing profiles; creates new profiles for unmatched accounts
4. Updates `raw_attributes` on each account (used by snapshots and reconstitution)
5. Sets `is_stale = false`, updates `last_synced_at`

**Event-triggered sync** — after any fan-out CR completes, immediately re-syncs affected accounts rather than waiting for the scheduled run. Ensures the graph reflects post-CR state before propagation windows close.

**Staleness handling:**
- Accounts not synced in >24h: `is_stale = true`, surfaced in UI
- Stale accounts excluded from fan-out execution by default (configurable override per org)
- Stale accounts still appear in the identity graph — operators can see the last known state

---

## Section 3: Fan-out Execution

### CR Model

Fan-out follows the existing project/campaign model — one parent CR spawns child CRs, one per identity connector. This preserves the existing CR model unchanged and gives each system its own audit trail and independent rollback.

**Parent CR:** `emergency_user_lockout`, `user_suspension`, `user_scope_reduction`, `enforce_mfa`
- Goes through normal approval gate
- On approval: resolves the target user's `IdentityProfile`, spawns one child CR per `IdentityAccount`
- Child CRs are **auto-approved** — they inherit the parent's approval; no separate operator sign-off per system

**Child CRs:** one per connector, standard single-connector CRs using existing executor actions

**Parent status rollup:**
- `completed` if ≥1 child succeeded
- `failed` if all children failed
- `partial` if some succeeded, some failed — failed systems listed in `warnings`

### Fan-out Registry

`backend/app/connectors/executors/identity/fan_out_registry.py` — single source of truth mapping ChangeType × connector_type → action:

```python
FAN_OUT_ACTIONS = {
    "emergency_user_lockout": {
        "active_directory": "disable_account",
        "okta":             "suspend_user",
        "entra_id":         "disable_user",
        "github":           "suspend_org_member",
        "gitlab":           "gitlab_suspend_user",
        "ldap":             "ldap_disable_user",
        "freeipa":          "freeipa_disable_user",
        "keycloak":         "keycloak_disable_user",
        "gitea":            "gitea_suspend_user",
        "teleport":         "teleport_lock_user",
        "kubernetes":       "k8s_revoke_rolebinding",
    },
    "user_suspension": {
        "active_directory": "disable_account",
        "okta":             "suspend_user",
        "entra_id":         "disable_user",
        "github":           "suspend_org_member",
        "gitlab":           "gitlab_suspend_user",
        "ldap":             "ldap_disable_user",
        "freeipa":          "freeipa_disable_user",
        "keycloak":         "keycloak_disable_user",
        "gitea":            "gitea_suspend_user",
    },
    "enforce_mfa": {
        "active_directory": "enforce_mfa",
        "okta":             "enforce_mfa",
        "entra_id":         "reset_mfa",
    },
    "user_scope_reduction": {
        "active_directory": "remove_from_group",
        "okta":             "deprovision_from_app",
        "entra_id":         "remove_from_role",
        "github":           "remove_org_member",
        "kubernetes":       "k8s_revoke_rolebinding",
    },
}
```

Adding a new identity connector requires only a new row in this dict — no other changes.

### Pre-State Snapshot (per child CR)

Before executing its action, each child CR calls a connector-specific `get_account_state()` read and stores the result in `execution_result.pre_state`. What is captured per connector type:

| Connector | Pre-state fields |
|-----------|-----------------|
| Active Directory | `enabled`, `locked`, `group_memberships`, `mfa_enforced` |
| Okta | `status` (ACTIVE/SUSPENDED/DEPROVISIONED), `mfa_enrolled_factors`, `app_assignments` |
| Entra ID | `accountEnabled`, `signInSessionsValidFromDateTime`, `assigned_roles` |
| GitHub | `org_membership_state`, `role`, `team_memberships` |
| GitLab | `state` (active/blocked), `group_memberships` |
| LDAP/FreeIPA | `enabled`, `locked`, connector-specific attribute map |
| Keycloak | `enabled`, `required_actions` |
| Kubernetes | `rolebindings[]` — full list at time of CR |
| Teleport | `locked`, `lock_expires` |

Child CRs that never executed (connector unreachable before snapshot) have no pre-state — their rollback is a no-op.

---

## Section 4: Rollback

Each child CR rolls back independently. Parent rollback triggers all child rollbacks concurrently.

**Per-child rollback:**
1. Read `execution_result.pre_state`
2. Call connector-specific `restore_account_state(pre_state, connector)` — the typed inverse of what the action did
3. If connector unreachable: mark child as `rollback_failed`, continue remaining children
4. If child never executed: no-op, mark `rollback_skipped`

**Propagation correctness:** If Okta had already suspended the account before the CR ran, the snapshot captures `status: SUSPENDED`. Rollback restores `SUSPENDED` — not `ACTIVE`. The CR never re-enables what it did not disable.

**Parent rollback status:**
- `completed` — all attempted children rolled back
- `partial` — some children failed rollback (listed in `rollback_warnings`)
- `failed` — all children failed rollback

---

## Section 5: Identity Snapshot and Reconstitution

### `identity_snapshot` CR

Captures full directory state across all connected identity systems to S3. Intended to be run on a schedule (daily or before major changes) to maintain a known-good baseline.

**What is captured per system:**
- All user accounts: full `raw_attributes` + membership/role/app assignments
- Group definitions and memberships
- Role assignments
- App assignments (Okta, Entra ID)
- MFA enrollment state

**Output:** S3 artifact at `{prefix}/identity-snapshot-{timestamp}/`:
```
manifest.json          — format version, timestamp, systems covered, artifact list
{connector_id}/
  users.json           — all user objects with full attributes
  groups.json          — all groups and memberships
  roles.json           — role assignments
  app_assignments.json — app-level access (Okta/Entra ID)
```

**Manifest format:**
```json
{
  "format": "identity_snapshot_v1",
  "snapshot_id": "identity-snapshot-20260523T120000Z",
  "snapshot_timestamp": "20260523T120000Z",
  "systems": ["active_directory", "okta", "freeipa"],
  "connector_ids": ["uuid1", "uuid2", "uuid3"],
  "artifact_counts": {"users": 1240, "groups": 87, "roles": 34}
}
```

### `identity_reconstitute` CR

Given a snapshot S3 prefix, compares current state against the snapshot and restores diverged objects. Designed for post-incident recovery where normal rollback is insufficient — objects may be corrupt, missing, or desynchronized across systems.

**Execution sequence:**

1. **Download and validate manifest** — confirm `format: "identity_snapshot_v1"`, fail with clear error if not
2. **Dry-run analysis (always runs first)** — for each account in snapshot vs current state:
   - `missing` — account exists in snapshot, not in current system → will create
   - `corrupted` — attributes diverged beyond threshold → will delete + recreate
   - `orphaned` — exists in current system, not in snapshot → flag for operator review, do NOT auto-delete
   - `clean` — matches snapshot within threshold → skip
3. **Operator review gate** — dry-run results returned as CR step result. CR pauses. Operator must explicitly approve the reconstitution plan before destructive actions proceed.
4. **Reconstitute** — for each `missing` or `corrupted` account:
   - Delete current object if present (corrupted case)
   - Recreate from snapshot attributes
   - Restore group/role/app memberships
5. **Verify** — re-read each reconstituted account, confirm attributes match snapshot within threshold
6. **Report** — full per-account result: reconstituted, skipped (clean), flagged (orphaned), failed

**Rollback of reconstitution:** Pre-reconstitution snapshot is taken before any writes (same mechanism as fan-out pre-state). Rollback deletes created accounts and restores deleted ones.

**Divergence threshold:** Configurable per org. Default: any difference in `enabled`, `locked`, `group_memberships`, `role_assignments`, or `mfa_state` triggers `corrupted` classification. Cosmetic fields (display name, phone number) use fuzzy matching — operators can configure which fields are structural vs cosmetic.

**Safety constraints:**
- Orphaned accounts (exist now, not in snapshot) are never auto-deleted — always flagged for operator review
- Accounts that are service accounts (identified by naming convention or flag) require explicit override to reconstitute
- Maximum reconstitution batch size configurable (default 100 accounts) — larger batches require explicit override

---

## Section 6: New ChangeType Values

Add to `ChangeType` enum in `backend/app/models/change_request.py`:

```python
identity_snapshot         = "identity_snapshot"
identity_reconstitute     = "identity_reconstitute"
```

Fan-out types already exist in the enum (`emergency_user_lockout`, `user_suspension`, `user_scope_reduction`, `enforce_mfa`).

---

## Section 7: Smoke Tests

Three phases added to `backend/tests/smoke/test_aws_live.py`:

### `IDENTITY_FANOUT`

**Setup:** AD DC AMI (cached, `dc-smoke`) + FreeIPA AMI (cached, `freeipa`). Both already used by existing phases.

**Sequence:**
1. Launch AD DC and FreeIPA from cached AMIs
2. Register AD connector + FreeIPA connector
3. Create test user in both systems with matching email (`fanout-smoke@smoke.nexplane.local`)
4. Trigger identity graph sync — verify both accounts appear under one `IdentityProfile`
5. Run `emergency_user_lockout` fan-out CR targeting the identity profile
6. Verify: parent CR `completed`, two child CRs created and executed
7. Verify: AD account disabled, FreeIPA account locked
8. Run rollback on parent CR
9. Verify: both accounts restored to pre-CR state
10. Cleanup connector, asset, test user, terminate instances

**Estimated time:** ~15 min from cached AMIs
**Cost:** ~$0.08/run

### `IDENTITY_SNAPSHOT`

**Setup:** Same AD DC + FreeIPA pair.

**Sequence:**
1. Launch both instances
2. Run `identity_snapshot` CR — verify S3 manifest at `{prefix}/manifest.json` with `format: identity_snapshot_v1`
3. Directly modify a user attribute via WinRM/LDAP (simulate post-incident corruption)
4. Run `identity_reconstitute` in dry-run mode — verify the corrupted account is detected and listed
5. Run `identity_reconstitute` for real — verify attribute restored to snapshot value
6. Run rollback — verify reconstituted changes are reversed
7. Cleanup S3 artifacts, instances

**Estimated time:** ~18 min
**Cost:** ~$0.09/run

### `IDENTITY_SYNC`

**Setup:** FreeIPA AMI only (faster).

**Sequence:**
1. Launch FreeIPA from cached AMI
2. Register connector, confirm initial sync populates identity graph
3. Create a new user directly in FreeIPA via LDAP
4. Trigger manual sync (API call to sync endpoint)
5. Verify new account appears in identity graph under correct profile
6. Verify `last_synced_at` updated, `is_stale: false`
7. Cleanup

**Estimated time:** ~8 min
**Cost:** ~$0.03/run

---

## Section 8: Files Touched

| File | Action |
|------|--------|
| `backend/app/models/identity_profile.py` | New — IdentityProfile + IdentityAccount SQLAlchemy models |
| `backend/alembic/versions/XXXX_identity_graph.py` | New — migration adding identity_profiles + identity_accounts tables |
| `backend/app/services/identity_sync_service.py` | New — sync engine: scheduled + event-triggered sync, correlation logic |
| `backend/app/connectors/executors/identity/fan_out_registry.py` | New — ChangeType × connector_type → action mapping |
| `backend/app/connectors/executors/identity/fan_out_executor.py` | New — spawns child CRs, handles auto-approval inheritance, rollup |
| `backend/app/connectors/executors/identity/get_account_state.py` | New — per-connector pre-state snapshot reads |
| `backend/app/connectors/executors/identity/restore_account_state.py` | New — per-connector pre-state restore |
| `backend/app/connectors/executors/identity/identity_snapshot.py` | New — captures full directory state to S3 |
| `backend/app/connectors/executors/identity/identity_reconstitute.py` | New — dry-run + reconstitute from snapshot |
| `backend/app/connectors/change_type_definitions/identity_snapshot.json` | New — CTD |
| `backend/app/connectors/change_type_definitions/identity_reconstitute.json` | New — CTD |
| `backend/app/models/change_request.py` | Add `identity_snapshot`, `identity_reconstitute` to ChangeType enum |
| `backend/app/routers/identity.py` | New — REST endpoints: GET /identity/profiles, GET /identity/profiles/{id}, POST /identity/sync |
| `backend/app/services/change_request_service.py` | Modify — detect fan-out ChangeTypes, spawn child CRs with auto-approval inheritance |
| `backend/app/services/scheduler_service.py` | Add scheduled identity sync job (every 4h) |
| `backend/tests/test_identity_graph.py` | New — unit tests: correlation logic, fan-out registry, pre-state snapshot/restore |
| `backend/tests/smoke/test_aws_live.py` | Add IDENTITY_FANOUT, IDENTITY_SNAPSHOT, IDENTITY_SYNC phases |
