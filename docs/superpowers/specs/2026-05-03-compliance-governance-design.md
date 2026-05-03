# Compliance & Governance — Design Spec

**Date:** 2026-05-03
**Status:** Approved
**Scope:** CIS Benchmark Campaigns, Drift Detection + Auto-Remediation, Audit Evidence Collection, and Change Freeze Enforcement across the Nexplane fleet.

---

## Background

Nexplane already collects host configuration data via `ossecurity` and `winharden` agent commands, manages change requests through a plan/approve/execute lifecycle, and runs scheduled ingest jobs via APScheduler. This spec adds a compliance layer on top: structured CIS auditing, baseline drift detection, audit-ready evidence bundles, and change freeze windows — enabling teams to demonstrate continuous control compliance for SOC2, PCI-DSS, and ISO 27001.

---

## Design Decisions

- **New `agent/commands/compliance/` package:** Compliance commands (`audit_cis_compliance`, `collect_evidence`) live in their own package rather than extending `ossecurity/`. The separation makes the audit surface explicit and avoids coupling remediation commands to evidence-collection logic.
- **CIS audit results stored in `asset_metadata`:** Per-host pass/fail scores are written back into the existing `asset_metadata` JSON column under a `cis_compliance` key. This gives trending for free via the existing metadata query paths without a new table.
- **`ComplianceBaseline` stored as versioned JSON:** Baselines are rows in a new `compliance_baselines` table with a `config JSONB` column and an integer `version` counter. No separate version table — the version field on the row is bumped on each update, and the previous config is preserved in a `history JSONB[]` column for audit rollback.
- **Drift remediation via DRAFT change requests:** Auto-remediation never silently applies changes. Detected drift always produces a DRAFT `enforce_cis_benchmark` change request. A separate `auto_execute` boolean policy field on `ComplianceBaseline` governs whether the scheduler also immediately approves and executes it.
- **Change freeze enforced as FastAPI middleware:** A `ChangeFreezeMiddleware` dependency injected into the change-request approval and execution endpoints checks the `change_freeze_windows` table. This keeps freeze logic in one place rather than duplicated across route handlers.
- **Evidence bundles built server-side as ZIP:** The backend aggregates agent job results, change logs, and config exports into a ZIP using Python's `zipfile` module. The ZIP is streamed as a `StreamingResponse` — no temp files persisted on the server.

---

## Section 1: CIS Benchmark Campaigns

### 1.1 Agent Command — `audit_cis_compliance`

**New file:** `agent/commands/compliance/audit_cis.go`

The command accepts a JSON payload and returns a structured per-control pass/fail report.

**Input payload:**

```json
{
  "level": 2,
  "os_family": "rhel"
}
```

Supported `os_family` values: `rhel`, `debian`, `ubuntu`.

**Output payload:**

```json
{
  "level": 2,
  "os_family": "rhel",
  "score": 0.74,
  "controls": [
    {
      "id": "1.1.1.1",
      "title": "Disable mounting of cramfs filesystems",
      "section": "filesystem",
      "status": "pass",
      "expected": "install /bin/true",
      "actual": "install /bin/true"
    },
    {
      "id": "5.2.4",
      "title": "Ensure SSH Protocol is set to 2",
      "section": "ssh",
      "status": "fail",
      "expected": "Protocol 2",
      "actual": "not set"
    }
  ],
  "collected_at": "2026-05-03T08:00:00Z"
}
```

`score` = passing controls / total controls (0.0–1.0).

**Controls checked per section:**

