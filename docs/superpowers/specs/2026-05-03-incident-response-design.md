# Incident Response Playbooks — Design Spec

**Date:** 2026-05-03
**Status:** Approved
**Scope:** Four pre-built, fast-path change workflows for common incident types — Host Isolation, Account Lockdown, Phishing Response, and Evidence Preservation — backed by two new agent command packages, new change types, DB schema additions, and a dedicated frontend tab.

---

## Background

Responders currently hand-craft change requests during active incidents, selecting individual steps from generic connector actions. Under time pressure this is slow, error-prone, and inconsistent. This spec introduces Incident Response Playbooks: pre-defined change request templates stored in the database, instantiated with a single click, with an `incident_response: true` flag that enables expedited or auto-approval for designated IR responders. Each playbook maps to a new first-class change type with a defined rollback.

---

## Design Decisions

- **Template-first:** Playbook definitions live in a `ir_playbook_templates` table. Instantiation copies the template into a normal change request row, stamped with `incident_response: true`. All existing approval, audit, and execution infrastructure is reused.
- **Expedited approval:** Change requests with `incident_response: true` bypass the standard reviewer-quorum rule when the requesting user holds the `ir_responder` role. A platform admin may also configure `ir_auto_approve: true` per playbook type.
- **Agent isolation is EDR-independent:** Host Isolation is implemented via the agent using OS-native firewall APIs. This ensures isolation even when an EDR sensor is compromised or offline.
- **Evidence before remediation:** The Evidence Preservation playbook must complete (or be explicitly skipped) before Host Isolation or Account Lockdown steps execute on the same asset. The API enforces this ordering at instantiation time when an asset is named in both.
- **Parallel connector steps:** Account Lockdown and Phishing Response steps that target independent connectors execute concurrently via `asyncio.gather` in the backend step runner. Each step's result is recorded independently; a partial failure does not abort the remaining parallel steps — the change request surface shows per-step status.
- **Rollback is always defined:** Every new change type ships with a rollback handler. The rollback for isolation and lockdown restores pre-action state captured at execution time (not a static inverse), so it adapts to the actual connector state found at execution.

---

## Section 1: New Agent Command Packages

### 1.1 `agent/commands/isolation/`

Implements OS-native network isolation. Called by the `isolate_host` change type step.

**New files:**
- `agent/commands/isolation/isolation.go` — dispatcher
- `agent/commands/isolation/linux.go` — iptables/nftables implementation
- `agent/commands/isolation/windows.go` — Windows Firewall implementation

**Go interface:**

```go
package isolation

import "context"

// IsolationConfig is sent from the control plane as the step payload.
type IsolationConfig struct {
    ManagementCIDR  string `json:"management_cidr"`  // e.g. "10.0.0.0/8"
    ControlPlaneURL string `json:"control_plane_url"` // always allow outbound to this host
}

// PreIsolationState is captured before isolation and stored for rollback.
type PreIsolationState struct {
    OS           string   `json:"os"`            // "linux" or "windows"
    IPTablesRules string  `json:"iptables_rules,omitempty"` // iptables-save output (Linux)
    NFTablesRules string  `json:"nftables_rules,omitempty"` // nft list ruleset output (Linux)
    WFWRules      string  `json:"wfw_rules,omitempty"`      // netsh advfirewall export dump (Windows)
}

// Isolate captures pre-state, applies isolation rules, and returns the state needed for rollback.
func Isolate(ctx context.Context, cfg IsolationConfig) (*PreIsolationState, error)

// Restore applies the pre-isolation state captured by Isolate.
func Restore(ctx context.Context, state *PreIsolationState) error
```

**Linux (`linux.go`) — isolation rules applied in order:**

1. Save current state: `iptables-save` (and `nft list ruleset` if nftables is active).
2. Flush all OUTPUT chain rules: `iptables -F OUTPUT`.
3. Allow established/related: `iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT`.
4. Allow SSH outbound to management CIDR: `iptables -A OUTPUT -d <management_cidr> -p tcp --dport 22 -j ACCEPT`.
5. Allow outbound to control plane IP on port 443: `iptables -A OUTPUT -d <control_plane_ip> -p tcp --dport 443 -j ACCEPT`.
6. Drop all other outbound: `iptables -P OUTPUT DROP`.
7. Repeat steps 2–6 for ip6tables.

