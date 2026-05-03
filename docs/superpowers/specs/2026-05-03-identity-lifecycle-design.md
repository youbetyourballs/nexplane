# Identity Lifecycle Management — Design Spec

**Date:** 2026-05-03
**Status:** Approved
**Scope:** Orchestrated multi-connector workflows for user lifecycle events: offboarding (kill switch across all identity systems), onboarding (provision across all systems), and periodic access reviews with manager-driven approval flows.

---

## Background

Today, disabling a departing employee in Nexplane means manually creating separate change requests for AD, Okta, Entra ID, Google Workspace, GitHub, and Slack. There is no coordination, no guaranteed ordering, no rollback if one step fails, and no audit trail that proves all systems were addressed. The same fragmentation applies to onboarding. Access reviews are done entirely outside the platform.

This spec introduces three new change types — `offboard_user`, `onboard_user`, and `access_review` — that treat the **email address** as the canonical cross-system user identity and orchestrate multi-step change plans across every connected identity system in a single atomic operation.

---

## Design Decisions

- **Email as canonical identifier:** Every connector that has the concept of a user account uses the account's primary email address as the join key. The resolution step queries each connector's asset inventory for an asset where `asset_metadata.email == target_email` (or equivalent field per connector). Only connectors that resolve an account proceed to the plan.
- **One step per connector:** The change plan for `offboard_user` and `onboard_user` has exactly one step per relevant connector, each targeting that connector. Steps that are order-independent run in parallel; steps with dependencies (e.g., revoke sessions before suspending account) are sequential within a connector.
- **Rollback defined per step:** Every offboarding step definition includes a `rollback_action` that re-enables or recreates the account. Rollback is invoked automatically if a later required step fails.
- **Access review is a separate model:** It is not a change request. It is a read-then-approve workflow that _generates_ change requests for approved removals. This keeps the change request model clean and allows the review to span days or weeks.
- **New change type definitions live in `backend/app/change_type_definitions/`:** Following the existing pattern, each new change type is a Python module that exports a `definition` dict and an `execute` coroutine.
- **CrowdStrike isolation is opt-in:** Endpoint isolation is a disruptive action. The offboarding plan includes a CrowdStrike step only when the request payload sets `isolate_endpoints: true`.

---

## Section 1: Data Models

### 1.1 Offboard / Onboard Request Payload

These are stored in `change_requests.request_payload` (JSONB). No new tables are required.

```python
# offboard_user request_payload schema
class OffboardUserPayload(BaseModel):
    target_email: str                  # canonical identifier
    reason: str                        # "resignation" | "termination" | "contract_end"
    isolate_endpoints: bool = False    # include CrowdStrike isolation step
    notify_manager: bool = True        # email offboarding report to manager
    manager_email: str | None = None   # resolved automatically if None

# onboard_user request_payload schema
class OnboardUserPayload(BaseModel):
    target_email: str
    display_name: str
    department: str
    manager_email: str
    # per-connector provisioning hints
    ad_ou: str | None = None            # e.g. "OU=Engineering,DC=corp,DC=example,DC=com"
    ad_groups: list[str] = []
    okta_groups: list[str] = []
    google_org_unit: str | None = None  # e.g. "/Engineering"
    github_teams: list[str] = []        # e.g. ["backend", "infra"]
    slack_channels: list[str] = []      # optional pre-join channels
```

### 1.2 Access Review Model

New table `access_reviews`:

```sql
CREATE TABLE access_reviews (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    title           TEXT NOT NULL,
    scope           JSONB NOT NULL,        -- which connectors / groups to review
    status          TEXT NOT NULL DEFAULT 'collecting',
    --  collecting → awaiting_approval → approved → generating_changes → completed | failed
    collected_at    TIMESTAMPTZ,           -- when the snapshot was taken
    approved_at     TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    snapshot        JSONB,                 -- full collected data (see 1.3)
    decisions       JSONB,                 -- manager decisions keyed by entry_id
    created_by      UUID REFERENCES users(id)
);

CREATE TABLE access_review_change_requests (
    review_id       UUID REFERENCES access_reviews(id),
    change_request_id UUID REFERENCES change_requests(id),
    PRIMARY KEY (review_id, change_request_id)
);
```

SQLAlchemy model `backend/app/models/access_review.py`:

