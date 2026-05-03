# Vulnerability Remediation Pipeline — Design Spec

**Date:** 2026-05-03
**Status:** Approved
**Scope:** Close the loop between scanner findings (Qualys, Tenable, Wiz) and automated fixes — auto-generating DRAFT change requests, tracking SLAs, and providing a CVE blast-radius view. No finding auto-approves without an explicit `RemediationPolicy` rule; every generated change request starts as DRAFT.

---

## Background

Nexplane connectors for Qualys, Tenable, Snyk, Wiz, and CrowdStrike are currently discovery-only: scan results land in `asset_metadata` but nothing happens next. Operators must manually triage findings, decide what to fix, and write change requests by hand. At scale this breaks down — critical CVEs age past SLA, misconfiguration findings accumulate, and there is no single place to track remediation status.

This spec adds a remediation layer: a new `vulnerability` router ingests findings (push via webhook, pull via polling job), matches them to Nexplane assets, auto-generates DRAFT change requests scoped to affected assets, enforces configurable SLA timers, and surfaces everything in a new "Pending Remediation" queue in the frontend.

---

## Design Decisions

- **Always DRAFT first:** Auto-generated change requests are created with `status = "draft"`. Auto-approval requires an explicit `RemediationPolicy` rule with `approval_level = "auto"`. Without a matching policy rule, all findings queue as DRAFT for human review.
- **Finding ingestion — push + pull:** A new webhook endpoint (`POST /webhooks/vulnerability-findings`) handles scanner push (Qualys, Tenable native webhooks). A background polling job handles scanners that only support pull. Both paths normalize to `VulnerabilityFinding` rows.
- **Asset matching by IP and hostname:** Findings are joined to `assets` on `ip_address` and `hostname`. Unmatched findings are stored with `asset_id = NULL` and surfaced in a "Unmatched Findings" list so operators can manually associate them.
- **RemediationPolicy is per-organization:** Each org configures its own rules mapping `(finding_type, severity)` → `(action_type, approval_level)`. Stored in the DB, editable via Settings.
- **SLA timers start at finding ingestion time:** Critical = 72 h, High = 7 days, Medium = 30 days. Timer is stored in `RemediationSLA`; a scheduler job checks for overdue findings every 15 minutes and auto-creates change requests + sends notifications.
- **CVE blast-radius query hits `asset_metadata`:** Software inventory stored by agent discovery is already in the `asset_metadata` JSONB column. The blast-radius endpoint queries that column for matching package/version ranges — no new data collection needed.
- **Change type mapping:** Package CVE → `patch_packages`. S3 public access → `s3_block_public_access`. Overly permissive security group → `security_group_update`. IAM no-MFA → `iam_enforce_mfa`. Misconfiguration fallback → `generic_remediation`.

---

## Section 1: Data Models

### 1.1 `VulnerabilityFinding`

```python
# backend/app/models/vulnerability_finding.py

class VulnerabilityFinding(Base):
    __tablename__ = "vulnerability_findings"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    asset_id        = Column(UUID(as_uuid=True), ForeignKey("assets.id"), nullable=True, index=True)

    # Source
    scanner         = Column(String, nullable=False)         # "qualys" | "tenable" | "wiz" | "snyk" | "crowdstrike"
    scanner_finding_id = Column(String, nullable=True)       # native ID from scanner, for deduplication
    source          = Column(String, nullable=False)         # "webhook" | "poll"

    # Finding details
    finding_type    = Column(String, nullable=False)         # "cve" | "misconfiguration" | "secret" | "iac"
    severity        = Column(String, nullable=False)         # "critical" | "high" | "medium" | "low" | "informational"
    cve_id          = Column(String, nullable=True)          # e.g. "CVE-2024-1234"
    title           = Column(String, nullable=False)
    description     = Column(Text, nullable=True)
    remediation_hint = Column(Text, nullable=True)           # scanner-provided fix guidance
    affected_package = Column(String, nullable=True)         # e.g. "openssl"
    affected_version = Column(String, nullable=True)         # e.g. "3.0.2"
    fixed_version   = Column(String, nullable=True)          # e.g. "3.0.7"
    resource_type   = Column(String, nullable=True)          # "s3_bucket" | "security_group" | "iam_user" | ...
    resource_id     = Column(String, nullable=True)          # cloud resource identifier

    # Target asset identity (before/during match)
    target_ip       = Column(String, nullable=True)
    target_hostname = Column(String, nullable=True)

    # Lifecycle
    status          = Column(String, nullable=False, default="open")
    # "open" | "change_request_generated" | "remediated" | "accepted_risk" | "false_positive"
    change_request_id = Column(UUID(as_uuid=True), ForeignKey("change_requests.id"), nullable=True)
    ingested_at     = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    first_seen_at   = Column(DateTime(timezone=True), nullable=True)   # scanner-reported
    last_seen_at    = Column(DateTime(timezone=True), nullable=True)
    raw_payload     = Column(JSONB, nullable=True)            # original scanner payload, for audit

    __table_args__ = (
        UniqueConstraint("organization_id", "scanner", "scanner_finding_id",
                         name="uq_finding_scanner_id"),
    )
```