**Windows (`windows.go`) — isolation rules:**

1. Export current firewall policy: `netsh advfirewall export "%TEMP%\wfw_pre_isolation.wfw"` — read file into `PreIsolationState.WFWRules`.
2. Block all outbound by default: `netsh advfirewall set allprofiles firewallpolicy blockinbound,blockoutbound`.
3. Add outbound allow rule for management CIDR: `netsh advfirewall firewall add rule name="NX_IR_MGMT_OUT" dir=out action=allow remoteip=<management_cidr> protocol=any`.
4. Add outbound allow rule for control plane: `netsh advfirewall firewall add rule name="NX_IR_CP_OUT" dir=out action=allow remoteip=<control_plane_ip> protocol=tcp localport=443`.

**Rollback (`Restore`):**
- Linux: pipe `PreIsolationState.IPTablesRules` into `iptables-restore`; if nftables was in use, pipe `NFTablesRules` into `nft -f -`.
- Windows: `netsh advfirewall import` from the exported `.wfw` bytes written back to a temp file.

---

### 1.2 `agent/commands/forensics/`

Collects forensic artifacts before remediation. Called by the `preserve_evidence` change type.

**New files:**
- `agent/commands/forensics/forensics.go` — dispatcher + bundle assembly
- `agent/commands/forensics/linux.go` — Linux artifact collection
- `agent/commands/forensics/windows.go` — Windows artifact collection

**Go interface:**

```go
package forensics

import (
    "context"
    "io"
)

// ForensicsConfig is sent from the control plane as the step payload.
type ForensicsConfig struct {
    UploadURL  string `json:"upload_url"`   // pre-signed S3/object-storage URL for the bundle
    AssetID    string `json:"asset_id"`
    IncludeMemoryDump bool `json:"include_memory_dump"` // false by default (expensive)
}

// ForensicBundle is the manifest returned to the control plane on success.
type ForensicBundle struct {
    BundleID      string            `json:"bundle_id"`       // UUID assigned by control plane
    UploadedAt    string            `json:"uploaded_at"`     // RFC3339
    Artifacts     []ArtifactEntry   `json:"artifacts"`
}

type ArtifactEntry struct {
    Name      string `json:"name"`
    SizeBytes int64  `json:"size_bytes"`
    SHA256    string `json:"sha256"`
}

// Collect gathers all artifacts, assembles them into a tar.gz, uploads to UploadURL,
// and returns the manifest. The caller (control plane) assigns BundleID.
func Collect(ctx context.Context, cfg ForensicsConfig) (*ForensicBundle, error)
```

**Linux artifacts collected:**

| Artifact | Method |
|---|---|
| `/var/log/auth.log` (or `/var/log/secure`) | Direct file read |
| `/var/log/syslog` or `journalctl -n 50000 --no-pager` | journalctl subprocess |
| `auditd` log: `/var/log/audit/audit.log` | Direct file read |
| Process list + maps: `ps aux` + `/proc/*/status` + `/proc/*/maps` (text summary, not raw mem) | Procfs walk |
| Network state: `ss -antp` + `arp -n` + `ip route` | Subprocess |
| Memory dump (optional): `/proc/<pid>/mem` summary — reads only readable VMA regions for PID 1 and top-CPU process, writes offsets + SHA256 per region | Procfs walk with region bounds from `/proc/<pid>/maps` |

**Windows artifacts collected:**

| Artifact | Method |
|---|---|
| System event log (last 10 000 events) | `wevtutil qe System /count:10000 /format:XML` |
| Security event log (last 10 000 events) | `wevtutil qe Security /count:10000 /format:XML` |
| Process list | `tasklist /v /fo CSV` |
| Network state: active connections + ARP + routes | `netstat -ano`, `arp -a`, `route print` |
| Memory dump (optional): `procdump64.exe -ma -accepteula <system-pid>` if procdump is present on PATH; otherwise skip and log warning | Subprocess |