| Section | Controls |
|---------|----------|
| `filesystem` | cramfs/squashfs/udf disabled, `/tmp` separate partition, `noexec`/`nosuid`/`nodev` mount options |
| `sysctl` | `net.ipv4.ip_forward=0`, `net.ipv4.conf.all.send_redirects=0`, `kernel.randomize_va_space=2`, `fs.suid_dumpable=0` |
| `ssh` | Protocol 2, `PermitRootLogin no`, `PasswordAuthentication no`, `MaxAuthTries ≤ 4`, `ClientAliveInterval ≤ 300` |
| `pam` | `pam_pwquality` enforced, password history ≥ 5, account lockout after 5 failures |
| `auditd` | Service running, rules for privileged commands, `-w /etc/passwd -p wa`, `-w /etc/sudoers -p wa` |
| `selinux` | `SELINUX=enforcing` in `/etc/selinux/config` (rhel only) |
| `apparmor` | `aa-status` shows enforced profiles (debian/ubuntu only) |

**Go interface:**

```go
package compliance

type AuditCISInput struct {
    Level    int    `json:"level"`    // 1 or 2
    OSFamily string `json:"os_family"` // rhel | debian | ubuntu
}

type ControlResult struct {
    ID       string `json:"id"`
    Title    string `json:"title"`
    Section  string `json:"section"`
    Status   string `json:"status"`   // "pass" | "fail" | "skip"
    Expected string `json:"expected"`
    Actual   string `json:"actual"`
}

type AuditCISOutput struct {
    Level       int             `json:"level"`
    OSFamily    string          `json:"os_family"`
    Score       float64         `json:"score"`
    Controls    []ControlResult `json:"controls"`
    CollectedAt time.Time       `json:"collected_at"`
}

func AuditCISCompliance(ctx context.Context, input AuditCISInput) (AuditCISOutput, error)
```

Level 1 runs only the `filesystem`, `ssh`, and `sysctl` sections. Level 2 adds `pam`, `auditd`, and the applicable MAC section (`selinux` or `apparmor`).

### 1.2 New Change Type — `enforce_cis_benchmark`

**Backend change:** `backend/app/change_requests/types.py` — add `"enforce_cis_benchmark"` to the allowed change type enum.

**Parameter schema** (stored in `change_requests.parameters JSONB`):

```json
{
  "level": 2,
  "os_family": "rhel",
  "asset_ids": ["uuid-1", "uuid-2"],
  "dry_run": false
}
```

**Execution flow** (`backend/app/change_requests/executors/cis_benchmark.py`):

1. Dispatch `audit_cis_compliance` job to each target asset via the existing agent job mechanism.
2. Collect results; record `before_score` per asset.
3. For each failing control, map it to the appropriate existing ossecurity command:
   - `sysctl` failures → `set_sysctl` command
   - `ssh` failures → `configure_sshd` command
   - `selinux` failures → `set_selinux_mode` command
   - `auditd` failures → `configure_auditd` command
   - `pam` failures → `configure_pam` command
4. Dispatch remediation jobs sequentially per asset.
5. Re-run `audit_cis_compliance` to collect `after_score`.
6. Write result to `change_requests.result JSONB`:

```json
{
  "assets": [
    {
      "asset_id": "uuid-1",
      "hostname": "prod-web-01",
      "before_score": 0.61,
      "after_score": 0.94,
      "remediated_controls": ["5.2.4", "3.1.1"],
      "skipped_controls": ["1.1.4"]
    }
  ]
}
```

### 1.3 Frontend — Compliance Tab

**New file:** `frontend/src/pages/Compliance.tsx`

Route: `/compliance`

**Components:**

- `CISScoreCard` — per-host score badge (color-coded: red < 0.6, yellow < 0.8, green ≥ 0.8) with hostname, last-audited timestamp, and score percentage.
- `ControlBreakdownTable` — expandable table of controls for a selected host: ID, section, title, status icon (pass/fail/skip), expected vs. actual values.
- `RunCISAuditButton` — opens a modal to select level (1/2), OS family, and target assets; submits `POST /change-requests` with type `enforce_cis_benchmark`.

**Data source:** `GET /assets/{id}/metadata` filtered to `cis_compliance` key — no new endpoint needed for display.

---

## Section 2: Drift Detection + Auto-Remediation