### 1.2 `RemediationPolicy`

```python
# backend/app/models/remediation_policy.py

class RemediationPolicy(Base):
    __tablename__ = "remediation_policies"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    name            = Column(String, nullable=False)

    # Matching criteria (all non-null fields must match for the rule to apply)
    match_scanner      = Column(String, nullable=True)       # None = any scanner
    match_finding_type = Column(String, nullable=True)       # None = any type
    match_severity     = Column(ARRAY(String), nullable=True) # None = any severity; e.g. ["critical","high"]
    match_resource_type = Column(String, nullable=True)      # None = any resource type

    # Action
    action_type        = Column(String, nullable=False)
    # "patch_packages" | "s3_block_public_access" | "security_group_update" |
    # "iam_enforce_mfa" | "generic_remediation" | "notify_only" | "suppress"
    action_params      = Column(JSONB, nullable=True)        # extra params forwarded to the change request

    # Approval gate
    approval_level     = Column(String, nullable=False, default="require_approval")
    # "require_approval" | "auto"  — auto only takes effect if action_type is low-risk

    # Priority: higher number wins when multiple rules match
    priority           = Column(Integer, nullable=False, default=0)
    enabled            = Column(Boolean, nullable=False, default=True)

    created_at         = Column(DateTime(timezone=True), server_default=func.now())
    updated_at         = Column(DateTime(timezone=True), onupdate=func.now())
```

### 1.3 `RemediationSLA`

```python
# backend/app/models/remediation_sla.py

class RemediationSLA(Base):
    __tablename__ = "remediation_slas"

    id                 = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id    = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True)
    finding_id         = Column(UUID(as_uuid=True), ForeignKey("vulnerability_findings.id"),
                                nullable=False, unique=True)

    severity           = Column(String, nullable=False)
    sla_hours          = Column(Integer, nullable=False)     # 72 / 168 / 720
    due_at             = Column(DateTime(timezone=True), nullable=False)
    breached           = Column(Boolean, nullable=False, default=False)
    breach_notified_at = Column(DateTime(timezone=True), nullable=True)
    escalated_at       = Column(DateTime(timezone=True), nullable=True)

    created_at         = Column(DateTime(timezone=True), server_default=func.now())
```

**Default SLA hours by severity:**

| Severity | SLA Hours | Human |
|----------|-----------|-------|
| critical | 72 | 3 days |
| high | 168 | 7 days |
| medium | 720 | 30 days |
| low | — | No SLA enforced |
| informational | — | No SLA enforced |

---

## Section 2: API Endpoints

### 2.1 New Router

**New file:** `backend/app/routers/vulnerability.py`

All routes prefixed `/api/v1/vulnerability`. Protected by existing `get_current_user` dependency unless noted.

---

#### `POST /webhooks/vulnerability-findings`

**Auth:** HMAC-SHA256 signature header (`X-Nexplane-Signature: sha256=<hex>`). Secret stored per-organization in `connector_credentials`. No session token required — this endpoint is called by external scanners.

**Request body:**

```json
{
  "scanner": "qualys",
  "organization_id": "uuid",
  "findings": [
    {
      "scanner_finding_id": "QID-12345",
      "finding_type": "cve",
      "severity": "critical",
      "cve_id": "CVE-2024-1234",
      "title": "OpenSSL Buffer Overflow",
      "description": "...",
      "remediation_hint": "Upgrade openssl to 3.0.7+",
      "affected_package": "openssl",
      "affected_version": "3.0.2",
      "fixed_version": "3.0.7",
      "target_ip": "10.0.1.50",
      "target_hostname": "web-prod-01",
      "first_seen_at": "2026-05-01T00:00:00Z",
      "last_seen_at": "2026-05-03T00:00:00Z",
      "raw_payload": {}
    }
  ]
}
```