**Bundle assembly:**
All artifacts are streamed into a `tar.gz` archive in memory (capped at 2 GB). The archive is uploaded via HTTP PUT to `ForensicsConfig.UploadURL`. The manifest (name, size, SHA256 per artifact) is returned to the control plane in the step result payload.

---

## Section 2: New Change Types

### 2.1 `isolate_host`

**Parameters (stored in `change_requests.parameters` JSONB):**

```json
{
  "asset_id": "uuid",
  "management_cidr": "10.0.0.0/8",
  "reason": "free-text reason for isolation"
}
```

**Steps (sequential):**
1. `preserve_evidence` (optional pre-step — skipped if a bundle already exists for this asset within the last 1 hour, or if operator explicitly skips)
2. `agent_command`: send `isolate` command to the asset's agent with `IsolationConfig`
3. Record `PreIsolationState` returned by agent into `change_requests.step_results[2].state` JSONB

**Rollback steps (sequential):**
1. `agent_command`: send `restore_network_access` command with `PreIsolationState` from step 3 above

**Connector:** agent (no external connector required)

---

### 2.2 `lockdown_account`

**Parameters:**

```json
{
  "email": "user@example.com",
  "reason": "free-text"
}
```

**Steps (parallel, all execute concurrently):**

| Step | Connector | Action |
|---|---|---|
| `ad_disable` | Active Directory | Disable AD account for `email` |
| `okta_suspend` | Okta | Suspend Okta user + call `DELETE /api/v1/users/{id}/sessions` |
| `entraid_suspend` | Entra ID | Disable sign-in (`accountEnabled: false`) + `POST /users/{id}/revokeSignInSessions` |
| `google_suspend` | Google Workspace | `users.update` with `suspended: true` + revoke all OAuth tokens via `tokens.list` + `tokens.delete` |
| `github_revoke` | GitHub | List and delete all user tokens/authorizations in org via `DELETE /orgs/{org}/members/{username}` + revoke OAuth grants |
| `slack_deactivate` | Slack | `POST /admin.users.setInactive` |

Per-step results are recorded in `change_requests.step_results` keyed by step name. A failed step is retried once after 10 seconds before being marked failed. Overall change request status is `partial_failure` if any step fails, `completed` if all succeed.

**Rollback steps (parallel):**

| Step | Action |
|---|---|
| `ad_enable` | Re-enable AD account |
| `okta_unsuspend` | `POST /api/v1/users/{id}/lifecycle/unsuspend` |
| `entraid_enable` | `accountEnabled: true` |
| `google_unsuspend` | `suspended: false` |
| `github_reinvite` | Re-invite user to org (note: tokens are not restored — user must re-authorize) |
| `slack_activate` | `POST /admin.users.setActive` |

---

### 2.3 `phishing_response`

**Parameters:**

```json
{
  "sender_domain": "evil.example.com",
  "affected_user_emails": ["alice@corp.com", "bob@corp.com"],
  "reason": "free-text"
}
```

**Steps (sequential by phase, parallel within phase):**

**Phase 1 — Block sender domain (parallel):**
- `defender_block_domain`: Defender ATP — add `sender_domain` to tenant-level block list via `POST /api/v1/indicators`
- `google_block_domain`: Google Workspace — add `sender_domain` to Admin Console blocked senders list via `POST /admin/directory/v1/customer/{customerId}/blockSenders`

**Phase 2 — Force password reset for affected users (parallel per user):**
- For each email in `affected_user_emails`:
  - `ad_force_password_reset`: set `pwdLastSet = 0` on AD user
  - `okta_expire_password`: `POST /api/v1/users/{id}/lifecycle/expire_password`
  - `google_force_password_change`: `changePasswordAtNextLogin: true`
  - `entraid_force_password_change`: `POST /users/{id}/invalidateAllRefreshTokens` (forces re-auth)

**Phase 3 — Revoke active sessions (parallel per user per connector):**
- AD: `Reset-ComputerMachinePassword` via agent on domain controller (Kerberos ticket purge)
- Okta: `DELETE /api/v1/users/{id}/sessions`
- Google: `tokens.list` + `tokens.delete` for all tokens

