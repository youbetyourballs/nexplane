# Drift Detection + Organizational Memory Reconciliation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect when infrastructure resources diverge from the state Nexplane last established, surface that drift to operators with a structured response menu, and advance the organizational memory anchor when out-of-band changes are intentionally accepted.

**Architecture:** Change-execution engine model (not desired-state). The platform tracks what it changed, observes whether it stayed that way, and treats deviations as drift requiring operator action. A `resource_states` table stores the canonical last-known-good anchor per `(org, asset, surface_type)`. CR execution advances the anchor. A hybrid detection engine (agent tripwires for host surfaces, connector polling for cloud surfaces) re-observes on cadence and diffs against the anchor. Detected drift surfaces as a `DriftEvent` with four operator actions: approve a generated shadow CR (restore), accept new state (advance anchor), attest (audit record, suppress), or dismiss.

**Tech Stack:** Python/FastAPI backend, SQLAlchemy async, Temporal workflows, existing `dispatch_agent_job()` for host observation, existing connector clients for cloud observation, existing CR lifecycle and notification pipelines.

---

## Global Constraints

- All new CR types must have `smoke_verified: false` in catalog JSON on commit; set `true` only after live smoke passes
- Every executor must implement `ROLLBACK_CAPABILITY` per platform contract
- `restore_resource_state` shadow CRs must go through the full approval gate — never auto-executed without operator approval
- Agent callbacks authenticated via existing HMAC agent secret (`org_settings.agent_secret`)
- `ResourceState` anchor must never be silently lost — if post-CR observation fails, preserve the prior anchor and log a warning; do not blank the anchor
- Diff format is structured JSON (`added`/`removed`/`changed`) on all surfaces — no raw text diffs
- Auto-created `DriftPolicy` records are non-deletable while the source CR is within the org's `pre_state_retention_days` rollback window
- All new tables follow existing SQLAlchemy async pattern with UUID PKs and `organization_id` FK for org-scoping
- Smoke test must use the full CR lifecycle (create → plan → approve → execute) for both the anchor-setting CR and the shadow CR remediation phase

---

## Data Model

### `resource_states` table

One row per `(organization_id, asset_id, surface_type)` — upserted on every observation. The canonical anchor.

```python
class ResourceState(Base):
    __tablename__ = "resource_states"

    id: UUID (PK)
    organization_id: UUID (FK → organizations)
    asset_id: UUID (FK → assets)
    surface_type: str  # see Surface Types section
    state: JSONB       # full captured state, surface-type specific
    captured_at: datetime
    source: str        # "cr_execution" | "initial_observation" | "accepted"
    source_cr_id: UUID | None  (FK → change_requests)
    accepted_by: UUID | None   (FK → users)
    accepted_at: datetime | None
    acceptance_note: str | None

    __table_args__ = (
        UniqueConstraint("organization_id", "asset_id", "surface_type"),
    )
```

### `drift_policies` table

Configuration for what to watch and how often. Auto-created by CR execution; also created explicitly by operators for surfaces Nexplane hasn't touched.

```python
class DriftPolicy(Base):
    __tablename__ = "drift_policies"

    id: UUID (PK)
    organization_id: UUID (FK → organizations)
    name: str
    scope_type: str    # "asset" | "tag"
    scope_value: str   # asset UUID or tag name
    surface_types: JSONB  # list[str] of surface type names
    poll_interval_seconds: int  # default 3600
    auto_created: bool  # True = spawned by CR execution
    enabled: bool       # default True
    source_cr_id: UUID | None  (FK → change_requests)  # CR that auto-created this
    created_by: UUID | None  (FK → users)  # None for auto-created
    created_at: datetime
```

### `drift_events` table

One row per detected drift instance, with full lifecycle tracking.

```python
class DriftEvent(Base):
    __tablename__ = "drift_events"

    id: UUID (PK)
    organization_id: UUID (FK → organizations)
    asset_id: UUID (FK → assets)
    surface_type: str
    drift_policy_id: UUID (FK → drift_policies)
    baseline_state: JSONB   # snapshot of ResourceState.state at detection time
    observed_state: JSONB   # what was actually observed
    diff: JSONB             # {added: {}, removed: {}, changed: {key: {from, to}}}
    severity: str           # "high" | "medium" | "low" (derived from surface_type)
    detected_at: datetime
    status: str             # "open" | "accepted" | "attested" | "dismissed"
    shadow_cr_id: UUID | None  (FK → change_requests)
    resolved_by: UUID | None   (FK → users)
    resolved_at: datetime | None
    resolution_note: str | None
    attested_suppress_until: datetime | None  # set on attest action
```