```python
class AccessReview(Base):
    __tablename__ = "access_reviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now())
    title = Column(Text, nullable=False)
    scope = Column(JSONB, nullable=False)
    status = Column(Text, nullable=False, default="collecting")
    collected_at = Column(DateTime(timezone=True))
    approved_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    snapshot = Column(JSONB)
    decisions = Column(JSONB)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id"))
```

### 1.3 Access Review Snapshot Structure

The `snapshot` column holds the collected state:

```json
{
  "entries": [
    {
      "entry_id": "uuid",
      "user_email": "alice@corp.com",
      "user_display_name": "Alice Smith",
      "manager_email": "bob@corp.com",
      "connector_id": "uuid-of-ad-connector",
      "connector_type": "active_directory",
      "access_type": "group_membership",
      "access_value": "CN=VPN-Users,OU=Groups,DC=corp,DC=example,DC=com",
      "access_label": "VPN-Users (AD Group)",
      "last_used": null,          # populated where the connector provides it
      "risk_level": "medium"      # low | medium | high — derived from access_value heuristics
    }
  ],
  "collected_at": "2026-05-03T12:00:00Z",
  "connector_ids": ["uuid1", "uuid2"]
}
```

### 1.4 Access Review Decisions Structure

The `decisions` column holds manager responses after the approval phase:

```json
{
  "uuid-entry-id": {
    "decision": "keep",       # "keep" | "revoke"
    "decided_by": "bob@corp.com",
    "decided_at": "2026-05-04T09:15:00Z",
    "note": ""
  }
}
```

---

## Section 2: Change Type Definitions

### 2.1 Directory Structure

```
backend/app/change_type_definitions/
  __init__.py
  restart_service.py          # existing example
  offboard_user/
    __init__.py               # exports DEFINITION and build_plan()
    steps/
      disable_ad.py
      revoke_okta.py
      suspend_entra.py
      suspend_google.py
      remove_github.py
      deactivate_slack.py
      isolate_crowdstrike.py
      offboarding_report.py
  onboard_user/
    __init__.py
    steps/
      create_ad.py
      create_okta.py
      create_google.py
      add_github.py
      invite_slack.py
      onboarding_report.py
```

### 2.2 `offboard_user` Definition

`backend/app/change_type_definitions/offboard_user/__init__.py`:

```python
from app.schemas.change_request import ChangePlanStep

DEFINITION = {
    "name": "offboard_user",
    "display_name": "Offboard User",
    "description": (
        "Disable a user across all connected identity systems in a single "
        "coordinated change request. One step is generated per connector "
        "that has an account for the target email address."
    ),
    "payload_schema": "OffboardUserPayload",
    "rollback_supported": True,
    "parallel_steps": False,   # steps run sequentially by default; see build_plan()
}

async def build_plan(payload: dict, resolved_connectors: list[dict]) -> list[ChangePlanStep]:
    """
    Called during change request creation to build the ordered step list.
    resolved_connectors is the list of connectors that have an account for
    payload["target_email"], with connector_type and asset_id pre-resolved.

    Step ordering:
      1. Session revocation (Okta, Entra, Google) — parallel
      2. Account suspension/disable (AD, Okta, Entra, Google) — parallel
      3. Org/workspace removal (GitHub, Slack) — parallel
      4. Endpoint isolation (CrowdStrike) — sequential, only if requested
      5. Offboarding report — sequential, always last
    """
    steps = []
    session_revoke_types = {"okta", "entra_id", "google_workspace"}
    account_disable_types = {"active_directory", "okta", "entra_id", "google_workspace"}
    removal_types = {"github", "slack"}

    phase1 = [c for c in resolved_connectors if c["connector_type"] in session_revoke_types]
    phase2 = [c for c in resolved_connectors if c["connector_type"] in account_disable_types]
    phase3 = [c for c in resolved_connectors if c["connector_type"] in removal_types]

    for c in phase1:
        steps.append(ChangePlanStep(
            name=f"Revoke {c['connector_type']} sessions",
            action=f"revoke_{c['connector_type']}_sessions",
            connector_id=c["connector_id"],
            parameters={"target_email": payload["target_email"], "asset_id": c["asset_id"]},
            phase=1,
        ))
    for c in phase2:
        steps.append(ChangePlanStep(
            name=f"Disable {c['connector_type']} account",
            action=f"disable_{c['connector_type']}_account",
            connector_id=c["connector_id"],
            parameters={"target_email": payload["target_email"], "asset_id": c["asset_id"]},
            phase=2,
        ))
    for c in phase3:
        steps.append(ChangePlanStep(
            name=f"Remove from {c['connector_type']}",
            action=f"remove_{c['connector_type']}_member",
            connector_id=c["connector_id"],
            parameters={"target_email": payload["target_email"], "asset_id": c["asset_id"]},
            phase=3,
        ))

    if payload.get("isolate_endpoints"):
        cs_connectors = [c for c in resolved_connectors if c["connector_type"] == "crowdstrike"]
        for c in cs_connectors:
            steps.append(ChangePlanStep(
                name="Isolate CrowdStrike-managed endpoints",
                action="isolate_crowdstrike_endpoints",
                connector_id=c["connector_id"],
                parameters={"target_email": payload["target_email"]},
                phase=4,
            ))

    steps.append(ChangePlanStep(
        name="Generate offboarding report",
        action="generate_offboarding_report",
        connector_id=None,
        parameters={
            "target_email": payload["target_email"],
            "reason": payload["reason"],
            "notify_manager": payload.get("notify_manager", True),
            "manager_email": payload.get("manager_email"),
        },
        phase=5,
    ))

    return steps
```

