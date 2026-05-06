# Vuln SLA Tiers & Auto-Escalation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable SLA hours per org, auto-escalation in the existing breach job, and a new SLA tab in the vuln remediation UI showing overdue findings and a configuration panel.

**Architecture:** `sla_config` JSON column on `OrganizationSettings`; escalation logic added to the existing `enforce_slas()` job (runs every 15 min via APScheduler); two new REST endpoints `/sla/config` GET+PUT; `sla_escalated` field added to `FindingRead`; new SLA tab in the frontend using the existing `/sla/dashboard` endpoint plus a findings list filtered to overdue findings, sorted by most-overdue first.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, APScheduler, React, TanStack Query, Tailwind CSS

---

## Files

**Create:**
- `backend/alembic/versions/030_add_sla_config_to_org_settings.py`

**Modify:**
- `backend/app/models/org_settings.py` — add `sla_config` JSON column
- `backend/app/jobs/sla_enforcement.py` — add escalation logic
- `backend/app/routers/vulnerability.py` — add `/sla/config` GET+PUT; update ingestion to use org config; add `sla_escalated` to finding list response
- `backend/app/schemas/vulnerability.py` — add `sla_escalated: Optional[bool]` to `FindingRead`; new `SLAConfig` schema
- `frontend/src/pages/VulnerabilityRemediation.tsx` — add SLA tab
- `frontend/src/components/FindingQueue.tsx` — update `SLABadge` for escalated state

---

### Task 1: Add `sla_config` to OrgSettings + migration

**Files:**
- Modify: `backend/app/models/org_settings.py`
- Create: `backend/alembic/versions/030_add_sla_config_to_org_settings.py`

- [ ] **Step 1: Add `sla_config` column to `OrganizationSettings`**

In `backend/app/models/org_settings.py`, add the import and column:

```python
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import Text, DateTime, func, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class OrganizationSettings(Base):
    __tablename__ = "organization_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, unique=True
    )
    anthropic_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_providers_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    sla_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 2: Create migration 030**

Create `backend/alembic/versions/030_add_sla_config_to_org_settings.py`:

```python
"""add sla_config to org_settings

Revision ID: 030
Revises: 029
Create Date: 2026-05-05
"""
from alembic import op
import sqlalchemy as sa

revision = '030'
down_revision = '029'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organization_settings', sa.Column('sla_config', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('organization_settings', 'sla_config')
```

- [ ] **Step 3: Run migration**

```bash
docker exec nexplane-backend-1 alembic upgrade head 2>&1 | tail -5
```

Expected: `Running upgrade 029 -> 030, add sla_config to org_settings`

- [ ] **Step 4: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/org_settings.py backend/alembic/versions/030_add_sla_config_to_org_settings.py
git commit -m "feat(sla): add sla_config JSON column to OrganizationSettings + migration 030"
```

---

### Task 2: Add escalation to `sla_enforcement.py`

**Files:**
- Modify: `backend/app/jobs/sla_enforcement.py`

The existing job already marks breaches. Add escalation: if a finding has been breached for longer than the escalation threshold, set `escalated_at`.

- [ ] **Step 1: Replace `enforce_slas` with version that adds escalation**

Replace the entire contents of `backend/app/jobs/sla_enforcement.py` with:

```python
import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vulnerability import VulnerabilityFinding, RemediationPolicy, RemediationSLA

logger = logging.getLogger(__name__)

# Hours after breach before escalating (per severity)
ESCALATION_HOURS: dict[str, int] = {
    "critical": 4,
    "high": 24,
    "medium": 72,
}


async def enforce_slas(db: AsyncSession) -> None:
    """
    1. Find RemediationSLA rows where due_at < now() and breached = False → mark as breached.
    2. Find breached SLAs where escalated_at is None and breach has exceeded threshold → escalate.
    """
    from app.services.vuln_remediation_engine import generate_change_request_for_finding, match_policy

    now = datetime.now(timezone.utc)

    # Step 1: Mark new breaches
    result = await db.execute(
        select(RemediationSLA).where(
            RemediationSLA.due_at < now,
            RemediationSLA.breached == False,
        )
    )
    overdue_slas = result.scalars().all()

    newly_breached = 0
    for sla in overdue_slas:
        sla.breached = True
        sla.breach_notified_at = now
        newly_breached += 1

        finding = await db.get(VulnerabilityFinding, sla.finding_id)
        if not finding or finding.status != "open" or finding.change_request_id:
            continue

        policies_result = await db.execute(
            select(RemediationPolicy).where(
                RemediationPolicy.organization_id == sla.organization_id,
                RemediationPolicy.enabled == True,
            ).order_by(RemediationPolicy.priority.desc())
        )
        policies = policies_result.scalars().all()
        matched = match_policy(finding, list(policies))

        try:
            await generate_change_request_for_finding(finding, matched, db)
            logger.info(f"SLA breach: auto-generated CR for finding {finding.id}")
        except Exception as e:
            logger.error(f"SLA breach CR generation failed for finding {finding.id}: {e}")

    # Step 2: Escalate findings that have been breached past their threshold
    breached_result = await db.execute(
        select(RemediationSLA).where(
            RemediationSLA.breached == True,
            RemediationSLA.escalated_at == None,  # noqa: E711
        )
    )
    breached_slas = breached_result.scalars().all()

    newly_escalated = 0
    for sla in breached_slas:
        threshold_hours = ESCALATION_HOURS.get(sla.severity)
        if threshold_hours is None:
            continue
        # due_at is when the breach started; escalate if now > due_at + threshold
        escalate_after = sla.due_at + timedelta(hours=threshold_hours)
        if now >= escalate_after:
            sla.escalated_at = now
            newly_escalated += 1
            logger.info(
                f"SLA escalated: finding {sla.finding_id} severity={sla.severity} "
                f"breach_age={(now - sla.due_at).total_seconds() / 3600:.1f}h"
            )

    await db.commit()
    logger.info(
        f"SLA enforcement: {newly_breached} newly breached, {newly_escalated} escalated"
    )
```

- [ ] **Step 2: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/app/jobs/sla_enforcement.py
git commit -m "feat(sla): add auto-escalation to enforce_slas job — escalates after 4h/24h/72h per severity"
```

---

### Task 3: SLA config endpoints + ingestion update + schema

**Files:**
- Modify: `backend/app/schemas/vulnerability.py`
- Modify: `backend/app/routers/vulnerability.py`

- [ ] **Step 1: Add `SLAConfig` schema and `sla_escalated` to `FindingRead`**

In `backend/app/schemas/vulnerability.py`, add after the existing imports:

```python
# After existing SLASeverityStats / SLADashboardResponse classes, add:

class SLAConfig(BaseModel):
    critical: int = 72
    high: int = 168
    medium: int = 720

    class Config:
        extra = "forbid"
```

In the same file, add `sla_escalated` to `FindingRead` (after `sla_breached`):
```python
    sla_due_at: Optional[datetime] = None
    sla_breached: Optional[bool] = None
    sla_escalated: Optional[bool] = None  # True when escalated_at is set
```

Also add `SLAConfig` to any `__all__` or imports if needed (it won't be — just define it in the file).

- [ ] **Step 2: Add SLA config helper and endpoints to `vulnerability.py`**

In `backend/app/routers/vulnerability.py`, add imports at the top (after existing imports):

```python
from app.models.org_settings import OrganizationSettings
from app.schemas.vulnerability import SLAConfig  # add to the existing import line
```

Add this helper function near the top of the router (after `ALLOWED_STATUS_TRANSITIONS`):

```python
_DEFAULT_SLA_CONFIG = {"critical": 72, "high": 168, "medium": 720}


