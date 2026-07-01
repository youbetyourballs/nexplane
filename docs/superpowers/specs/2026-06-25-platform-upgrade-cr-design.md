# Platform Upgrade CR — Design Spec

**Date:** 2026-06-25
**Status:** Approved for planning

---

## Goal

Make Nexplane platform upgrades a first-class CR — with the same plan, approve, execute, and rollback lifecycle as every other infrastructure change — for both self-hosted and hosted deployments. Self-hosted operators initiate their own upgrade CRs; ops initiates upgrade CRs against hosted customer instances. Same CR type, same executor interface, different initiator and connector.

## Architecture

### Guiding principle: dogfood the platform

The upgrade mechanism uses the Nexplane CR lifecycle rather than out-of-band scripts. This closes the credibility gap ("we enforce change control on everything except ourselves") and gives operators a full audit trail for every version bump including rollbacks.

### Components

| Component | Responsibility |
|-----------|---------------|
| `platform_upgrade` CR type | Declarative phase definition, failure policy |
| `platform_upgrade` executor (core) | Runs phases 1–4 while backend is alive |
| `nexplane-watchdog` container | Handles phase 5 cutover; primary recovery path |
| Sentinel file (host volume) | Out-of-band state shared by executor, watchdog, and recovery script |
| `install.sh --recover` | Backstop recovery when Docker or watchdog is unhealthy |
| Version poller (backend service) | Checks `releases.nexplane.ai/latest.json` every 6 hours |
| Update banner (frontend) | Surfaces new version to admin; pre-populates draft CR |
| `upgrade_instance` executor (deploy) | Hosted path; drives same phases via SSM + watchdog signal |

---

## CR Type Definition

**File:** `backend/app/change_type_definitions/platform_upgrade.json`

**Fields:**

- `target_version` — exact semver tag to upgrade to (e.g. `1.4.2`)
- `image_sha256` — sha256 digest from release manifest; verified before cutover
- `changelog_url` — attached as context for the approver
- `require_approval` — boolean; always `true` for self-hosted; configurable per customer for hosted

---

## Phase Sequence

The executor runs phases 1–4 while the current backend is alive. Phase 5 is handed off to the watchdog. Phase 6 runs in the new backend on first startup.

```
Phase 1: preflight
Phase 2: snapshot
Phase 3: pull
Phase 4: migrate
Phase 5: cutover       ← watchdog takes over here
Phase 6: verify        ← new backend runs this on startup
```

### Phase detail

**Phase 1 — preflight**
- Confirm target version is newer than running version
- Confirm target version is >= `min_compatible_version` from release manifest
- Verify disk space sufficient for pg_dump + new image layer
- Confirm DB is reachable and healthy
- Confirm watchdog container is running and sentinel volume is mounted
- Write `preflight_complete` to sentinel file and DB

**Phase 2 — snapshot**
- Run `pg_dump` of the Nexplane database to `/nexplane-data/snapshots/pre_upgrade_<version>_<timestamp>.dump.gz` on the host-mounted volume
- Verify dump file integrity (size > 0, gzip valid)
- Write `snapshot_complete` + snapshot path + previous image tag to sentinel file and DB

**Phase 3 — pull**
- `docker pull nexplane/nexplane:<target_version>`
- Verify pulled image sha256 matches release manifest value
- Write `pull_complete` to sentinel file and DB

**Phase 4 — migrate**
- Run `alembic upgrade head` against the live DB
- If migration fails: run `alembic downgrade` to previous revision; if downgrade also fails, run `pg_restore` from snapshot; mark CR `failed`; do not proceed to cutover
- Write `migrate_complete` + alembic revision to sentinel file and DB

**Phase 5 — cutover (watchdog)**
- Executor writes `cutover_pending` + cutover timestamp to sentinel file and DB, then exits cleanly
- Watchdog detects `cutover_pending` state
- Watchdog updates `IMAGE_TAG` in `.env` to target version
- Watchdog stops and restarts the backend container (`docker compose up -d backend`)
- Watchdog polls `GET /health` every 5s for up to 90s
- On success: write `cutover_complete` to sentinel; proceed to phase 6
- On timeout: execute watchdog rollback (see Failure Handling)