**Response:** `202 Accepted`

```json
{
  "accepted": 1,
  "duplicates_skipped": 0,
  "queued_for_matching": 1
}
```

**Processing (async, background task):**
1. Deduplicate on `(organization_id, scanner, scanner_finding_id)`.
2. Match each finding to an `asset` by `ip_address` or `hostname`.
3. Insert `VulnerabilityFinding` rows.
4. Insert `RemediationSLA` for critical/high/medium findings.
5. Evaluate `RemediationPolicy` rules → generate DRAFT change requests as configured.

---

#### `GET /vulnerability/findings`

List findings for the authenticated org with filtering and pagination.

**Query params:** `severity`, `status`, `scanner`, `finding_type`, `asset_id`, `overdue` (bool), `page`, `page_size`

**Response:** `200 OK`

```json
{
  "total": 142,
  "page": 1,
  "page_size": 25,
  "findings": [
    {
      "id": "uuid",
      "scanner": "qualys",
      "finding_type": "cve",
      "severity": "critical",
      "cve_id": "CVE-2024-1234",
      "title": "OpenSSL Buffer Overflow",
      "asset_id": "uuid",
      "asset_name": "web-prod-01",
      "status": "open",
      "change_request_id": null,
      "ingested_at": "2026-05-03T10:00:00Z",
      "sla_due_at": "2026-05-06T10:00:00Z",
      "sla_breached": false
    }
  ]
}
```

---

#### `GET /vulnerability/findings/{finding_id}`

Single finding detail including full `raw_payload` and linked change request summary.

---

#### `POST /vulnerability/findings/{finding_id}/generate-change-request`

Manually trigger change request generation for a specific finding. Useful when no `RemediationPolicy` matched automatically.

**Request body:** optional overrides

```json
{
  "action_type": "patch_packages",
  "action_params": {},
  "title_override": null
}
```

**Response:** `201 Created` — the created `ChangeRequest` object.

---

#### `PATCH /vulnerability/findings/{finding_id}/status`

Update finding lifecycle status.

**Request body:**

```json
{
  "status": "accepted_risk",
  "reason": "Mitigated by WAF rule"
}
```

**Valid transitions:** `open → accepted_risk | false_positive`. `change_request_generated` is set by the system only.

---

#### `GET /vulnerability/cve/{cve_id}/blast-radius`

Query asset metadata for assets running the vulnerable package/version.

**Response:** `200 OK`

```json
{
  "cve_id": "CVE-2024-1234",
  "affected_assets": [
    {
      "asset_id": "uuid",
      "hostname": "web-prod-01",
      "ip_address": "10.0.1.50",
      "package": "openssl",
      "installed_version": "3.0.2",
      "os": "Ubuntu 22.04"
    }
  ],
  "total_affected": 47,
  "known_fixed_version": "3.0.7"
}
```

**Query logic:**

```sql
SELECT a.id, a.hostname, a.ip_address,
       meta.value->>'installed_version' AS installed_version,
       meta.value->>'os'               AS os
FROM   assets a,
       jsonb_array_elements(a.asset_metadata->'software') AS meta(value)
WHERE  a.organization_id = :org_id
AND    meta.value->>'package' = :package_name
AND    meta.value->>'installed_version' = :affected_version;
```

---

#### `POST /vulnerability/cve/{cve_id}/patch-campaign`

One-click create a patch campaign change request targeting all affected assets from the blast-radius query.

**Request body:**

```json
{
  "target_asset_ids": ["uuid", "uuid"],
  "batch_size": 10,
  "rollout_strategy": "rolling"
}
```

**Response:** `201 Created` — `ChangeRequest` with `change_type = "patch_packages"`, `status = "draft"`.

---

#### `GET /vulnerability/sla/dashboard`

Summary stats for the SLA dashboard widget.

**Response:** `200 OK`

```json
{
  "critical": { "total": 12, "overdue": 3, "due_soon": 2 },
  "high":     { "total": 34, "overdue": 5, "due_soon": 8 },
  "medium":   { "total": 89, "overdue": 0, "due_soon": 12 }
}
```

`due_soon` = SLA expires within the next 24 hours.

---

#### `GET /vulnerability/policies`

List `RemediationPolicy` rules for the org.

#### `POST /vulnerability/policies`

Create a new policy rule.

#### `PATCH /vulnerability/policies/{policy_id}`

Update a policy rule (enable/disable, change action type, change approval level).