### 2.3 `onboard_user` Definition

`backend/app/change_type_definitions/onboard_user/__init__.py`:

```python
DEFINITION = {
    "name": "onboard_user",
    "display_name": "Onboard User",
    "description": (
        "Provision a new user across all connected identity systems. "
        "Steps are generated for each connector type present in the tenant."
    ),
    "payload_schema": "OnboardUserPayload",
    "rollback_supported": True,
    "parallel_steps": False,
}

async def build_plan(payload: dict, available_connectors: list[dict]) -> list[ChangePlanStep]:
    """
    Steps:
      1. Create AD account (must be first — downstream SSO may depend on AD)
      2. Create Okta / Entra / Google accounts — parallel
      3. Add to GitHub org + teams — after identity accounts exist
      4. Invite to Slack — after identity accounts exist
      5. Generate onboarding report with credentials / welcome instructions
    """
    steps = []
    connector_by_type = {c["connector_type"]: c for c in available_connectors}

    if "active_directory" in connector_by_type:
        c = connector_by_type["active_directory"]
        steps.append(ChangePlanStep(
            name="Create Active Directory account",
            action="create_ad_account",
            connector_id=c["connector_id"],
            parameters={
                "target_email": payload["target_email"],
                "display_name": payload["display_name"],
                "ou": payload.get("ad_ou"),
                "groups": payload.get("ad_groups", []),
            },
            phase=1,
        ))

    idp_types = ["okta", "entra_id", "google_workspace"]
    for ct in idp_types:
        if ct in connector_by_type:
            c = connector_by_type[ct]
            steps.append(ChangePlanStep(
                name=f"Create {ct} account",
                action=f"create_{ct}_account",
                connector_id=c["connector_id"],
                parameters={
                    "target_email": payload["target_email"],
                    "display_name": payload["display_name"],
                    "department": payload["department"],
                    "manager_email": payload["manager_email"],
                    "groups": payload.get(f"{ct}_groups", []),
                    "org_unit": payload.get("google_org_unit"),
                },
                phase=2,
            ))

    if "github" in connector_by_type:
        c = connector_by_type["github"]
        steps.append(ChangePlanStep(
            name="Add to GitHub org",
            action="add_github_member",
            connector_id=c["connector_id"],
            parameters={
                "target_email": payload["target_email"],
                "teams": payload.get("github_teams", []),
            },
            phase=3,
        ))

    if "slack" in connector_by_type:
        c = connector_by_type["slack"]
        steps.append(ChangePlanStep(
            name="Invite to Slack workspace",
            action="invite_slack_member",
            connector_id=c["connector_id"],
            parameters={
                "target_email": payload["target_email"],
                "channels": payload.get("slack_channels", []),
            },
            phase=3,
        ))

    steps.append(ChangePlanStep(
        name="Generate onboarding report",
        action="generate_onboarding_report",
        connector_id=None,
        parameters={
            "target_email": payload["target_email"],
            "display_name": payload["display_name"],
            "manager_email": payload["manager_email"],
        },
        phase=4,
    ))

    return steps
```

### 2.4 ChangePlanStep Schema Addition

Add `phase: int` to the existing `ChangePlanStep` schema so the executor can group steps by phase and run each phase's steps in parallel before proceeding to the next:

```python
class ChangePlanStep(BaseModel):
    name: str
    action: str
    connector_id: UUID | None
    parameters: dict
    phase: int = 1          # steps with equal phase run in parallel
    rollback_action: str | None = None
    rollback_parameters: dict = {}
    status: str = "pending"
    result: dict | None = None
    error: str | None = None
```

### 2.5 Connector Resolution Logic

New utility `backend/app/services/identity_resolution.py`:

```python
async def resolve_user_across_connectors(
    db: AsyncSession,
    target_email: str,
    tenant_id: UUID,
) -> list[dict]:
    """
    Queries the assets table for assets matching target_email across all
    active connectors in the tenant. Returns a list of:
      {connector_id, connector_type, asset_id, display_name, account_status}

    Each connector stores the user email in a different metadata key:
      active_directory:  asset_metadata["mail"] or asset_metadata["userPrincipalName"]
      okta:              asset_metadata["login"] or asset_metadata["email"]
      entra_id:          asset_metadata["userPrincipalName"] or asset_metadata["mail"]
      google_workspace:  asset_metadata["primaryEmail"]
      github:            asset_metadata["email"]
      slack:             asset_metadata["profile"]["email"]
      crowdstrike:       asset_metadata["device_policies"]["sensor_update"]["email"]
                         (CrowdStrike assets are endpoints, not user accounts; matched
                          via asset_metadata["last_logged_in_user"] cross-referenced
                          with a user-provided email when isolate_endpoints=True)
    """
    results = []
    # Query uses JSONB containment / path operators per connector_type.
    # Implementation uses a union of connector-specific sub-queries or
    # a single query with a CASE expression on connector_type.
    ...
    return results
```

---

## Section 3: Step Executor — Phase-Aware Execution

`backend/app/services/change_executor.py` is extended to support phased parallel execution:

```python
import asyncio
from itertools import groupby

async def execute_change_plan(change_request_id: UUID, db: AsyncSession):
    cr = await db.get(ChangeRequest, change_request_id)
    steps = cr.change_plan["steps"]  # list of ChangePlanStep dicts

    # Group steps by phase (phase values are contiguous integers starting at 1)
    for phase_num, phase_steps in groupby(steps, key=lambda s: s["phase"]):
        phase_list = list(phase_steps)
        if len(phase_list) == 1:
            await execute_step(phase_list[0], cr, db)
        else:
            await asyncio.gather(*[execute_step(s, cr, db) for s in phase_list])

        # If any step in this phase failed with required=True, abort and rollback
        failed = [s for s in phase_list if s["status"] == "failed" and s.get("required", True)]
        if failed:
            await rollback_completed_steps(steps, db)
            await mark_change_request_failed(cr, db)
            return

    await mark_change_request_completed(cr, db)
```

---

## Section 4: API Endpoints

### 4.1 Change Request Endpoints (existing, extended)

No new endpoints are needed for `offboard_user` and `onboard_user`. They are created via the existing `POST /api/change-requests` endpoint with `change_type` set to the new type name. The backend's change request creation handler calls `build_plan()` from the change type definition to generate the step list after resolving connectors.

**`POST /api/change-requests`** — extended behavior for new change types:

```
Request body:
{
  "change_type": "offboard_user",
  "title": "Offboard Alice Smith (alice@corp.com)",
  "request_payload": {
    "target_email": "alice@corp.com",
    "reason": "termination",
    "isolate_endpoints": true,
    "notify_manager": true,
    "manager_email": "bob@corp.com"
  }
}

Response: 201 Created — standard ChangeRequest schema with change_plan already populated
```

The creation handler:
1. Validates `request_payload` against `OffboardUserPayload` (or `OnboardUserPayload`).
2. Calls `resolve_user_across_connectors()` (offboard) or `get_available_connectors()` (onboard).
3. Calls `build_plan(payload, resolved_connectors)` from the change type definition.
4. Saves the change request with `status = "draft"` and the populated `change_plan`.

### 4.2 Access Review Endpoints (new)

All endpoints are under `/api/access-reviews`.

```
POST   /api/access-reviews                         Create a new review (triggers collection)
GET    /api/access-reviews                         List reviews (paginated)
GET    /api/access-reviews/{review_id}             Get review detail with snapshot + decisions
POST   /api/access-reviews/{review_id}/collect     Re-trigger data collection (if status=collecting)
POST   /api/access-reviews/{review_id}/decisions   Submit manager decisions (bulk)
POST   /api/access-reviews/{review_id}/approve     Finalize decisions and generate change requests
GET    /api/access-reviews/{review_id}/changes     List generated change requests for this review
```