async def _get_sla_config(db: AsyncSession, org_id: uuid.UUID) -> dict:
    """Return org SLA config, falling back to defaults for any missing severity."""
    result = await db.execute(
        select(OrganizationSettings).where(OrganizationSettings.organization_id == org_id)
    )
    settings = result.scalar_one_or_none()
    if settings and settings.sla_config:
        cfg = dict(_DEFAULT_SLA_CONFIG)
        cfg.update(settings.sla_config)
        return cfg
    return dict(_DEFAULT_SLA_CONFIG)
```

Add two new endpoints (place after the `/sla/dashboard` endpoint):

```python
@router.get("/sla/config", response_model=SLAConfig)
async def get_sla_config(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the org's configurable SLA hours per severity."""
    cfg = await _get_sla_config(db, user.organization_id)
    return SLAConfig(**cfg)


@router.put("/sla/config", response_model=SLAConfig)
async def update_sla_config(
    body: SLAConfig,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update the org's SLA hours per severity. Changes apply to new findings only."""
    result = await db.execute(
        select(OrganizationSettings).where(OrganizationSettings.organization_id == user.organization_id)
    )
    settings = result.scalar_one_or_none()
    if not settings:
        settings = OrganizationSettings(organization_id=user.organization_id)
        db.add(settings)
    settings.sla_config = body.model_dump()
    await db.commit()
    return body
```

- [ ] **Step 3: Update ingestion to use org SLA config**

In `_ingest_findings_background`, replace:

```python
            # Create SLA if applicable
            sla_hours = SLA_HOURS.get(f_in.severity)
```

with:

```python
            # Create SLA if applicable — use org config if set, else default
            from app.models.org_settings import OrganizationSettings as _OrgSettings
            _org_settings_result = await db.execute(
                select(_OrgSettings).where(_OrgSettings.organization_id == payload.organization_id)
            )
            _org_settings = _org_settings_result.scalar_one_or_none()
            _sla_cfg = dict(SLA_HOURS)
            if _org_settings and _org_settings.sla_config:
                _sla_cfg.update(_org_settings.sla_config)
            sla_hours = _sla_cfg.get(f_in.severity)
```

- [ ] **Step 4: Add `sla_escalated` to finding list response**

In `list_findings`, update the `FindingRead` construction (replace the `finding_reads.append(FindingRead(...))` block):

```python
        finding_reads.append(FindingRead(
            **{k: v for k, v in f.__dict__.items() if not k.startswith("_")},
            asset_name=asset_name,
            sla_due_at=sla.due_at if sla else None,
            sla_breached=sla.breached if sla else None,
            sla_escalated=bool(sla.escalated_at) if sla else None,
        ))
```

- [ ] **Step 5: Run backend tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all tests pass.

- [ ] **Step 6: Verify new endpoints are reachable**

```bash
docker exec nexplane-backend-1 python3 -c "
import httpx
tok = httpx.post('http://localhost:8000/auth/login',json={'email':'admin@acme.example','password':'admin123'}).json()['access_token']
r = httpx.get('http://localhost:8000/api/v1/vulnerability/sla/config', headers={'Authorization': 'Bearer '+tok})
print(r.status_code, r.json())
"
```

Expected: `200 {'critical': 72, 'high': 168, 'medium': 720}`

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/vulnerability.py backend/app/routers/vulnerability.py
git commit -m "feat(sla): add /sla/config GET+PUT endpoints, org-configurable SLA hours, sla_escalated in FindingRead"
```

---

### Task 4: Update `FindingQueue` SLA badge for escalated state

**Files:**
- Modify: `frontend/src/components/FindingQueue.tsx`

- [ ] **Step 1: Add `sla_escalated` to the `Finding` interface and update `SLABadge`**

Read the current `FindingQueue.tsx`. Find the `Finding` interface and the `SLABadge` function.

Add `sla_escalated` to the `Finding` interface:
```typescript
  sla_escalated?: boolean | null;
```

Replace the `SLABadge` function with:

```typescript
function SLABadge({
  dueAt,
  breached,
  escalated,
}: {
  dueAt: string | null;
  breached: boolean | null;
  escalated?: boolean | null;
}) {
  if (escalated) {
    return (
      <span className="inline-flex items-center gap-1 text-red-700 font-semibold text-xs bg-red-50 px-1.5 py-0.5 rounded">
        🔴 ESCALATED
      </span>
    );
  }
  if (breached) {
    return (
      <span className="inline-flex items-center gap-1 text-orange-600 font-semibold text-xs bg-orange-50 px-1.5 py-0.5 rounded">
        🟠 OVERDUE
      </span>
    );
  }
  if (!dueAt) return null;
  const due = new Date(dueAt);
  const now = new Date();
  const hoursLeft = (due.getTime() - now.getTime()) / 3600000;
  if (hoursLeft < 0) {
    return (
      <span className="text-orange-600 font-semibold text-xs">OVERDUE</span>
    );
  }
  if (hoursLeft < 24) {
    return (
      <span className="text-yellow-600 text-xs font-medium">
        {Math.round(hoursLeft)}h left
      </span>
    );
  }
  return (
    <span className="text-slate-400 text-xs">
      {due.toLocaleDateString()}
    </span>
  );
}
```

Update every usage of `<SLABadge dueAt={f.sla_due_at} breached={f.sla_breached} />` to also pass `escalated`:

```typescript
<SLABadge
  dueAt={f.sla_due_at}
  breached={f.sla_breached}
  escalated={f.sla_escalated}
/>
```

- [ ] **Step 2: Verify TypeScript**

```bash
docker compose exec frontend npx tsc --noEmit 2>&1 | grep "FindingQueue" | head -5
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/FindingQueue.tsx
git commit -m "feat(sla): update SLABadge to show ESCALATED (red) / OVERDUE (orange) / countdown / on-track states"
```

---

### Task 5: Add SLA tab to `VulnerabilityRemediation.tsx`

**Files:**
- Modify: `frontend/src/pages/VulnerabilityRemediation.tsx`

- [ ] **Step 1: Add SLA tab type and imports**

At the top of `VulnerabilityRemediation.tsx`, update the `Tab` type:

```typescript
type Tab = "findings" | "policies" | "sla";
```

Add `Clock` to the lucide-react import (alongside existing icons already imported).

- [ ] **Step 2: Add SLA data fetching**

Inside the `VulnerabilityRemediation` component, add these queries (alongside existing ones):

```typescript
  const { data: slaDashboard } = useQuery({
    queryKey: ["sla-dashboard"],
    queryFn: () =>
      apiClient.get("/api/v1/vulnerability/sla/dashboard").then((r) => r.data),
    refetchInterval: tab === "sla" ? 30000 : false,
  });

  const { data: overdueFindings } = useQuery({
    queryKey: ["overdue-findings"],
    queryFn: () =>
      apiClient
        .get("/api/v1/vulnerability/findings", {
          params: { overdue: true, page_size: 50 },
        })
        .then((r) => r.data.findings),
    enabled: tab === "sla",
  });

  const { data: slaConfig, refetch: refetchSlaConfig } = useQuery({
    queryKey: ["sla-config"],
    queryFn: () =>
      apiClient.get("/api/v1/vulnerability/sla/config").then((r) => r.data),
    enabled: tab === "sla",
  });

  const [slaEdits, setSlaEdits] = React.useState<Record<string, number>>({});
  const [slaSaving, setSlaSaving] = React.useState(false);

  const saveSlaConfig = async () => {
    setSlaSaving(true);
    try {
      const payload = {
        critical: slaEdits.critical ?? slaConfig?.critical ?? 72,
        high: slaEdits.high ?? slaConfig?.high ?? 168,
        medium: slaEdits.medium ?? slaConfig?.medium ?? 720,
      };
      await apiClient.put("/api/v1/vulnerability/sla/config", payload);
      await refetchSlaConfig();
      setSlaEdits({});
    } finally {
      setSlaSaving(false);
    }
  };
```

- [ ] **Step 3: Add "SLA" to the tab list**

Find the `tabs` array definition and add the SLA tab:

```typescript
  const tabs: { id: Tab; label: string }[] = [
    { id: "findings", label: "Findings" },
    { id: "policies", label: "Remediation Policies" },
    { id: "sla", label: "SLA" },
  ];
```

- [ ] **Step 4: Add SLA tab content**

Add the SLA tab panel in the tab content area. Find where the existing tab content is rendered (the block that switches on `tab`). Add a new branch for `tab === "sla"`:

```tsx
        {tab === "sla" && (
          <div className="space-y-6">
            {/* Summary cards */}
            {slaDashboard && (
              <div className="grid grid-cols-3 gap-4">
                {(["critical", "high", "medium"] as const).map((sev) => {
                  const stats = slaDashboard[sev];
                  if (!stats) return null;
                  return (
                    <div
                      key={sev}
                      className="bg-white border border-slate-200 rounded-xl p-4"
                    >
                      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide capitalize mb-2">
                        {sev}
                      </p>
                      <div className="space-y-1 text-sm">
                        <div className="flex justify-between">
                          <span className="text-slate-600">Total</span>
                          <span className="font-medium">{stats.total}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-slate-600">Breached</span>
                          <span
                            className={
                              stats.breached > 0
                                ? "font-semibold text-red-600"
                                : "text-slate-400"
                            }
                          >
                            {stats.breached}
                          </span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-slate-600">Due soon</span>
                          <span
                            className={
                              stats.due_soon > 0
                                ? "font-medium text-yellow-600"
                                : "text-slate-400"
                            }
                          >
                            {stats.due_soon}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Overdue findings */}
            <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
              <div className="px-4 py-3 border-b border-slate-100 flex items-center gap-2">
                <Clock size={16} className="text-slate-400" />
                <h3 className="font-medium text-slate-900 text-sm">
                  Overdue Findings
                </h3>
                {overdueFindings && overdueFindings.length > 0 && (
                  <span className="text-xs text-red-600 font-semibold ml-1">
                    ({overdueFindings.length})
                  </span>
                )}
              </div>
              {!overdueFindings || overdueFindings.length === 0 ? (
                <div className="px-4 py-8 text-center text-slate-400 text-sm">
                  No overdue findings 🎉
                </div>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-slate-500 uppercase tracking-wide border-b border-slate-100">
                      <th className="px-4 py-2 font-medium">Severity</th>
                      <th className="px-4 py-2 font-medium">Finding</th>
                      <th className="px-4 py-2 font-medium">Asset</th>
                      <th className="px-4 py-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...overdueFindings]
                      .sort(
                        (a: any, b: any) =>
                          new Date(a.sla_due_at).getTime() -
                          new Date(b.sla_due_at).getTime()
                      )
                      .map((f: any) => {
                        const hoursOverdue = f.sla_due_at
                          ? Math.round(
                              (Date.now() -
                                new Date(f.sla_due_at).getTime()) /
                                3600000
                            )
                          : null;
                        return (
                          <tr
                            key={f.id}
                            className="border-t border-slate-50 hover:bg-slate-50"
                          >
                            <td className="px-4 py-2">
                              <span
                                className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
                                  f.severity === "critical"
                                    ? "bg-red-100 text-red-700"
                                    : f.severity === "high"
                                    ? "bg-orange-100 text-orange-700"
                                    : "bg-yellow-100 text-yellow-700"
                                }`}
                              >
                                {f.severity}
                              </span>
                            </td>
                            <td className="px-4 py-2 text-slate-700 max-w-xs truncate">
                              {f.cve_id
                                ? `${f.cve_id} — ${f.title}`
                                : f.title}
                            </td>
                            <td className="px-4 py-2 text-slate-500 text-xs">
                              {f.asset_name ?? "—"}
                            </td>
                            <td className="px-4 py-2">
                              {f.sla_escalated ? (
                                <span className="text-xs font-semibold text-red-700 bg-red-50 px-1.5 py-0.5 rounded">
                                  🔴 ESCALATED{" "}
                                  {hoursOverdue !== null
                                    ? `(${hoursOverdue}h overdue)`
                                    : ""}
                                </span>
                              ) : (
                                <span className="text-xs font-medium text-orange-600">
                                  🟠 OVERDUE{" "}
                                  {hoursOverdue !== null
                                    ? `${hoursOverdue}h`
                                    : ""}
                                </span>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                  </tbody>
                </table>
              )}
            </div>

            {/* SLA Configuration */}
            <div className="bg-white border border-slate-200 rounded-xl p-5">
              <h3 className="font-medium text-slate-900 text-sm mb-1">
                SLA Configuration
              </h3>
              <p className="text-xs text-slate-400 mb-4">
                Hours allowed before a finding of each severity is considered
                overdue. Changes apply to new findings only.
              </p>
              <div className="grid grid-cols-3 gap-4 mb-4">
                {(
                  [
                    { key: "critical", label: "Critical", default: 72 },
                    { key: "high", label: "High", default: 168 },
                    { key: "medium", label: "Medium", default: 720 },
                  ] as const
                ).map(({ key, label, default: def }) => (
                  <div key={key}>
                    <label className="block text-xs font-medium text-slate-600 mb-1">
                      {label} (hours)
                    </label>
                    <input
                      type="number"
                      min={1}
                      className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-blue-400"
                      value={
                        slaEdits[key] !== undefined
                          ? slaEdits[key]
                          : slaConfig?.[key] ?? def
                      }
                      onChange={(e) =>
                        setSlaEdits((prev) => ({
                          ...prev,
                          [key]: parseInt(e.target.value, 10) || def,
                        }))
                      }
                    />
                  </div>
                ))}
              </div>
              <button
                onClick={saveSlaConfig}
                disabled={slaSaving || Object.keys(slaEdits).length === 0}
                className="inline-flex items-center gap-2 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {slaSaving ? "Saving..." : "Save SLA Config"}
              </button>
            </div>
          </div>
        )}