#### `DELETE /vulnerability/policies/{policy_id}`

Delete a policy rule.

---

## Section 3: Webhook Format Reference

Nexplane normalizes scanner-native payloads. Scanners that support webhooks push directly; others are polled. Both paths produce the same `VulnerabilityFinding` schema.

| Scanner | Ingestion Method | Notes |
|---------|-----------------|-------|
| Qualys | Webhook push | `POST /webhooks/vulnerability-findings` |
| Tenable | Webhook push | Tenable.io native webhook → Nexplane format |
| Wiz | Webhook push | Wiz issues webhook |
| Snyk | Webhook push | Snyk project webhook |
| CrowdStrike | Polling | CrowdStrike Spotlight API polled every 15 min |

**HMAC verification (all webhook paths):**

```python
import hashlib, hmac

def verify_signature(body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, header)
```

---

## Section 4: Background Jobs

### 4.1 Scanner Polling Job

**File:** `backend/app/jobs/scanner_poll.py`

Runs every 15 minutes via APScheduler (already used in the project).

```python
async def poll_vulnerability_findings(org_id: UUID):
    """Pull findings from scanners that don't support push webhooks."""
    # CrowdStrike Spotlight: GET /spotlight/queries/vulnerabilities/v1
    # Normalize results → POST internal ingest function (same path as webhook)
    ...
```

### 4.2 SLA Enforcement Job

**File:** `backend/app/jobs/sla_enforcement.py`

Runs every 15 minutes.

```python
async def enforce_slas():
    """
    1. Find RemediationSLA rows where due_at < now() and breached = false.
    2. Set breached = true.
    3. If finding.status == "open" and no change_request_id:
       - Auto-generate a DRAFT change request (uses highest-priority matching policy,
         or generic_remediation if no policy matches).
    4. Send notification (email + in-app) to org admins.
    5. Update breach_notified_at.
    """
    ...
```

### 4.3 Asset Matching Job

**File:** `backend/app/jobs/finding_asset_match.py`

Runs every 5 minutes. Picks up `VulnerabilityFinding` rows with `asset_id = NULL` and retries the IP/hostname match (useful when an asset is registered after the finding is ingested).

---

## Section 5: Change Request Auto-Generation Logic

**File:** `backend/app/services/remediation_service.py`

```python
async def generate_change_request_for_finding(
    finding: VulnerabilityFinding,
    policy: RemediationPolicy | None,
    db: AsyncSession,
) -> ChangeRequest:
    """
    Derive change_type and params from the finding + matched policy.
    Always creates status="draft".
    """
    action_type = policy.action_type if policy else _default_action(finding)
    params      = policy.action_params or {} if policy else {}

    title = _build_title(finding)      # e.g. "Patch CVE-2024-1234 on web-prod-01"
    plan  = await ai_generate_plan(finding, action_type)  # existing AI plan generation

    cr = ChangeRequest(
        organization_id = finding.organization_id,
        asset_id        = finding.asset_id,
        change_type     = action_type,
        title           = title,
        description     = finding.description,
        ai_plan         = plan,
        status          = "draft",
        source          = "auto_remediation",
        finding_id      = finding.id,     # new FK column
    )
    db.add(cr)
    await db.flush()

    finding.change_request_id = cr.id
    finding.status = "change_request_generated"

    return cr

def _default_action(finding: VulnerabilityFinding) -> str:
    if finding.finding_type == "cve":
        return "patch_packages"
    type_map = {
        "s3_public_access":      "s3_block_public_access",
        "security_group_open":   "security_group_update",
        "iam_no_mfa":            "iam_enforce_mfa",
    }
    return type_map.get(finding.resource_type, "generic_remediation")
```

**Policy matching (highest priority wins, first match):**

```python
def match_policy(
    finding: VulnerabilityFinding,
    policies: list[RemediationPolicy],
) -> RemediationPolicy | None:
    candidates = [
        p for p in policies
        if p.enabled
        and (p.match_scanner      is None or p.match_scanner      == finding.scanner)
        and (p.match_finding_type is None or p.match_finding_type == finding.finding_type)
        and (p.match_severity     is None or finding.severity in p.match_severity)
        and (p.match_resource_type is None or p.match_resource_type == finding.resource_type)
    ]
    return max(candidates, key=lambda p: p.priority) if candidates else None
```

---

## Section 6: Frontend Components

### 6.1 Pending Remediation Queue

**New page:** `frontend/src/pages/Remediation.tsx`

Route: `/remediation`