**`POST /api/access-reviews`**

```json
Request:
{
  "title": "Q2 2026 Access Review — Engineering",
  "scope": {
    "connector_ids": ["uuid1", "uuid2"],   // null = all connectors
    "groups": null,                         // null = all groups
    "user_emails": null                     // null = all users
  }
}

Response: 201 Created — AccessReview object, status="collecting"
```

Collection is triggered as a background task immediately after creation. It queries each scoped connector's assets and group memberships via the existing connector ingest/discovery layer and populates `snapshot`.

**`POST /api/access-reviews/{review_id}/decisions`**

```json
Request:
{
  "decisions": {
    "uuid-entry-id-1": {"decision": "keep", "note": ""},
    "uuid-entry-id-2": {"decision": "revoke", "note": "No longer on this project"}
  }
}

Response: 200 OK — updated AccessReview object
```

Decisions can be submitted incrementally. Status stays `awaiting_approval` until `approve` is called.

**`POST /api/access-reviews/{review_id}/approve`**

Validates that all entries have a decision. For each `"revoke"` decision, creates a change request of the appropriate type (e.g., `remove_ad_group_member`, `remove_okta_group_member`) and records the change request IDs in `access_review_change_requests`. Sets status to `generating_changes`, then `completed`.

```json
Response: 200 OK
{
  "review_id": "uuid",
  "status": "completed",
  "generated_change_requests": 7
}
```

### 4.3 Schemas

```python
class AccessReviewScope(BaseModel):
    connector_ids: list[UUID] | None = None
    groups: list[str] | None = None
    user_emails: list[str] | None = None

class AccessReviewCreate(BaseModel):
    title: str
    scope: AccessReviewScope

class AccessReviewDecisionItem(BaseModel):
    decision: Literal["keep", "revoke"]
    note: str = ""

class AccessReviewDecisionsSubmit(BaseModel):
    decisions: dict[str, AccessReviewDecisionItem]  # keyed by entry_id

class AccessReviewOut(BaseModel):
    id: UUID
    created_at: datetime
    updated_at: datetime
    title: str
    scope: dict
    status: str
    collected_at: datetime | None
    approved_at: datetime | None
    completed_at: datetime | None
    snapshot: dict | None
    decisions: dict | None
    created_by: UUID | None

    model_config = ConfigDict(from_attributes=True)
```

---

## Section 5: Frontend

### 5.1 Offboard / Onboard — Change Request Creation

The existing "New Change Request" modal gains two new change type options: **Offboard User** and **Onboard User**. Selecting either type swaps the payload form to a dedicated component:

- `frontend/src/components/change-requests/OffboardUserForm.tsx` — email field, reason dropdown, isolate_endpoints toggle, manager email.
- `frontend/src/components/change-requests/OnboardUserForm.tsx` — email, display name, department, manager email, and per-connector expandable sections for OU / groups / teams.

After submission, the standard change request detail view shows the generated multi-step plan with phase labels ("Phase 1: Revoke Sessions", "Phase 2: Disable Accounts", etc.).

### 5.2 Access Reviews Page

New top-level route `/access-reviews`:

- **List view** — table of reviews with title, scope summary, status badge, created date, created by.
- **Detail view** `/access-reviews/{id}` — tabbed layout:
  - **Summary** — scope, status, collection timestamp, connector breakdown.
  - **Review Entries** — table grouped by manager. Each row shows user, connector, access right, risk badge, and a Keep / Revoke toggle. Managers can filter to "my direct reports." Decisions are saved via `POST /decisions`.
  - **Changes** — list of generated change requests (visible after approval).
- **New Review** button — modal with title input and scope selectors (connector multi-select, optional user/group filters). Submits `POST /api/access-reviews`.

---

## Section 6: Alembic Migration

New migration `backend/alembic/versions/xxxx_add_access_reviews.py`:

```python
def upgrade():
    op.create_table(
        "access_reviews",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("scope", pg.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="collecting"),
        sa.Column("collected_at", sa.DateTime(timezone=True)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("snapshot", pg.JSONB()),
        sa.Column("decisions", pg.JSONB()),
        sa.Column("created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id")),
    )
    op.create_table(
        "access_review_change_requests",
        sa.Column("review_id", pg.UUID(as_uuid=True), sa.ForeignKey("access_reviews.id"), primary_key=True),
        sa.Column("change_request_id", pg.UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), primary_key=True),
    )
    op.create_index("ix_access_reviews_status", "access_reviews", ["status"])
    op.create_index("ix_access_reviews_created_by", "access_reviews", ["created_by"])

def downgrade():
    op.drop_table("access_review_change_requests")
    op.drop_table("access_reviews")
```