```

- [ ] **Step 5: Verify TypeScript**

```bash
docker compose exec frontend npx tsc --noEmit 2>&1 | grep "VulnerabilityRemediation" | head -5
```

Expected: no errors.

- [ ] **Step 6: Restart frontend and verify the SLA tab appears**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Navigate to `/remediation` and confirm the "SLA" tab appears. Click it and confirm the three summary cards load.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/pages/VulnerabilityRemediation.tsx
git commit -m "feat(sla): add SLA tab to VulnerabilityRemediation — summary cards, overdue findings list, config panel"
```

---

## Self-review

**Spec coverage:**
- ✅ Configurable SLA hours per org — Task 1 (model/migration) + Task 3 (endpoints + ingestion)
- ✅ Auto-escalation scheduler — Task 2 (`enforce_slas` extended with escalation thresholds)
- ✅ `/sla/config` GET + PUT — Task 3
- ✅ `sla_escalated` in FindingRead — Task 3
- ✅ SLABadge: ESCALATED / OVERDUE / countdown / on-track — Task 4
- ✅ SLA tab: summary cards, overdue list, config panel — Task 5

**Placeholder scan:** None found. All steps have concrete code.

**Type consistency:** `sla_escalated: Optional[bool]` defined in Task 3 schema, used in Task 4 `Finding` interface and Task 5 table rendering. `SLAConfig` defined in Task 3, used in Task 5 query. Consistent throughout.