### 2.1 Data Model — `ComplianceBaseline`

**New migration:** `backend/alembic/versions/XXXX_add_compliance_baselines.py`

```sql
CREATE TABLE compliance_baselines (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    description TEXT,
    scope_type  TEXT NOT NULL CHECK (scope_type IN ('asset', 'tag')),
    scope_value TEXT NOT NULL,          -- asset UUID or tag string
    cis_level   INTEGER NOT NULL CHECK (cis_level IN (1, 2)),
    os_family   TEXT NOT NULL,
    config      JSONB NOT NULL,         -- expected control states
    history     JSONB[] NOT NULL DEFAULT '{}',  -- previous config snapshots
    version     INTEGER NOT NULL DEFAULT 1,
    auto_execute BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_compliance_baselines_scope ON compliance_baselines (scope_type, scope_value);
```

`config` structure:

```json
{
  "cis_level": 2,
  "os_family": "rhel",
  "overrides": {
    "5.2.4": "skip",
    "net.ipv4.ip_forward": "1"
  }
}
```

`overrides` allows per-baseline exceptions (e.g. a host that legitimately forwards IP traffic). Controls listed in `overrides` with value `"skip"` are excluded from drift comparison.

**SQLAlchemy model:** `backend/app/compliance/models.py`

```python
class ComplianceBaseline(Base):
    __tablename__ = "compliance_baselines"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name         = Column(Text, nullable=False)
    description  = Column(Text)
    scope_type   = Column(Text, nullable=False)   # "asset" | "tag"
    scope_value  = Column(Text, nullable=False)
    cis_level    = Column(Integer, nullable=False)
    os_family    = Column(Text, nullable=False)
    config       = Column(JSONB, nullable=False)
    history      = Column(ARRAY(JSONB), nullable=False, default=list)
    version      = Column(Integer, nullable=False, default=1)
    auto_execute = Column(Boolean, nullable=False, default=False)
    created_at   = Column(TIMESTAMPTZ, nullable=False, default=func.now())
    updated_at   = Column(TIMESTAMPTZ, nullable=False, default=func.now(), onupdate=func.now())
```

### 2.2 Scheduled Drift Detection Job

**Modified file:** `backend/app/scheduler.py`

Add a weekly job:

```python
scheduler.add_job(
    run_drift_detection,
    trigger=CronTrigger(day_of_week="sun", hour=2, minute=0),
    id="drift_detection_weekly",
    replace_existing=True,
)
```

**New file:** `backend/app/compliance/drift.py`

```python
async def run_drift_detection(db: AsyncSession) -> None:
    """
    For each ComplianceBaseline, fetch matching assets, dispatch
    audit_cis_compliance, compare to baseline config, and create
    DRAFT change requests for drifted hosts.
    """
    baselines = await db.execute(select(ComplianceBaseline))
    for baseline in baselines.scalars():
        assets = await resolve_scope(db, baseline.scope_type, baseline.scope_value)
        for asset in assets:
            job_result = await dispatch_agent_job(
                asset_id=asset.id,
                command="audit_cis_compliance",
                parameters={"level": baseline.cis_level, "os_family": baseline.os_family},
            )
            drifted = detect_drift(job_result, baseline)
            if drifted:
                await create_drift_change_request(db, asset, baseline, drifted, job_result)
                if baseline.auto_execute:
                    await approve_and_execute_change_request(db, change_request_id)

async def detect_drift(
    audit_result: dict, baseline: ComplianceBaseline
) -> list[str]:
    """Return list of failing control IDs not marked skip in baseline overrides."""
    overrides = baseline.config.get("overrides", {})
    return [
        c["id"]
        for c in audit_result["controls"]
        if c["status"] == "fail" and overrides.get(c["id"]) != "skip"
    ]
```

### 2.3 Frontend — Drift Alerts Widget

**Modified file:** `frontend/src/pages/Dashboard.tsx`