**Phase 6 — verify (new backend)**
- On startup, new backend reads sentinel file
- If state is `cutover_complete`: confirm running version matches target version; mark CR `completed`; write `upgrade_complete` to sentinel; emit audit event
- If state is `cutover_pending` and backend is the new version: cutover succeeded but verify didn't run yet (e.g. restart race); run verify now
- If state is anything else: surface degraded mode to operator (see Failure Handling)

---

## Sentinel File

**Path:** `/nexplane-data/upgrade/sentinel.json` (host-mounted volume, shared by all containers)

**Schema:**

```json
{
  "state": "cutover_pending",
  "previous_version": "1.3.1",
  "previous_image_tag": "nexplane/nexplane:1.3.1",
  "target_version": "1.4.2",
  "snapshot_path": "/nexplane-data/snapshots/pre_upgrade_1.4.2_20260625T1200.dump.gz",
  "alembic_revision_before": "a1b2c3d4",
  "cutover_timestamp": "2026-06-25T12:05:00Z",
  "cr_id": "uuid-of-the-upgrade-cr"
}
```

States: `preflight_complete` → `snapshot_complete` → `pull_complete` → `migrate_complete` → `cutover_pending` → `cutover_complete` → `upgrade_complete`

The DB CR record mirrors this state at each phase. Both are updated together; the sentinel is the safety net when the DB connection is unavailable.

---

## Failure Handling

### Watchdog rollback (primary path)

Triggered when: backend fails to return HTTP 200 on `/health` within 90s of cutover.

Steps:
1. Stop backend container via Docker socket
2. Restore `IMAGE_TAG` in `.env` to `previous_image_tag` from sentinel
3. Run `pg_restore` from snapshot path in sentinel
4. Restart backend container on previous image
5. Write CR record directly to Postgres (backend is down): status = `failed`, reason = `watchdog_rollback`
6. Write `rollback_complete` to sentinel file
7. Log full sequence to `/nexplane-data/upgrade/watchdog.log`

### install.sh --recover (backstop)

Triggered manually by operator when instance is down and watchdog has not self-healed.

Reads sentinel file from host volume. Detects upgrade state. Performs the same restore sequence as the watchdog but runs entirely outside Docker — works even if Docker daemon is degraded. On completion, prints recovery summary and access URLs.

### Per-phase failure table

| Phase fails | Recovery |
|-------------|----------|
| preflight | CR blocked; no changes made; admin notified via UI notification + configured webhooks |
| snapshot | CR blocked; no changes made; admin notified via UI notification + configured webhooks |
| pull | CR blocked; previous image still running; admin notified via UI notification + configured webhooks |
| migrate (alembic downgrade succeeds) | Previous schema restored; CR failed; admin notified via UI notification + configured webhooks |
| migrate (alembic downgrade fails) | pg_restore from snapshot; CR failed; admin notified via UI notification + configured webhooks |
| cutover (watchdog rollback succeeds) | Previous version restored; CR failed; admin notified via UI notification + configured webhooks |
| cutover (watchdog rollback fails) | Degraded mode UI shown on next page load; `install.sh --recover` instructions surfaced |
| verify (new backend running but CR incomplete) | Degraded mode UI shown; admin presented with choice: mark complete or rollback |

### Degraded mode UI

If the backend starts and detects an unresolved upgrade state (sentinel state is not `upgrade_complete` or absent), the UI surfaces a full-screen degraded mode banner visible to admin users:

- Current state and what failed
- "Roll back to v{previous_version}" button — triggers pg_restore + image swap via watchdog
- "Mark as complete" button — for cases where the upgrade actually succeeded but verify failed to record it
- Link to `install.sh --recover` instructions for out-of-Docker recovery

---

## Version Check & Update Banner

**Backend:** A background task polls `https://releases.nexplane.ai/latest.json` every 6 hours (configurable via `UPDATE_CHECK_INTERVAL_HOURS` env var; set to `0` to disable). Disabled when `NEXPLANE_EDITION=commercial` (hosted instances receive updates via ops-initiated CRs).