```
┌─────────────────────────────────────────────────────────────────┐
│  Pending Remediation                   [Filter ▾] [Export]      │
├───────────┬──────────┬───────────────┬────────────┬─────────────┤
│ Severity  │ Finding  │ Asset         │ SLA        │ Actions     │
├───────────┼──────────┼───────────────┼────────────┼─────────────┤
│ CRITICAL  │ CVE-2024 │ web-prod-01   │ 2d 14h     │ [Review CR] │
│           │ -1234    │               │ remaining  │ [Suppress]  │
├───────────┼──────────┼───────────────┼────────────┼─────────────┤
│ HIGH      │ S3 pub.  │ prod-assets   │ 5d 3h      │ [Review CR] │
│           │ access   │               │ remaining  │ [Suppress]  │
└───────────┴──────────┴───────────────┴────────────┴─────────────┘
```

State: React Query `useQuery(["findings", filters])` → `GET /api/v1/vulnerability/findings`.

### 6.2 CVE Blast Radius UI

**New component:** `frontend/src/components/CveBlastRadius.tsx`

Embedded in the Remediation page as a side panel or accessible from a top-level search bar.

```
┌────────────────────────────────────────────┐
│  CVE Lookup                                │
│  [ CVE-2024-1234              ] [Search]   │
├────────────────────────────────────────────┤
│  CVE-2024-1234 — OpenSSL Buffer Overflow   │
│  47 assets running openssl 3.0.x           │
│  Fixed in: 3.0.7                           │
│                                            │
│  [▶ Generate Patch Campaign]               │
│                                            │
│  Asset list (expandable):                  │
│   • web-prod-01  10.0.1.50  openssl 3.0.2  │
│   • web-prod-02  10.0.1.51  openssl 3.0.2  │
│   • ...                                    │
└────────────────────────────────────────────┘
```

### 6.3 SLA Dashboard Widget

**New component:** `frontend/src/components/SLAWidget.tsx`

Placed on the main Dashboard page alongside existing widgets.

```
┌──────────────────────────────────┐
│  Remediation SLA Status          │
│                                  │
│  Critical   12 open  ⚠ 3 OVERDUE │
│  High       34 open  ⚠ 5 OVERDUE │
│  Medium     89 open  ✓ on track  │
│                                  │
│  [View overdue findings →]       │
└──────────────────────────────────┘
```

Data: `useQuery(["sla-dashboard"])` → `GET /api/v1/vulnerability/sla/dashboard`. Refetches every 5 minutes.

### 6.4 Remediation Policy Settings Panel

**New tab in Settings page:** `frontend/src/pages/Settings.tsx` → new "Remediation Policies" tab.

Displays a table of `RemediationPolicy` rules. Operators can:
- Create a new rule (modal form: match criteria + action type + approval level).
- Enable/disable a rule.
- Reorder rules by priority (drag handle).
- Delete a rule.

---

## Section 7: DB Migration

**New file:** `backend/alembic/versions/XXXX_vulnerability_remediation.py`

