# Certificate Rotation Design

## Goal

Add a `certificate_rotation` CR type that orchestrates a full TLS certificate rotation campaign: discover dependents via `scan_for_references`, snapshot pre-rotation state, issue a new cert via step-ca, update all dependents, verify TLS chain end-to-end, and rollback with FILO unwind if verification fails.

## Global Constraints

- No `from __future__ import annotations` in any new Python file
- All functionality tested against live infrastructure — no mocks
- Every CR passes through `awaiting_approval` before execution
- FILO rollback guarantee: dependents unwind in reverse update order
- Rollback strategy C: restore original cert if still valid (>24h remaining and `trigger_reason != "compromise"`), otherwise re-issue fresh cert
- `paused` and `rolled_back_with_warnings` statuses already exist (from `credential_rotation`) — reuse them
- Smoke test must exercise full lifecycle: create → plan → approve → execute → verify → rollback

---

## Section 1: CR Structure

New `change_type: certificate_rotation`. The `desired_outcome` is a structured object — not a steps array:

```json
{
  "change_type": "certificate_rotation",
  "desired_outcome": {
    "subject": "api.example.com",
    "san": ["api.example.com", "*.api.example.com"],
    "not_after": "720h",
    "trigger_reason": "scheduled",
    "scan_scope": ["aws", "kubernetes", "nexplane_agent"],
    "verify_timeout_seconds": 60
  }
}
```

`trigger_reason` is `"scheduled"` or `"compromise"`. When `"compromise"`, rollback always re-issues a fresh cert (strategy A) regardless of original cert validity. `scan_scope` limits which surfaces `scan_for_references` searches. `verify_timeout_seconds` controls how long to wait for services to reload before probing.

The `execution_result` tracks each phase:

```json
{
  "phase": "scan|snapshot|rotate|update|verify",
  "dependents": [
    {
      "index": 0,
      "type": "host",
      "host": "api.example.com",
      "port": 443,
      "connector_type": "nexplane_agent",
      "connector_id": "...",
      "snapshot": "<PEM string>",
      "snapshot_fingerprint": "<sha256>",
      "update_result": {},
      "verify_result": {},
      "rollback_result": {}
    },
    {
      "index": 1,
      "type": "k8s_secret",
      "namespace": "prod",
      "name": "api-tls",
      "connector_type": "kubernetes",
      "connector_id": "...",
      "snapshot": "<base64 secret data>",
      "update_result": {},
      "verify_result": {},
      "rollback_result": {}
    }
  ],
  "rotation_result": {
    "subject": "api.example.com",
    "fingerprint": "<sha256>",
    "cert_pem": "<PEM>",
    "key_pem": "<PEM>",
    "issued_at": "<ISO>"
  },
  "rollback_strategy": "restore|reissue",
  "has_warnings": false
}
```

Dependent types:
- `"host"` — a server/load balancer/ingress actively serving the cert (type-A)
- `"k8s_secret"` — a Kubernetes Secret embedding the cert PEM (type-B)
- `"aws_secret"` — an AWS Secrets Manager secret containing the cert (type-B)
- `"config_file"` — a config file on a host referencing the cert path/fingerprint (type-B)

---

## Section 2: Execution Lifecycle

**Standard flow:** `draft → planned → awaiting_approval → approved → executing → completed`

**On verify failure:** CR transitions to `paused`. The `execution_result.phase` is `"verify"` and each failed dependent has `verify_result.success: false`. Operator options:
- `POST /change-requests/{id}/retry-verify` — re-run verification for all failed dependents; CR returns to `executing`
- `POST /change-requests/{id}/skip-verify` — mark all remaining verify failures as skipped; transition to `completed`
- `POST /change-requests/{id}/rollback` — trigger FILO unwind

**All dependents verified:** CR transitions to `completed`.

### Execution Phases (serial)

**Phase 1 — Scan:**
Calls `scan_for_references` orchestrator with `subject`, `san` values, and cert fingerprint (if already known from a previous check) as search terms across `scan_scope`. Builds the `dependents` list. Each dependent gets an `index` assigned in discovery order.

**Phase 2 — Snapshot:**
For each dependent:
- Type-A host: TLS probe via `_check_via_ssl` (reuse from `check_expiry.py`), capture current cert PEM via DER decode. Store as `snapshot` + `snapshot_fingerprint`.
- Type-B (K8s secret, AWS secret, config file): read current value via appropriate connector. Store as `snapshot`.

Snapshot failure for a dependent: log warning, set `snapshot: null`. Rollback will use strategy A (re-issue) for this dependent even if strategy B was intended.