---

## Surface Types

### Host surfaces (agent-based)

| Surface type | Observation method | Tripwire? | Severity |
|---|---|---|---|
| `ssh_config` | agent: read `/etc/ssh/sshd_config` | yes | medium |
| `sudoers` | agent: read `/etc/sudoers` + `/etc/sudoers.d/*` | yes | high |
| `cron_jobs` | agent: list system cron + user crontabs | yes | medium |
| `listening_ports` | agent: `ss -tlnp` | no | low |
| `running_services` | agent: `systemctl list-units --state=active` | no | low |
| `users_groups` | agent: read `/etc/passwd`, `/etc/group` | yes | high |
| `firewall_rules` | agent: `iptables-save` / `nft list ruleset` / `ufw status` | no | high |

### Cloud surfaces (connector polling)

| Surface type | Observation method | Severity |
|---|---|---|
| `aws_security_group` | `ec2.describe_security_groups(GroupIds=[...])` | high |
| `aws_iam_policy` | `iam.get_policy_version(PolicyArn, VersionId)` | high |
| `aws_s3_bucket_policy` | `s3.get_bucket_policy(Bucket)` | high |
| `gcp_firewall_rule` | `compute.firewalls().get(project, firewall)` | high |
| `gcp_iam_binding` | `resourcemanager.getIamPolicy(resource)` | high |
| `oci_security_list` | `virtual_network.get_security_list(security_list_id)` | high |

---

## Catalog Changes

Each CR type in the connector catalogs gets an optional `drift_surfaces` field listing which surface types it modifies. CR types without this field do not trigger drift monitoring.

```json
{
  "action_id": "ssh_hardening",
  "drift_surfaces": ["ssh_config", "firewall_rules"]
}
{
  "action_id": "aws_security_group_update",
  "drift_surfaces": ["aws_security_group"]
}
```

The post-CR hook reads this field to know which surfaces to observe after completion.

---

## CR Lifecycle Integration

### Post-completion hook — `drift_service.on_cr_completed(cr_id, db)`

Called from `execute_change_workflow.py` after a CR transitions to `completed`. Runs asynchronously — CR completion is not blocked by drift observation.

1. Load CR and its catalog entry; read `drift_surfaces`
2. If `drift_surfaces` is empty or absent: return (nothing to monitor)
3. For each target asset in `cr.target_asset_ids`:
   - For each surface_type in `drift_surfaces`:
     - Dispatch observation job (agent job or connector call per surface type map)
     - On success: upsert `ResourceState` with `source="cr_execution"`, `source_cr_id=cr_id`, `captured_at=now()`
     - On failure: log warning, preserve existing `ResourceState` anchor — do not blank it
     - If no `DriftPolicy` exists for `(org, asset, surface_type)`: create one with `auto_created=True`, `poll_interval_seconds=3600`, `source_cr_id=cr_id`
     - If agent is present and surface has tripwire support: dispatch `start_drift_watch` agent job for this surface
4. Close any open `DriftEvent` for `(org, asset, surface_type)` with `status="dismissed"` and `resolution_note="resolved by CR {cr_id}"`

### Initial observation for explicit policies

When an operator creates a `DriftPolicy` manually via `POST /drift/policies`, immediately run the same observation flow to establish the first anchor (`source="initial_observation"`). No `source_cr_id`.

---

## Detection Engine

### Drift worker — `app/workers/drift_check_worker.py`

The existing stub gets implemented. Runs on an APScheduler tick (default every 5 minutes; each policy controls its own effective cadence via `poll_interval_seconds`).

```
for each enabled DriftPolicy:
    if now() < policy.last_checked_at + poll_interval_seconds: continue
    resolve scope → list of asset_ids (by asset_id or tag)
    for each (asset_id, surface_type):
        observed = observe_surface(asset_id, surface_type)
        baseline = load ResourceState for (org, asset_id, surface_type)
        if baseline is None: write observed as initial_observation anchor; continue
        diff = compute_diff(baseline.state, observed)
        if diff is empty: continue
        if open DriftEvent exists for (org, asset_id, surface_type): continue  # deduplicate
        if DriftEvent exists with status=attested and suppress_until > now(): continue
        create DriftEvent(baseline_state=baseline.state, observed_state=observed, diff=diff)
        generate shadow CR
        fire notification
    update policy.last_checked_at
```