```python
"""Add vulnerability remediation tables

Revision ID: XXXX
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

def upgrade():
    op.create_table(
        "vulnerability_findings",
        sa.Column("id",                  UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id",     UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id",            UUID(as_uuid=True), nullable=True),
        sa.Column("scanner",             sa.String,          nullable=False),
        sa.Column("scanner_finding_id",  sa.String,          nullable=True),
        sa.Column("source",              sa.String,          nullable=False),
        sa.Column("finding_type",        sa.String,          nullable=False),
        sa.Column("severity",            sa.String,          nullable=False),
        sa.Column("cve_id",              sa.String,          nullable=True),
        sa.Column("title",               sa.String,          nullable=False),
        sa.Column("description",         sa.Text,            nullable=True),
        sa.Column("remediation_hint",    sa.Text,            nullable=True),
        sa.Column("affected_package",    sa.String,          nullable=True),
        sa.Column("affected_version",    sa.String,          nullable=True),
        sa.Column("fixed_version",       sa.String,          nullable=True),
        sa.Column("resource_type",       sa.String,          nullable=True),
        sa.Column("resource_id",         sa.String,          nullable=True),
        sa.Column("target_ip",           sa.String,          nullable=True),
        sa.Column("target_hostname",     sa.String,          nullable=True),
        sa.Column("status",              sa.String,          nullable=False, server_default="open"),
        sa.Column("change_request_id",   UUID(as_uuid=True), nullable=True),
        sa.Column("ingested_at",         sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("first_seen_at",       sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at",        sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_payload",         JSONB,              nullable=True),
    )
    op.create_index("ix_vf_org_id",    "vulnerability_findings", ["organization_id"])
    op.create_index("ix_vf_asset_id",  "vulnerability_findings", ["asset_id"])
    op.create_index("ix_vf_severity",  "vulnerability_findings", ["severity"])
    op.create_index("ix_vf_status",    "vulnerability_findings", ["status"])
    op.create_unique_constraint(
        "uq_finding_scanner_id",
        "vulnerability_findings",
        ["organization_id", "scanner", "scanner_finding_id"],
    )

    op.create_table(
        "remediation_policies",
        sa.Column("id",                  UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id",     UUID(as_uuid=True), nullable=False),
        sa.Column("name",                sa.String,          nullable=False),
        sa.Column("match_scanner",       sa.String,          nullable=True),
        sa.Column("match_finding_type",  sa.String,          nullable=True),
        sa.Column("match_severity",      ARRAY(sa.String),   nullable=True),
        sa.Column("match_resource_type", sa.String,          nullable=True),
        sa.Column("action_type",         sa.String,          nullable=False),
        sa.Column("action_params",       JSONB,              nullable=True),
        sa.Column("approval_level",      sa.String,          nullable=False, server_default="require_approval"),
        sa.Column("priority",            sa.Integer,         nullable=False, server_default="0"),
        sa.Column("enabled",             sa.Boolean,         nullable=False, server_default="true"),
        sa.Column("created_at",          sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at",          sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_rp_org_id", "remediation_policies", ["organization_id"])

    op.create_table(
        "remediation_slas",
        sa.Column("id",                  UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id",     UUID(as_uuid=True), nullable=False),
        sa.Column("finding_id",          UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("severity",            sa.String,          nullable=False),
        sa.Column("sla_hours",           sa.Integer,         nullable=False),
        sa.Column("due_at",              sa.DateTime(timezone=True), nullable=False),
        sa.Column("breached",            sa.Boolean,         nullable=False, server_default="false"),
        sa.Column("breach_notified_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_at",        sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at",          sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_rs_org_due", "remediation_slas", ["organization_id", "due_at"])

    # Add finding_id FK column to change_requests
    op.add_column(
        "change_requests",
        sa.Column("finding_id", UUID(as_uuid=True),
                  sa.ForeignKey("vulnerability_findings.id"), nullable=True),
    )
    op.add_column(
        "change_requests",
        sa.Column("source", sa.String, nullable=True),   # "manual" | "auto_remediation" | "sla_breach"
    )


def downgrade():
    op.drop_column("change_requests", "source")
    op.drop_column("change_requests", "finding_id")
    op.drop_table("remediation_slas")
    op.drop_table("remediation_policies")
    op.drop_table("vulnerability_findings")
```

---

## Files Changed

| File | Change |
|------|--------|
| `backend/app/routers/vulnerability.py` | New router — all `/api/v1/vulnerability/*` and `/webhooks/vulnerability-findings` endpoints |
| `backend/app/models/vulnerability_finding.py` | New model — `VulnerabilityFinding` |
| `backend/app/models/remediation_policy.py` | New model — `RemediationPolicy` |
| `backend/app/models/remediation_sla.py` | New model — `RemediationSLA` |
| `backend/app/services/remediation_service.py` | New service — `generate_change_request_for_finding`, `match_policy`, blast-radius query |
| `backend/app/jobs/scanner_poll.py` | New job — CrowdStrike Spotlight poll (and future pull-based scanners) |
| `backend/app/jobs/sla_enforcement.py` | New job — SLA breach detection + auto-CR generation + notifications |
| `backend/app/jobs/finding_asset_match.py` | New job — retry asset matching for unmatched findings |
| `backend/app/main.py` | Register `vulnerability` router; schedule three new jobs |
| `backend/alembic/versions/XXXX_vulnerability_remediation.py` | New migration — three new tables + two columns on `change_requests` |
| `frontend/src/pages/Remediation.tsx` | New page — Pending Remediation queue |
| `frontend/src/components/CveBlastRadius.tsx` | New component — CVE search + blast-radius list + patch campaign button |
| `frontend/src/components/SLAWidget.tsx` | New component — SLA status dashboard widget |
| `frontend/src/pages/Settings.tsx` | Add "Remediation Policies" tab with rule CRUD |
| `frontend/src/App.tsx` | Add `/remediation` route |
