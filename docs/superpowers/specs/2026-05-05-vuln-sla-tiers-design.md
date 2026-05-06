# Vulnerability Remediation — SLA Tiers & Auto-Escalation Design

**Date:** 2026-05-05  
**Status:** Approved for implementation

---

## Goals

1. Make SLA hours configurable per org per severity (currently hardcoded at critical: 72h, high: 168h, medium: 720h).
2. Run an hourly scheduler job that marks breaches and escalates findings that have been overdue for too long.
3. Surface SLA status, countdown timers, and configuration directly in the Vuln Remediation UI.

---

## Current State

- `RemediationSLA` model: has `breached`, `escalated_at`, `breach_notified_at`, `due_at`, `sla_hours` — all fields exist, none populated by any scheduler.
- `SLA_HOURS` dict: hardcoded in `backend/app/models/vulnerability.py`, used only at finding-ingestion time.
- `/api/v1/vulnerability/sla/dashboard` endpoint: returns counts (total/breached/due-soon) per severity. Exists but not shown in UI.
- `FindingQueue` component: shows `sla_due_at` and `sla_breached` columns with an `SLABadge`.
- APScheduler: already running for scheduled ingest (`IngestService`). Same mechanism used here.

---

## Architecture

Three contained additions:

```
backend/app/routers/vulnerability.py   ← 2 new endpoints: GET/PUT /sla/config
backend/app/models/org_settings.py    ← new sla_config JSON column on OrganizationSettings
backend/app/compliance/drift.py       ← new mark_sla_breaches_and_escalate() job
backend/app/main.py                   ← register new scheduler job (hourly)
frontend/src/pages/VulnerabilityRemediation.tsx  ← new SLA tab
```

No new tables. No new models. No email/webhook in this iteration.

---

## 1. Configurable SLA Hours

### Backend model change

Add `sla_config` JSON column to `OrganizationSettings`:

```python
sla_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
```

Default (when `null`): fall back to `SLA_HOURS` constants.  
Format: `{"critical": 24, "high": 72, "medium": 336}` (hours per severity).

### Migration

`030_add_sla_config_to_org_settings.py` — adds `sla_config` JSON column to `org_settings`.

### New endpoints in `vulnerability.py`

**`GET /api/v1/vulnerability/sla/config`**  
Returns the org's current SLA config (merged with defaults for any missing severity):
```json
{"critical": 72, "high": 168, "medium": 720}
```

**`PUT /api/v1/vulnerability/sla/config`**  
Body: `{"critical": 24, "high": 72, "medium": 336}`  
Validates: each value must be a positive integer ≥ 1.  
Saves to `org_settings.sla_config`. Returns the saved config.

### Ingestion change

In `_ingest_findings_background`, replace:
```python
sla_hours = SLA_HOURS.get(f_in.severity)
```
with a DB lookup of `org_settings.sla_config` (or fall back to `SLA_HOURS`).

---

## 2. Auto-Escalation Scheduler Job

### New function in `backend/app/compliance/drift.py`

```python
async def mark_sla_breaches_and_escalate(db: AsyncSession, org_id: uuid.UUID) -> dict:
    """
    1. Mark any overdue SLAs as breached (breached=True).
    2. Escalate findings that have been breached past their escalation threshold.
    
    Escalation thresholds (time after breach before escalated_at is set):
        critical: 4 hours
        high: 24 hours
        medium: 72 hours
    
    Returns counts: {"newly_breached": N, "newly_escalated": N}
    """
```

Logic:
- Query all `RemediationSLA` where `due_at < now` and `breached == False` → set `breached = True`.
- Query all `RemediationSLA` where `breached == True` and `escalated_at IS NULL`:
  - Compute hours since breach: `now - due_at`
  - If hours > escalation threshold for the severity → set `escalated_at = now`.

Escalation thresholds (hardcoded for now, same pattern as SLA_HOURS):
```python
ESCALATION_HOURS = {"critical": 4, "high": 24, "medium": 72}
```

### Scheduler registration in `backend/app/main.py`

Add alongside the existing ingest scheduler:
```python
scheduler.add_job(
    _run_sla_job,
    "interval",
    hours=1,
    id="sla_breach_escalation",
    replace_existing=True,
)
```

Where `_run_sla_job` iterates all organizations and calls `mark_sla_breaches_and_escalate`.

### Schema addition

Add `escalated` field to `FindingRead`:
```python
sla_escalated: Optional[bool] = None  # True when escalated_at is set
```

Update the finding list endpoint to join and include `escalated_at IS NOT NULL` as `sla_escalated`.

---

## 3. SLA Tab in Vuln Remediation UI

### New tab: "SLA"

Added to the existing tab bar in `VulnerabilityRemediation.tsx` alongside "Findings" and "Policies".

### SLA tab content — three sections:

**Section A: Summary cards**  
Pull from `/api/v1/vulnerability/sla/dashboard`. Three cards (Critical / High / Medium), each showing:
- Total SLAs
- Breached count (red if > 0)
- Due within 24h (orange)
- Escalated count (derived from findings list filtered by `sla_escalated=true`)

**Section B: Overdue findings list**  
Query `GET /api/v1/vulnerability/findings?overdue=true`, sorted by `sla_due_at` ascending (most-overdue first).  
Columns: Severity badge | CVE/Title | Asset | Time overdue | Status badge (ESCALATED 🔴 vs OVERDUE 🟠)  
Each row has a "Remediate" button that links to create a CR for that finding.

**Section C: SLA Configuration panel**  
Three numeric inputs: Critical (hours), High (hours), Medium (hours).  
Shows current values from `GET /api/v1/vulnerability/sla/config`.  
Save button calls `PUT /api/v1/vulnerability/sla/config`.  
Note: "Changes apply to new findings. Existing SLA timers are not retroactively adjusted."

### SLA badge update in `FindingQueue`

Update `SLABadge` to handle three states:
- `sla_escalated=true` → red badge "ESCALATED" with 🔴
- `sla_breached=true` but not escalated → orange "OVERDUE"
- Approaching (within 24h) → yellow with countdown
- On track → grey with due date

---

## Files

**Modify:**
- `backend/app/models/org_settings.py` — add `sla_config: Mapped[Optional[dict]]`
- `backend/app/compliance/drift.py` — add `mark_sla_breaches_and_escalate()`
- `backend/app/routers/vulnerability.py` — add `/sla/config` GET/PUT, update ingestion, update finding list schema
- `backend/app/schemas/vulnerability.py` — add `sla_escalated` to `FindingRead`
- `backend/app/main.py` — register hourly SLA job
- `frontend/src/pages/VulnerabilityRemediation.tsx` — add SLA tab
- `frontend/src/components/FindingQueue.tsx` — update SLABadge for escalated state

**Create:**
- `backend/alembic/versions/030_add_sla_config_to_org_settings.py`

---

## Out of Scope

- Email/webhook notifications on breach or escalation (future iteration)
- Retroactive SLA recalculation when config changes
- Per-asset-criticality SLA multipliers
- Auto-execute remediation on breach (requires safety review design)
- Threat intel feed integration (separate feature)