### Agent tripwires — fast path

New agent command `start_drift_watch`:
- Parameters: `{"surface_types": ["ssh_config", "sudoers", ...], "callback_url": "/drift/agent-event"}`
- Agent installs OS-native watchers on the paths for each requested surface type
- On path change: `POST /drift/agent-event` with `{asset_id, surface_type, changed_paths}`
- Platform side: receive callback → immediately dispatch full observation for that surface → run diff → create `DriftEvent` if drift found
- Authenticated via HMAC using `org_settings.agent_secret` (same as existing agent job auth)
- Re-dispatched by `on_cr_completed()` hook if not already active (agent tracks active watches in memory; re-dispatch is idempotent)

### Diff algorithm — `drift_service.compute_diff(baseline, observed)`

All state dicts are normalized before diffing (keys sorted, arrays sorted by deterministic key where applicable — e.g., firewall rules sorted by rule ID, `passwd` entries sorted by username).

```python
def compute_diff(baseline: dict, observed: dict) -> dict:
    added = {k: v for k, v in observed.items() if k not in baseline}
    removed = {k: v for k, v in baseline.items() if k not in observed}
    changed = {
        k: {"from": baseline[k], "to": observed[k]}
        for k in baseline
        if k in observed and baseline[k] != observed[k]
    }
    return {"added": added, "removed": removed, "changed": changed}
```

Returns `{}` (empty dict) if no drift — workers check `bool(diff)` to decide whether to create an event.

---

## Shadow CR Generation

New CR type `restore_resource_state` added to `nexplane_agent.json` catalog:

```json
{
  "action_id": "restore_resource_state",
  "display_name": "Restore Resource State",
  "description": "Restore a monitored resource surface to its last anchored state following detected drift.",
  "parameters": [
    {"name": "resource_state_id", "type": "string", "required": true},
    {"name": "drift_event_id", "type": "string", "required": true}
  ],
  "executor": "nexplane_agent.restore_resource_state",
  "rollback_action": null,
  "rollback_capability": "none",
  "blast_radius_hint": "host_config_change",
  "drift_surfaces": [],
  "smoke_verified": false
}
```

`ROLLBACK_CAPABILITY = "none"` — restoring to the anchor IS the rollback; a rollback of a restore would bring back the drift, which is not meaningful. The CR is created as DRAFT and follows the normal plan → approve → execute lifecycle.

**Executor logic (`app/connectors/executors/nexplane_agent/restore_resource_state.py`):**

1. Load `ResourceState` by `resource_state_id`; verify `organization_id` matches
2. Dispatch surface-type-specific restoration agent job or connector call:
   - Host surfaces: agent job writes files back, reloads affected service
   - Cloud surfaces: connector API call restores resource to snapshotted config
3. After restoration: call `drift_service.on_cr_completed()` to re-observe and advance anchor
4. `DriftEvent` is closed by the post-CR hook (step 4 of on_cr_completed)

**Shadow CR is created automatically** when a `DriftEvent` is written. `DriftEvent.shadow_cr_id` is set to the new CR ID. Shadow CR title: `"Restore {surface_type} on {asset_name} (drift detected {detected_at})"`.

---

## Response Flow

### Status transitions

```
open ──► (approve shadow CR) ──► shadow CR executes ──► dismissed
     ──► (accept new state)  ──► accepted   (anchor advanced)
     ──► (attest)            ──► attested   (anchor unchanged, suppressed N days)
     ──► (dismiss)           ──► dismissed  (no action, no suppression)
```

### Accept new state — `POST /drift/events/{id}/accept`

Body: `{"note": str}`

1. Upsert `ResourceState` with `state=drift_event.observed_state`, `source="accepted"`, `accepted_by=current_user.id`, `accepted_at=now()`
2. Cancel the shadow CR (set CR status to `cancelled`)
3. Set `DriftEvent.status="accepted"`, `resolved_by`, `resolved_at`, `resolution_note=note`
4. Dispatch `start_drift_watch` re-registration if agent is present (new anchor = new watch baseline)

### Attest — `POST /drift/events/{id}/attest`

Body: `{"note": str, "snooze_days": int}` (default `snooze_days=7`)