**Phase 4 — Force MFA re-enrollment (parallel per user per connector):**
- Okta: `DELETE /api/v1/users/{id}/factors` (removes all enrolled factors, triggers re-enrollment on next login)
- Entra ID: `DELETE /users/{id}/authentication/methods/{methodId}` for all non-password methods
- Google: Reset 2-Step verification enrollment via `POST /admin/directory/v1/users/{userKey}` with `isEnrolledIn2Sv: false` (requires admin SDK)

**Phase 5 — Generate report (sequential, after all prior phases):**
- Aggregate per-step results from phases 1–4
- Write `phishing_response_report` JSONB into `change_requests.output`:
  ```json
  {
    "sender_domain": "evil.example.com",
    "blocked_in": ["defender", "google_workspace"],
    "affected_users": [
      {
        "email": "alice@corp.com",
        "password_reset": {"ad": "ok", "okta": "ok", "google": "ok", "entraid": "ok"},
        "sessions_revoked": {"ad": "ok", "okta": "ok", "google": "ok"},
        "mfa_reset": {"okta": "ok", "entraid": "ok", "google": "failed"}
      }
    ],
    "completed_at": "2026-05-03T14:22:01Z"
  }
  ```

**Rollback:** Domain block list entries are removed. Password reset and MFA re-enrollment are not reversed (by design — the user will authenticate fresh). Rollback is flagged as `partial` in the UI with an explanation.

---

### 2.4 `preserve_evidence`

**Parameters:**

```json
{
  "asset_id": "uuid",
  "include_memory_dump": false,
  "upload_destination": "s3://nexplane-forensics/bundles/"
}
```

**Steps (sequential):**
1. Control plane generates a pre-signed upload URL for `upload_destination/<bundle_id>.tar.gz` and assigns `bundle_id` (UUID).
2. `agent_command`: send `collect_forensics` to the asset's agent with `ForensicsConfig`.
3. Agent uploads bundle and returns `ForensicBundle` manifest.
4. Control plane stores manifest in `forensic_bundles` table (see Section 3).
5. Change request `output` JSONB is set to `{"bundle_id": "<uuid>", "artifact_count": N, "size_bytes": M}`.

**Rollback:** Not applicable (evidence collection is non-destructive). The rollback handler is a no-op that returns success.

---

## Section 3: Database Schema Changes

### 3.1 `change_requests` table — new columns

```sql
ALTER TABLE change_requests
    ADD COLUMN incident_response   BOOLEAN      NOT NULL DEFAULT FALSE,
    ADD COLUMN ir_playbook_type    VARCHAR(64),   -- 'isolate_host' | 'lockdown_account' | 'phishing_response' | 'preserve_evidence'
    ADD COLUMN ir_template_id      UUID REFERENCES ir_playbook_templates(id),
    ADD COLUMN step_results        JSONB         NOT NULL DEFAULT '{}',
    ADD COLUMN output              JSONB;
```

`step_results` structure:
```json
{
  "step_name": {
    "status": "completed|failed|skipped",
    "started_at": "RFC3339",
    "completed_at": "RFC3339",
    "error": "optional error string",
    "state": {}
  }
}
```

### 3.2 `ir_playbook_templates` table — new table

```sql
CREATE TABLE ir_playbook_templates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    playbook_type   VARCHAR(64)  NOT NULL UNIQUE,
    display_name    VARCHAR(255) NOT NULL,
    description     TEXT,
    default_parameters  JSONB    NOT NULL DEFAULT '{}',
    ir_auto_approve BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

INSERT INTO ir_playbook_templates (playbook_type, display_name, description, default_parameters) VALUES
  ('isolate_host',       'Host Isolation',        'Isolate a compromised host via agent-side firewall rules', '{"management_cidr": "10.0.0.0/8"}'),
  ('lockdown_account',   'Account Lockdown',      'Disable a user across all identity connectors simultaneously', '{}'),
  ('phishing_response',  'Phishing Response',     'Block sender domain, reset passwords, revoke sessions, re-enroll MFA', '{}'),
  ('preserve_evidence',  'Evidence Preservation', 'Collect forensic artifacts from host before remediation', '{"include_memory_dump": false}');
```

### 3.3 `forensic_bundles` table — new table