---

## Section 7: Report Generation

Both offboarding and onboarding plans include a final "report" step that runs after all connector steps complete. The report step:

1. Collects the result payloads from all preceding steps in the change plan.
2. Renders a structured report document stored in `change_requests.result_metadata["report"]`.
3. Optionally sends the report to `manager_email` via the platform's notification service.

**Offboarding report content:**
- Target user, reason, timestamp.
- Per-connector: action taken, account status before and after, any errors.
- Confirmation that all sessions were revoked.
- List of any steps that were skipped (connector not found for user) or failed.

**Onboarding report content:**
- New user details (email, display name, department, manager).
- Per-connector: account created, groups assigned, temporary credentials (for AD, if generated).
- Next steps / welcome instructions for the new employee.

Reports are accessible via `GET /api/change-requests/{id}` in `result_metadata.report` and rendered in the change request detail view's result panel.

---

## Files Changed

| File | Change |
|------|--------|
| `backend/app/models/access_review.py` | New — `AccessReview` SQLAlchemy model |
| `backend/app/schemas/access_review.py` | New — Pydantic schemas for create, decisions, out |
| `backend/app/schemas/change_request.py` | Add `phase` field to `ChangePlanStep`; add `OffboardUserPayload`, `OnboardUserPayload` |
| `backend/app/routers/access_reviews.py` | New — all `/api/access-reviews` endpoints |
| `backend/app/routers/__init__.py` | Register `access_reviews` router |
| `backend/app/change_type_definitions/offboard_user/__init__.py` | New — `DEFINITION` + `build_plan()` |
| `backend/app/change_type_definitions/offboard_user/steps/disable_ad.py` | New — AD disable + rollback |
| `backend/app/change_type_definitions/offboard_user/steps/revoke_okta.py` | New — Okta session revoke + suspend + rollback |
| `backend/app/change_type_definitions/offboard_user/steps/suspend_entra.py` | New — Entra ID suspend + session revoke + rollback |
| `backend/app/change_type_definitions/offboard_user/steps/suspend_google.py` | New — Google Workspace suspend + OAuth revoke + rollback |
| `backend/app/change_type_definitions/offboard_user/steps/remove_github.py` | New — GitHub org removal + rollback |
| `backend/app/change_type_definitions/offboard_user/steps/deactivate_slack.py` | New — Slack deactivation + rollback |
| `backend/app/change_type_definitions/offboard_user/steps/isolate_crowdstrike.py` | New — CrowdStrike endpoint isolation (opt-in) |
| `backend/app/change_type_definitions/offboard_user/steps/offboarding_report.py` | New — report generation + optional manager notification |
| `backend/app/change_type_definitions/onboard_user/__init__.py` | New — `DEFINITION` + `build_plan()` |
| `backend/app/change_type_definitions/onboard_user/steps/create_ad.py` | New — AD account creation + rollback |
| `backend/app/change_type_definitions/onboard_user/steps/create_okta.py` | New — Okta account creation + rollback |
| `backend/app/change_type_definitions/onboard_user/steps/create_google.py` | New — Google Workspace account creation + rollback |
| `backend/app/change_type_definitions/onboard_user/steps/add_github.py` | New — GitHub org invite + teams + rollback |
| `backend/app/change_type_definitions/onboard_user/steps/invite_slack.py` | New — Slack workspace invite + rollback |
| `backend/app/change_type_definitions/onboard_user/steps/onboarding_report.py` | New — welcome report generation |
| `backend/app/services/identity_resolution.py` | New — `resolve_user_across_connectors()` |
| `backend/app/services/change_executor.py` | Extend — phase-aware parallel step execution + rollback |
| `backend/alembic/versions/xxxx_add_access_reviews.py` | New — migration for `access_reviews` and `access_review_change_requests` tables |
| `frontend/src/pages/AccessReviews.tsx` | New — list + detail view for access reviews |
| `frontend/src/components/change-requests/OffboardUserForm.tsx` | New — offboarding payload form |
| `frontend/src/components/change-requests/OnboardUserForm.tsx` | New — onboarding payload form |
| `frontend/src/api/accessReviews.ts` | New — API client functions for access review endpoints |
| `frontend/src/App.tsx` | Add `/access-reviews` route |