1. Set `DriftEvent.status="attested"`, `resolved_by`, `resolved_at`, `resolution_note=note`
2. Set `DriftEvent.attested_suppress_until = now() + snooze_days`
3. `ResourceState` anchor **unchanged** — drift still exists relative to last known good
4. Shadow CR remains as DRAFT (operator may still want to use it after the snooze expires)

### Dismiss — `POST /drift/events/{id}/dismiss`

1. Set `DriftEvent.status="dismissed"`, `resolved_by`, `resolved_at`
2. No anchor change, no suppression — will re-alert on next poll

---

## API

```
# Drift policies
GET    /drift/policies
POST   /drift/policies
GET    /drift/policies/{id}
PATCH  /drift/policies/{id}
DELETE /drift/policies/{id}           # blocked if auto_created and within rollback window

# Drift events
GET    /drift/events                  # filterable: status, asset_id, surface_type, severity
GET    /drift/events/{id}
POST   /drift/events/{id}/accept      # body: {note}
POST   /drift/events/{id}/attest      # body: {note, snooze_days}
POST   /drift/events/{id}/dismiss

# Asset drift summary
GET    /assets/{id}/drift             # current ResourceState per surface + open events

# Agent tripwire callback (internal, HMAC-authenticated)
POST   /drift/agent-event             # body: {asset_id, surface_type, changed_paths}
```

Shadow CR approval/execution uses existing `/change-requests/{id}/*` endpoints — no new routes needed.

---

## UI

**Drift Events page** (`/drift`) — lists all open events across the org. Columns: asset name, surface type, severity (colour-coded), detected at, diff summary (one-line). Click-through to event detail with full before/after diff table and the four action buttons. Auto-created policies are labelled "System-managed."

**Asset detail — Drift tab** — shows current `ResourceState` per surface (last observed, captured at, source CR link) and any open or recent events. Operators can create explicit `DriftPolicy` records from this tab for surfaces not yet monitored.

---

## Severity Mapping

| Surface type | Severity |
|---|---|
| `sudoers`, `users_groups`, `aws_iam_policy`, `aws_s3_bucket_policy`, `gcp_iam_binding`, `firewall_rules`, `aws_security_group`, `gcp_firewall_rule`, `oci_security_list` | high |
| `ssh_config`, `cron_jobs` | medium |
| `listening_ports`, `running_services` | low |

High severity drift fires immediate notifications. Medium fires on next notification batch (5-minute window). Low appears in the UI only — no push notification.

---

## Testing

### Unit tests

- `compute_diff()` — correct `added/removed/changed` for all surface types; edge cases: nested keys, array reordering, empty state, identical state
- Shadow CR parameter generation from `DriftEvent`
- Suppression logic: attested event with `suppress_until` in future is skipped by worker
- `DriftPolicy` deletion blocked within rollback window

### Integration tests

- Post-CR hook writes correct `ResourceState` and creates `DriftPolicy`
- Worker detects drift and creates `DriftEvent` with correct diff
- Accept: `ResourceState` advances, shadow CR cancelled, event resolved
- Attest: `ResourceState` unchanged, event suppressed for `snooze_days`
- Dismiss: no state change, event closed

### Smoke test phases

**DRIFT_ANCHOR** — execute `ssh_hardening` CR against a live agent host via full CR lifecycle. Verify `ResourceState` written with `source="cr_execution"` for `ssh_config` and `firewall_rules`. Verify `DriftPolicy` auto-created.

**DRIFT_DETECT** — mutate `/etc/ssh/sshd_config` out-of-band via direct agent job (bypassing CR lifecycle). Trigger manual poll. Verify `DriftEvent` created with correct diff. Verify shadow `restore_resource_state` CR created in DRAFT status.

**DRIFT_ACCEPT** — `POST /drift/events/{id}/accept` with note. Verify `ResourceState` advances to mutated state, shadow CR cancelled, event marked accepted.

**DRIFT_REMEDIATE** — repeat DRIFT_DETECT mutation. Approve and execute the shadow CR through full CR lifecycle. Verify host restored, `ResourceState` updated with `source="cr_execution"`, `DriftEvent` closed.

**DRIFT_CLOUD** — add an ingress rule to an AWS security group out-of-band via boto3. Wait for poll cycle. Verify `DriftEvent` created for `aws_security_group` surface. Verify shadow CR generated. Accept new state and verify anchor advances.