```sql
CREATE TABLE forensic_bundles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id        UUID         NOT NULL REFERENCES assets(id),
    change_request_id UUID       REFERENCES change_requests(id),
    upload_url      TEXT         NOT NULL,
    manifest        JSONB        NOT NULL DEFAULT '{}',
    size_bytes      BIGINT,
    collected_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_forensic_bundles_asset_id ON forensic_bundles(asset_id);
CREATE INDEX idx_forensic_bundles_collected_at ON forensic_bundles(collected_at);
```

### 3.4 User roles

Add `ir_responder` to the allowed values for `users.role` (existing VARCHAR column). IR responders may instantiate and auto-approve IR change requests when `ir_auto_approve` is set on the template.

---

## Section 4: New API Endpoints

All endpoints are under the existing FastAPI router at `backend/api/`.

### `GET /api/ir/templates`
Returns all rows from `ir_playbook_templates`.

**Response:**
```json
[
  {
    "id": "uuid",
    "playbook_type": "isolate_host",
    "display_name": "Host Isolation",
    "description": "...",
    "default_parameters": {"management_cidr": "10.0.0.0/8"},
    "ir_auto_approve": false
  }
]
```

---

### `POST /api/ir/instantiate`
Creates a change request from a playbook template. Merges `default_parameters` with caller-supplied `parameters`.

**Request:**
```json
{
  "playbook_type": "isolate_host",
  "parameters": {
    "asset_id": "uuid",
    "management_cidr": "192.168.1.0/24",
    "reason": "CrowdStrike alert - lateral movement detected"
  }
}
```

**Response:** standard `change_request` object with `incident_response: true` set.

**Authorization:** requires `ir_responder` role or `admin` role.

**Behavior:**
- Copies template `default_parameters`, merges with request `parameters` (request wins on conflict).
- Sets `incident_response = true` and `ir_playbook_type` on the new change request.
- If the template has `ir_auto_approve = true` and the caller holds `ir_responder` or `admin`, sets `status = approved` immediately and enqueues execution.
- Otherwise creates in `pending_approval` status with expedited approval queue (single approver required rather than quorum).

---

### `GET /api/ir/bundles`
List forensic bundles, filterable by `asset_id` and date range.

**Query params:** `asset_id`, `since` (RFC3339), `until` (RFC3339), `limit` (default 50).

**Response:** array of `forensic_bundle` rows including manifest.

---

### `GET /api/ir/bundles/{bundle_id}`
Fetch a single forensic bundle manifest. Does not return raw artifact data (use upload URL directly).

---

### `GET /api/ir/bundles/{bundle_id}/download-url`
Returns a fresh pre-signed download URL for the bundle archive. Requires `ir_responder` or `admin` role.

**Response:**
```json
{"url": "https://s3.amazonaws.com/nexplane-forensics/bundles/<bundle_id>.tar.gz?X-Amz-..."}
```

---

## Section 5: Agent Command Protocol Changes

The agent currently receives commands as JSON via the poll endpoint. Two new command types are added to `agent/main.go`'s command dispatcher:

```go
// In agent/main.go command dispatch switch:
case "isolate":
    var cfg isolation.IsolationConfig
    if err := json.Unmarshal(cmd.Payload, &cfg); err != nil { ... }
    state, err := isolation.Isolate(ctx, cfg)
    result = commandResult{State: state, Err: err}

case "restore_network_access":
    var state isolation.PreIsolationState
    if err := json.Unmarshal(cmd.Payload, &state); err != nil { ... }
    err = isolation.Restore(ctx, &state)
    result = commandResult{Err: err}

case "collect_forensics":
    var cfg forensics.ForensicsConfig
    if err := json.Unmarshal(cmd.Payload, &cfg); err != nil { ... }
    bundle, err := forensics.Collect(ctx, cfg)
    result = commandResult{Bundle: bundle, Err: err}
```

`commandResult` is serialized and returned in the agent's next poll response as `step_result`.

---

## Section 6: Frontend — Incident Response Tab

**File:** `frontend/src/pages/ChangeRequests.tsx` (existing page)

Add a tab bar at the top of the Change Requests page:
- **All** (existing view)
- **Incident Response** (new view, filtered to `incident_response = true`)