**Phase 3 — Rotate:**
Calls `step_ca.rotate_certificate` executor with `subject`, `san`, `not_after` from `desired_outcome`. Stores `cert_pem`, `key_pem`, `fingerprint` in `execution_result.rotation_result`. The cert content is passed to subsequent phases — never written to disk on the backend.

**Phase 4 — Update:**
Fans out to all dependents serially (FILO stack order — first updated is last to roll back):
- Type-A host with `nexplane_agent`: `nexplane_agent.manage_tls_certificates` with new cert/key PEM + reload command
- Type-A host with SSM: `step_ca.rotate_certificate` deploy_via_ssm path
- Type-B K8s secret: patch secret data via kubernetes connector
- Type-B AWS secret: `rotate_secrets_manager_secret` with new cert PEM as value
- Type-B config file: `nexplane_agent` file-write + service reload

Each dependent's `update_result` is recorded. If any update fails, CR transitions to `paused` at the update phase.

**Phase 5 — Verify:**
After `verify_timeout_seconds`, probes each dependent:
- Type-A host: TLS handshake via `_check_via_ssl`, assert served fingerprint matches `rotation_result.fingerprint`, assert chain validates, assert SAN matches
- Type-B K8s secret: read secret, assert cert PEM matches `rotation_result.cert_pem`
- Type-B AWS secret: read secret value, assert matches
- Type-B config file: checksum of file content matches expected

Dependent-level `verify_result`: `{"success": bool, "fingerprint": str, "error": str|null}`.

### Auto-Trigger

`credential_expiry_worker._check_step_ca_certs` is extended: instead of calling `client.renew_certificate()` directly, it creates a draft `certificate_rotation` CR and submits it for approval (`plan` + `submit-for-approval`). The CR still requires human approval before executing. The existing `_create_expiry_finding` call is preserved as fallback if CR creation fails.

The `_check_tls_certs` function (port 443 probe of known assets) is similarly extended: when a cert is within `SLA_CRITICAL` threshold (7 days), it creates a `certificate_rotation` CR draft rather than only a finding.

---

## Section 3: Rollback Strategy

Rollback is triggered via `POST /change-requests/{id}/rollback` (existing endpoint). For `certificate_rotation` CRs:

**Determine strategy:**
- If `trigger_reason == "compromise"` → strategy A (re-issue) always
- If original cert `snapshot_fingerprint` is present AND original cert has >24h remaining (check via TLS probe of any type-A host, or parse snapshot PEM) → strategy B (restore)
- Otherwise → strategy A (re-issue)

Record `execution_result.rollback_strategy = "restore"|"reissue"`.

**FILO unwind — for each dependent in reverse index order:**

Strategy B (restore):
- Type-A host: push `dependent.snapshot` PEM back via appropriate connector, reload service
- Type-B: restore `dependent.snapshot` value

Strategy A (re-issue):
- Call `step_ca.rotate_certificate` again with same `subject`/`san` to get a fresh cert
- Push fresh cert to dependent via same update path as Phase 4
- The "re-issued for rollback" cert becomes what's deployed — no further verification pass

**Per-dependent rollback result:** `{"rolled_back": bool, "strategy": "restore|reissue", "error": str|null}`

**Terminal status:**
- `rolled_back` — all dependents restored/reissued
- `rolled_back_with_warnings` — some dependents could not be restored (step-ca CA-level cert is never revoked by this flow; operator must run `step ca revoke` separately for compromise cases)

**Note on CA state:** The step-ca CA itself is never "rolled back" — a cert once issued cannot be un-issued. Rollback only controls what is *deployed* to dependents. For `trigger_reason == "compromise"`, operators should separately revoke the compromised cert serial via step-ca CLI.

---

## Section 4: New API Endpoints

**`POST /change-requests/{id}/retry-verify`**
- Valid only when `cr.status == paused` and `cr.change_type == certificate_rotation`
- Admin/approver role required
- Re-runs Phase 5 (Verify) for all dependents with `verify_result.success: false`
- Transitions CR back to `executing`
- Returns 400 if not `paused`

**`POST /change-requests/{id}/skip-verify`**
- Valid only when `cr.status == paused` and `cr.change_type == certificate_rotation`
- Admin/approver role required
- Marks all remaining verify failures as `skipped`
- Transitions CR to `completed`
- Returns 400 if not `paused`

Both endpoints fire the resume coroutine via `asyncio.ensure_future` with the `_resume_execution` wrapper pattern (error → `failed` transition) established in `credential_rotation`.

---

## Section 5: Files