Add `DriftAlertsWidget` component:

```typescript
// Polls GET /compliance/drift-alerts (new endpoint, see Section 2.4)
// Shows a summary card: "3 hosts drifted from baseline in the last 7 days"
// Links to /compliance with drift filter pre-applied
```

Widget shows:
- Count of assets with open drift-detected DRAFT change requests.
- Most recent drift event: asset hostname, baseline name, drifted control count.
- "Review Drift" button linking to `/compliance?filter=drift`.

---

## Section 3: Audit Evidence Collection

### 3.1 Agent Command — `collect_evidence`

**New file:** `agent/commands/compliance/collect_evidence.go`

**Input payload:**

```json
{
  "framework": "soc2",
  "control_id": "CC6.1",
  "evidence_types": ["config_files", "command_outputs", "change_logs"]
}
```

Supported `framework` values: `soc2`, `pci`, `iso27001`.

**Evidence type handlers:**

| `evidence_type` | What the agent collects |
|-----------------|-------------------------|
| `config_files` | `/etc/ssh/sshd_config`, `/etc/pam.d/system-auth`, `/etc/audit/audit.rules`, `/etc/selinux/config` |
| `command_outputs` | `sestatus`, `auditctl -l`, `sysctl -a`, `ss -tlnp`, `last -n 20`, `passwd -S` for privileged users |
| `change_logs` | Fetched from Nexplane API: change requests touching this asset in the last 90 days |

**Output payload:**

```json
{
  "framework": "soc2",
  "control_id": "CC6.1",
  "asset_id": "uuid-1",
  "hostname": "prod-web-01",
  "collected_at": "2026-05-03T10:00:00Z",
  "artifacts": [
    {
      "type": "config_file",
      "path": "/etc/ssh/sshd_config",
      "content": "..."
    },
    {
      "type": "command_output",
      "command": "sestatus",
      "output": "SELinuxfs mount: /sys/fs/selinux\n..."
    }
  ]
}
```

### 3.2 API Endpoint — Evidence Collection

**New file:** `backend/app/compliance/routes.py`

```python
@router.post("/compliance/evidence-collection")
async def collect_evidence(
    body: EvidenceCollectionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("auditor")),
) -> StreamingResponse:
    """
    Dispatch collect_evidence to each asset_id, aggregate results,
    package as ZIP, stream to caller.
    """
```

**Request schema:**

```python
class EvidenceCollectionRequest(BaseModel):
    framework:   Literal["soc2", "pci", "iso27001"]
    control_id:  str
    asset_ids:   list[UUID]
    evidence_types: list[str] = ["config_files", "command_outputs", "change_logs"]
```

**ZIP structure:**

```
evidence-soc2-CC6.1-2026-05-03.zip
├── manifest.json          # framework, control_id, asset_ids, collected_at, Nexplane version
├── prod-web-01/
│   ├── sshd_config
│   ├── audit.rules
│   ├── sestatus.txt
│   ├── sysctl.txt
│   └── change_log.json    # change requests from Nexplane DB for this asset
└── prod-web-02/
    └── ...
```

`manifest.json`:

```json
{
  "framework": "soc2",
  "control_id": "CC6.1",
  "collected_at": "2026-05-03T10:00:00Z",
  "nexplane_version": "0.1.0",
  "assets": ["prod-web-01", "prod-web-02"],
  "evidence_types": ["config_files", "command_outputs", "change_logs"]
}
```

**Response:** `StreamingResponse` with `Content-Type: application/zip` and `Content-Disposition: attachment; filename="evidence-{framework}-{control_id}-{date}.zip"`.

### 3.3 New API Endpoints (Compliance Router)