**Incident Response tab contents:**

1. **Playbook launcher panel** — card grid, one card per template returned by `GET /api/ir/templates`. Each card shows:
   - Display name and description
   - "Launch" button — opens a modal with a form pre-filled from `default_parameters`
   - Auto-approve badge if `ir_auto_approve = true`

2. **Active IR change requests table** — same columns as the standard change request table but with an additional "Steps" column showing per-step status icons (green check, red X, spinner, dash).

3. **Forensic Bundles panel** (below the table) — shows bundles collected in the last 7 days with asset name, collected timestamp, bundle size, and a "Download" button that calls `GET /api/ir/bundles/{id}/download-url` and opens the URL.

**New file:** `frontend/src/components/IRPlaybookLauncher.tsx`

```typescript
interface IRPlaybookLauncherProps {
  template: IRPlaybookTemplate;
  onLaunched: (changeRequest: ChangeRequest) => void;
}

// Renders a form from template.default_parameters keys.
// On submit: POST /api/ir/instantiate, then calls onLaunched.
```

**New file:** `frontend/src/components/IRStepStatusBadge.tsx`

```typescript
interface IRStepStatusBadgeProps {
  stepResults: Record<string, StepResult>;
}
// Renders a horizontal row of colored icons (one per step) with tooltip on hover.
```

---

## Section 7: Backend Module Layout

**New Python modules under `backend/`:**

```
backend/
  api/
    ir.py                        # FastAPI router: /api/ir/* endpoints
  services/
    ir_executor.py               # Orchestrates multi-step / parallel IR execution
    ir_forensics.py              # Generates pre-signed S3 URLs, stores bundle manifests
  models/
    ir_playbook_template.py      # SQLAlchemy model for ir_playbook_templates
    forensic_bundle.py           # SQLAlchemy model for forensic_bundles
  alembic/versions/
    XXXX_add_ir_tables.py        # Migration: new columns + new tables
```

`ir_executor.py` key function:

```python
async def execute_ir_change_request(cr_id: uuid.UUID, db: AsyncSession) -> None:
    """
    Loads the change request, dispatches steps (parallel or sequential per playbook type),
    records per-step results into change_requests.step_results, and sets final status.
    """
```

Parallel steps are dispatched with `asyncio.gather(*step_coroutines, return_exceptions=True)`. Each coroutine writes its result directly to `step_results[step_name]` via an upsert on the JSONB column to avoid write conflicts.

---

## Files Changed

| File | Change |
|---|---|
| `agent/commands/isolation/isolation.go` | New — `Isolate` / `Restore` dispatcher |
| `agent/commands/isolation/linux.go` | New — iptables/nftables isolation for Linux |
| `agent/commands/isolation/windows.go` | New — Windows Firewall isolation |
| `agent/commands/forensics/forensics.go` | New — `Collect` dispatcher + tar.gz assembly + upload |
| `agent/commands/forensics/linux.go` | New — Linux artifact collection |
| `agent/commands/forensics/windows.go` | New — Windows artifact collection |
| `agent/main.go` | Add `isolate`, `restore_network_access`, `collect_forensics` cases to command dispatcher |
| `backend/api/ir.py` | New — FastAPI router for `/api/ir/*` |
| `backend/services/ir_executor.py` | New — IR change request execution orchestrator |
| `backend/services/ir_forensics.py` | New — pre-signed URL generation, bundle manifest persistence |
| `backend/models/ir_playbook_template.py` | New — SQLAlchemy model |
| `backend/models/forensic_bundle.py` | New — SQLAlchemy model |
| `backend/alembic/versions/XXXX_add_ir_tables.py` | New — migration: `ir_playbook_templates`, `forensic_bundles`, new `change_requests` columns |
| `frontend/src/pages/ChangeRequests.tsx` | Add Incident Response tab + tab routing |
| `frontend/src/components/IRPlaybookLauncher.tsx` | New — playbook card grid + launch modal |
| `frontend/src/components/IRStepStatusBadge.tsx` | New — per-step status icon row |
| `frontend/src/api/ir.ts` | New — typed API client functions for `/api/ir/*` |