**Release manifest schema:**

```json
{
  "version": "1.4.2",
  "released_at": "2026-06-25T00:00:00Z",
  "min_compatible_version": "1.2.0",
  "image_sha256": "sha256:abc123...",
  "changelog_url": "https://docs.nexplane.ai/changelog/1.4.2",
  "severity": "recommended"
}
```

`severity` values: `recommended` (blue banner) | `security` (amber banner) | `critical` (red banner, dismissal blocked).

**Frontend:** When a newer version is detected, a banner appears in the admin header (admin role only). "Review update" opens a pre-populated draft `platform_upgrade` CR with target version, sha256, severity, and changelog URL attached. Operator reviews, approves, and executes through the normal CR lifecycle.

**Running version:** Read from `NEXPLANE_VERSION` env var, set in `.env` by the upgrade executor on each successful upgrade and by `install.sh` on first deploy.

---

## Hosted Path (nexplane-deploy)

For hosted customers, the `upgrade_instance` executor in `nexplane-deploy` is extended to implement the same six-phase sequence:

- Phases 1–4 run via SSM against the managed EC2 instance
- Phase 5: ops executor writes `cutover_pending` to the sentinel file via SSM, then polls sentinel for `cutover_complete` or `rollback_complete` (timeout: 3 minutes)
- Phase 6: confirmed by polling the customer instance's `/health` endpoint and reading the version header

**Approval:** Ops creates the CR; customers see it in their audit trail with `initiated_by: nexplane-ops`. Enterprise customers can require an explicit approval gate before ops executes — configured per customer in `client-registry/<client_id>.yaml`.

**On rollback:** Ops executor reads `rollback_complete` from sentinel, marks CR `failed`, updates `client-registry/<client_id>.yaml` to retain previous version, pages on-call.

### Shared guarantees across both paths

| | Self-hosted | Hosted |
|---|---|---|
| CR in audit trail | ✓ | ✓ |
| DB snapshot before cutover | ✓ | ✓ |
| Watchdog recovery | ✓ | ✓ |
| Operator choice on verify failure | ✓ | ✓ (ops + customer) |
| Version check banner | ✓ | ✗ suppressed |
| Rollback to exact previous version | ✓ | ✓ |

---

## Files Affected

### Core platform (nexplane)

| File | Change |
|------|--------|
| `backend/app/change_type_definitions/platform_upgrade.json` | New CR type definition |
| `backend/app/connectors/executors/platform/upgrade.py` | New executor (phases 1–4) |
| `backend/app/services/version_poller.py` | New background polling service |
| `backend/app/routers/version.py` | New `GET /version` and `GET /version/check` endpoints |
| `backend/app/models/change_request.py` | Add `platform_upgrade` to ChangeType enum |
| `docker-compose.yml` | Add `nexplane-watchdog` service; add `nexplane-data` host volume |
| `watchdog/main.py` | New watchdog container entrypoint |
| `watchdog/Dockerfile` | Minimal Python image |
| `frontend/src/components/UpdateBanner.tsx` | New admin-only update notification banner |
| `frontend/src/components/DegradedModeBanner.tsx` | New upgrade failure UI |
| `install.sh` | Add `--recover` mode |
| `backend/tests/test_platform_upgrade.py` | Unit tests for executor phases and sentinel logic |

### nexplane-deploy

| File | Change |
|------|--------|
| `executors/upgrade_instance/execute.py` | Extend to six-phase sequence with sentinel handoff |
| `catalog/commercial.json` | Add `platform_upgrade` action definition |

---

## Open Dependencies

- **Release pipeline** — mechanism by which a tagged commit on `master` produces a new entry in `releases.nexplane.ai/latest.json` and a published Docker image. To be designed separately before this feature ships.
- **`releases.nexplane.ai` hosting** — static JSON endpoint; needs a domain and CDN (S3 + CloudFront is the natural fit given existing AWS usage).
- **`NEXPLANE_VERSION` stamping in install.sh** — install script needs to write the installed version to `.env` so the version poller knows what's running.