**New file:** `backend/app/compliance/routes.py` (extended):

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/compliance/baselines` | List all compliance baselines |
| `POST` | `/compliance/baselines` | Create a new baseline |
| `GET` | `/compliance/baselines/{id}` | Get baseline detail with version history |
| `PUT` | `/compliance/baselines/{id}` | Update baseline (bumps version, saves old config to history) |
| `DELETE` | `/compliance/baselines/{id}` | Delete baseline |
| `GET` | `/compliance/drift-alerts` | List assets with active drift DRAFT change requests |
| `POST` | `/compliance/evidence-collection` | Collect and download evidence bundle |

---

## Section 4: Change Freeze Enforcement

### 4.1 Data Model — `ChangeFreezeWindow`

**New migration:** `backend/alembic/versions/XXXX_add_change_freeze_windows.py`

```sql
CREATE TABLE change_freeze_windows (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reason               TEXT NOT NULL,
    start_at             TIMESTAMPTZ NOT NULL,
    end_at               TIMESTAMPTZ NOT NULL,
    emergency_bypass_role TEXT NOT NULL DEFAULT 'emergency_bypass',
    created_by           UUID NOT NULL REFERENCES users(id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_at > start_at)
);

CREATE INDEX idx_freeze_windows_active ON change_freeze_windows (start_at, end_at);
```

**SQLAlchemy model:** `backend/app/compliance/models.py` (added to same file as `ComplianceBaseline`)

```python
class ChangeFreezeWindow(Base):
    __tablename__ = "change_freeze_windows"

    id                    = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    reason                = Column(Text, nullable=False)
    start_at              = Column(TIMESTAMPTZ, nullable=False)
    end_at                = Column(TIMESTAMPTZ, nullable=False)
    emergency_bypass_role = Column(Text, nullable=False, default="emergency_bypass")
    created_by            = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at            = Column(TIMESTAMPTZ, nullable=False, default=func.now())