| Action | Path |
|--------|------|
| Modify | `backend/app/models/change_request.py` — add `certificate_rotation` to `ChangeType` |
| Create | `backend/alembic/versions/cert001_certificate_rotation.py` — `ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'certificate_rotation'` |
| Modify | `backend/app/services/planning_engine.py` — validate `desired_outcome` fields, check step-ca connector exists |
| Create | `backend/app/services/certificate_rotation_executor.py` — `execute_certificate_rotation(cr_id)`, `execute_certificate_rollback(cr_id, execution_result)` |
| Modify | `backend/app/workflows/activities.py` — dispatch branch for `certificate_rotation` |
| Modify | `backend/app/workflows/execute_change_workflow.py` — termination logic (paused on verify failure, completed on success) |
| Modify | `backend/app/services/rollback_executor.py` — `certificate_rotation` branch calling `execute_certificate_rollback` |
| Modify | `backend/app/routers/change_requests.py` — `retry-verify` and `skip-verify` endpoints |
| Modify | `backend/app/workers/credential_expiry_worker.py` — auto-trigger CR creation in `_check_step_ca_certs` and `_check_tls_certs` |
| Create | `backend/tests/smoke/test_certificate_rotation_smoke.py` |

---

## Section 6: Smoke Test

File: `backend/tests/smoke/test_certificate_rotation_smoke.py`

Uses `NexplaneClient`, `get_connector_creds_from_db("step_ca")`. Skips if no step-ca connector found.

### Setup (class-level)

Provisions a smoke nginx on EC2 port 8443 with a step-ca issued cert for subject `nexplane-smoke-cert.internal`:
1. Issue initial cert via `step_ca.rotate_certificate` executor directly (using connector from DB)
2. Deploy cert to EC2 platform host port 8443 via SSM (minimal nginx config)
3. Register smoke host as an asset in the platform DB (so `scan_for_references` can find it)

Teardown removes the nginx config and revokes the smoke cert.

### Phase 1 — Happy path (type-A host)

1. Create `certificate_rotation` CR: subject `nexplane-smoke-cert.internal`, `scan_scope: ["nexplane_agent"]`, `trigger_reason: "scheduled"`
2. Plan → approve → execute → poll until `completed`
3. Assert `execution_result.dependents[0].verify_result.success == true`
4. Assert served fingerprint on port 8443 matches `rotation_result.fingerprint`
5. Trigger rollback → poll until `rolled_back` or `rolled_back_with_warnings`
6. Assert original cert (or re-issued cert) is now served on port 8443 (fingerprint changed back)

### Phase 2 — Type-B consumer (K8s secret)

1. Pre-create K8s secret `nexplane-smoke-tls` in namespace `default` with current cert PEM
2. Create `certificate_rotation` CR: subject `nexplane-smoke-cert.internal`, `scan_scope: ["kubernetes"]`
3. Execute → poll `completed`
4. Assert K8s secret value matches new `rotation_result.cert_pem`
5. Rollback → assert secret restored to original PEM

### Phase 3 — Compromise trigger

1. Create `certificate_rotation` CR: `trigger_reason: "compromise"`, same subject
2. Execute → poll `completed`
3. Trigger rollback → assert `rollback_strategy == "reissue"` (not `"restore"`)
4. Assert new fingerprint served (re-issued cert, not original)

### Phase 4 — Verify failure → paused → rollback

The CR includes two dependents: the real nginx on port 8443 (will verify successfully) and a synthetic host entry pointing to port 9999 on localhost (always unreachable). The port 9999 entry is injected by pre-registering a fake asset so `scan_for_references` returns it.

1. Pre-register synthetic asset `127.0.0.1:9999` as a server asset with subject `nexplane-smoke-cert.internal`
2. Create `certificate_rotation` CR: subject `nexplane-smoke-cert.internal`, `scan_scope: ["nexplane_agent"]`
3. Execute → poll until `paused`
4. Assert `execution_result.phase == "verify"`, port 9999 dependent has `verify_result.success == false`, port 8443 dependent has `verify_result.success == true`
5. Trigger rollback → poll until `rolled_back` or `rolled_back_with_warnings`
6. Assert port 8443 serves original (or re-issued) cert

### Phase 5 — Auto-trigger via expiry worker

1. Issue a cert with `not_after: "2h"` for subject `nexplane-smoke-expiring.internal`
2. Call `credential_expiry_worker._check_step_ca_certs` directly with a threshold of `1` day (so 2h cert qualifies)
3. Assert a draft `certificate_rotation` CR was created for subject `nexplane-smoke-expiring.internal`
4. Plan → approve → execute → poll `completed`
5. Assert new cert issued with default `not_after`

All phases use step-ca connector credentials from `get_connector_creds_from_db("step_ca")`. K8s credentials from `get_connector_creds_from_db("kubernetes")` (Phase 2 skips if not found).