```

### 4.2 Freeze Enforcement — FastAPI Dependency

**New file:** `backend/app/compliance/freeze.py`

```python
async def require_no_active_freeze(
    justification: str | None = Header(None, alias="X-Emergency-Justification"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """
    Raise HTTP 423 if a freeze window is active and the caller does not have
    the emergency_bypass role. Requires X-Emergency-Justification header when
    bypassing. Logs bypass events to the audit log.
    """
    now = datetime.utcnow()
    freeze = await db.execute(
        select(ChangeFreezeWindow).where(
            ChangeFreezeWindow.start_at <= now,
            ChangeFreezeWindow.end_at >= now,
        ).limit(1)
    )
    active = freeze.scalar_one_or_none()
    if active is None:
        return

    if active.emergency_bypass_role not in current_user.roles:
        raise HTTPException(
            status_code=423,
            detail={
                "error": "change_freeze_active",
                "message": f"Change freeze active until {active.end_at.isoformat()}",
                "reason": active.reason,
                "freeze_id": str(active.id),
            },
        )

    # User has bypass role — require justification
    if not justification:
        raise HTTPException(
            status_code=422,
            detail="X-Emergency-Justification header required to bypass change freeze",
        )

    await log_freeze_bypass(db, current_user, active, justification)
```

**Inject into change request routes** (`backend/app/change_requests/routes.py`):

```python
@router.post("/change-requests/{id}/approve", dependencies=[Depends(require_no_active_freeze)])
@router.post("/change-requests/{id}/execute", dependencies=[Depends(require_no_active_freeze)])
```

### 4.3 Freeze Management Endpoints

Added to `backend/app/compliance/routes.py`:

| Method | Path | Role Required | Description |
|--------|------|---------------|-------------|
| `GET` | `/compliance/freeze-windows` | `viewer` | List all freeze windows (past + future) |
| `POST` | `/compliance/freeze-windows` | `change_manager` | Create a freeze window |
| `DELETE` | `/compliance/freeze-windows/{id}` | `change_manager` | Cancel a freeze window |
| `GET` | `/compliance/freeze-windows/active` | `viewer` | Return current active freeze (or 404) |

### 4.4 Frontend — Freeze Banner

**Modified file:** `frontend/src/App.tsx`

```typescript
// Poll GET /compliance/freeze-windows/active every 60 seconds
const { data: activeFreeze } = useQuery({
  queryKey: ["active-freeze"],
  queryFn: () => fetch("/compliance/freeze-windows/active").then(r => r.ok ? r.json() : null).catch(() => null),
  refetchInterval: 60_000,
});

// Render above main layout when activeFreeze is non-null:
{activeFreeze && (
  <FreezeAlert reason={activeFreeze.reason} endAt={activeFreeze.end_at} />
)}
```

**New file:** `frontend/src/components/FreezeAlert.tsx`

- Sticky amber/red banner across the top of the app.
- Text: `"Change freeze active until {end_at} — {reason}"`.
- Dismissible per session (localStorage flag), but re-appears if the page is reloaded or a new freeze window is detected.
- Does not block navigation — only inform. The API blocks the actual action.

---

## Section 5: CIS Audit Results Stored in Asset Metadata

After each `audit_cis_compliance` run (whether triggered manually, via change request, or by the drift detection scheduler), the backend writes results back to `assets.metadata` under the `cis_compliance` key.

**Modified file:** `backend/app/compliance/drift.py` (and the `enforce_cis_benchmark` executor):

```python
async def write_cis_score_to_metadata(
    db: AsyncSession, asset_id: UUID, audit_result: dict
) -> None:
    asset = await db.get(Asset, asset_id)
    metadata = asset.metadata or {}
    history = metadata.get("cis_compliance", {}).get("history", [])
    history.append({
        "score": audit_result["score"],
        "level": audit_result["level"],
        "collected_at": audit_result["collected_at"],
    })
    metadata["cis_compliance"] = {
        "latest": audit_result,
        "history": history[-30:],  # keep last 30 snapshots
    }
    asset.metadata = metadata
    await db.commit()
```

This enables the Compliance tab to render score trend charts from existing asset metadata queries without an additional endpoint.

---

## Files Changed

| File | Change |
|------|--------|
| `agent/commands/compliance/audit_cis.go` | New package — `AuditCISCompliance` command, per-section control checks |
| `agent/commands/compliance/collect_evidence.go` | New — `CollectEvidence` command, config file and command output collection |
| `backend/alembic/versions/XXXX_add_compliance_baselines.py` | New migration — `compliance_baselines` table |
| `backend/alembic/versions/XXXX_add_change_freeze_windows.py` | New migration — `change_freeze_windows` table |
| `backend/app/compliance/models.py` | New — `ComplianceBaseline`, `ChangeFreezeWindow` SQLAlchemy models |
| `backend/app/compliance/routes.py` | New — compliance router: baselines CRUD, drift alerts, evidence collection, freeze windows |
| `backend/app/compliance/drift.py` | New — `run_drift_detection`, `detect_drift`, `write_cis_score_to_metadata` |
| `backend/app/compliance/freeze.py` | New — `require_no_active_freeze` FastAPI dependency |
| `backend/app/change_requests/types.py` | Add `"enforce_cis_benchmark"` to change type enum |
| `backend/app/change_requests/executors/cis_benchmark.py` | New — audit → remediate → re-audit flow, before/after score result |
| `backend/app/change_requests/routes.py` | Inject `require_no_active_freeze` dependency on approve + execute routes |
| `backend/app/scheduler.py` | Add weekly `run_drift_detection` cron job |
| `backend/app/main.py` | Register compliance router |
| `frontend/src/pages/Compliance.tsx` | New — CIS score cards, control breakdown table, run-audit modal |
| `frontend/src/pages/Dashboard.tsx` | Add `DriftAlertsWidget` |
| `frontend/src/components/FreezeAlert.tsx` | New — sticky freeze banner component |
| `frontend/src/App.tsx` | Poll active freeze, render `FreezeAlert` when active |
| `frontend/src/App.tsx` | Add `/compliance` route |
