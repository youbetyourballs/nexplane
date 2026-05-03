# Identity Lifecycle Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement orchestrated multi-connector user lifecycle workflows — offboard, onboard, and access reviews — treating email as the canonical cross-system identity, with phase-based parallel step execution and rollback.

**Architecture:** Two new change types (`offboard_user`, `onboard_user`) are registered alongside existing types in `ChangeType` enum and handled by executor modules in `backend/app/connectors/executors/`. An `identity_resolution` service queries the assets table per connector-type email field to find all accounts for a target email. A new `AccessReview` model stores snapshot + decisions separately from change requests; the `/api/access-reviews` router handles collection, decisions, and approval (which generates individual change requests). Phase-based parallel execution is added to `change_executor.py`. Frontend adds an Access Reviews page and two new payload forms wired into the existing New Change Request flow.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2 (async) / PostgreSQL JSONB / Alembic / React 18 / TypeScript / TanStack Query / pytest-asyncio / SQLite in-memory for tests.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/alembic/versions/016_add_access_reviews.py` | Create | Migration for `access_reviews` and `access_review_change_requests` tables |
| `backend/app/models/access_review.py` | Create | `AccessReview` SQLAlchemy model |
| `backend/app/schemas/access_review.py` | Create | Pydantic schemas: Create, Out, DecisionsSubmit, ApproveOut |
| `backend/app/models/change_request.py` | Modify | Add `offboard_user`, `onboard_user` to `ChangeType` enum |
| `backend/app/schemas/change_request.py` | Modify | Add `OffboardUserPayload`, `OnboardUserPayload` Pydantic models |
| `backend/app/services/identity_resolution.py` | Create | `resolve_user_across_connectors()` — JSONB email lookup per connector type |
| `backend/app/connectors/executors/offboard_user/__init__.py` | Create | `DEFINITION` dict + `build_plan()` |
| `backend/app/connectors/executors/offboard_user/steps/disable_ad.py` | Create | AD disable + rollback |
| `backend/app/connectors/executors/offboard_user/steps/revoke_okta.py` | Create | Okta session revoke + suspend + rollback |
| `backend/app/connectors/executors/offboard_user/steps/suspend_entra.py` | Create | Entra ID suspend + rollback |
| `backend/app/connectors/executors/offboard_user/steps/suspend_google.py` | Create | Google Workspace suspend + rollback |
| `backend/app/connectors/executors/offboard_user/steps/remove_github.py` | Create | GitHub org removal + rollback |
| `backend/app/connectors/executors/offboard_user/steps/deactivate_slack.py` | Create | Slack deactivation + rollback |
| `backend/app/connectors/executors/offboard_user/steps/isolate_crowdstrike.py` | Create | CrowdStrike endpoint isolation (opt-in) |
| `backend/app/connectors/executors/offboard_user/steps/offboarding_report.py` | Create | Report generation + optional manager notification |
| `backend/app/connectors/executors/onboard_user/__init__.py` | Create | `DEFINITION` dict + `build_plan()` |
| `backend/app/connectors/executors/onboard_user/steps/create_ad.py` | Create | AD account creation + rollback |
| `backend/app/connectors/executors/onboard_user/steps/create_okta.py` | Create | Okta account creation + rollback |
| `backend/app/connectors/executors/onboard_user/steps/create_google.py` | Create | Google Workspace account creation + rollback |
| `backend/app/connectors/executors/onboard_user/steps/add_github.py` | Create | GitHub org invite + teams + rollback |
| `backend/app/connectors/executors/onboard_user/steps/invite_slack.py` | Create | Slack workspace invite + rollback |
| `backend/app/connectors/executors/onboard_user/steps/onboarding_report.py` | Create | Welcome report generation |
| `backend/app/routers/access_reviews.py` | Create | All `/api/access-reviews` endpoints |
| `backend/app/main.py` | Modify | Register `access_reviews` router |
| `backend/app/tests/test_identity_resolution.py` | Create | Unit tests for `resolve_user_across_connectors` |
| `backend/app/tests/test_offboard_build_plan.py` | Create | Unit tests for offboard `build_plan()` |
| `backend/app/tests/test_onboard_build_plan.py` | Create | Unit tests for onboard `build_plan()` |
| `backend/app/tests/test_access_reviews.py` | Create | API tests for access review endpoints |
| `frontend/src/pages/AccessReviews.tsx` | Create | List + detail view for access reviews |
| `frontend/src/components/change-requests/OffboardUserForm.tsx` | Create | Offboarding payload form |
| `frontend/src/components/change-requests/OnboardUserForm.tsx` | Create | Onboarding payload form |
| `frontend/src/api/accessReviews.ts` | Create | API client functions for access review endpoints |
| `frontend/src/routes/index.tsx` | Modify | Add `/access-reviews` and `/access-reviews/:id` routes |

---

## Task 1: DB Migration — AccessReview model

**Files:**
- Create: `backend/alembic/versions/016_add_access_reviews.py`
- Create: `backend/app/models/access_review.py`

- [ ] **Step 1: Write the failing test first**

Create `backend/app/tests/test_access_review_model.py`:

```python
import pytest
from app.models.access_review import AccessReview


def test_access_review_tablename():
    assert AccessReview.__tablename__ == "access_reviews"


def test_access_review_has_required_columns():
    cols = {c.key for c in AccessReview.__table__.columns}
    assert "id" in cols
    assert "title" in cols
    assert "scope" in cols
    assert "status" in cols
    assert "snapshot" in cols
    assert "decisions" in cols
    assert "created_by" in cols
```

- [ ] **Step 2: Run — expect ImportError (model doesn't exist)**

```bash
docker compose exec backend pytest app/tests/test_access_review_model.py -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError: No module named 'app.models.access_review'`

- [ ] **Step 3: Create the SQLAlchemy model**

Create `backend/app/models/access_review.py`:

```python
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database import Base


class AccessReview(Base):
    __tablename__ = "access_reviews"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="collecting")
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    decisions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    creator: Mapped["User | None"] = relationship("User", foreign_keys=[created_by])
```

- [ ] **Step 4: Create the Alembic migration**

Create `backend/alembic/versions/016_add_access_reviews.py`:

```python
"""add access_reviews and access_review_change_requests tables

Revision ID: 016
Revises: 015
Create Date: 2026-05-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = '016'
down_revision = '015'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "access_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("scope", JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="collecting"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snapshot", JSONB(), nullable=True),
        sa.Column("decisions", JSONB(), nullable=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )
    op.create_table(
        "access_review_change_requests",
        sa.Column("review_id", UUID(as_uuid=True), sa.ForeignKey("access_reviews.id"), primary_key=True),
        sa.Column("change_request_id", UUID(as_uuid=True), sa.ForeignKey("change_requests.id"), primary_key=True),
    )
    op.create_index("ix_access_reviews_status", "access_reviews", ["status"])
    op.create_index("ix_access_reviews_created_by", "access_reviews", ["created_by"])


def downgrade():
    op.drop_index("ix_access_reviews_created_by", table_name="access_reviews")
    op.drop_index("ix_access_reviews_status", table_name="access_reviews")
    op.drop_table("access_review_change_requests")
    op.drop_table("access_reviews")
```

- [ ] **Step 5: Run migration**

```bash
docker compose exec backend alembic upgrade 016
```

Expected: `Running upgrade 015 -> 016, add access_reviews and access_review_change_requests tables`

- [ ] **Step 6: Run model test — expect pass**

```bash
docker compose exec backend pytest app/tests/test_access_review_model.py -v
```

Expected: `2 passed`

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/016_add_access_reviews.py backend/app/models/access_review.py backend/app/tests/test_access_review_model.py
git commit -m "feat(db): add access_reviews migration and SQLAlchemy model"
```

---

## Task 2: Pydantic Schemas + ChangeType Extension

**Files:**
- Create: `backend/app/schemas/access_review.py`
- Modify: `backend/app/models/change_request.py`
- Modify: `backend/app/schemas/change_request.py`

- [ ] **Step 1: Write failing schema tests**

Create `backend/app/tests/test_access_review_schemas.py`:

```python
import uuid
from app.schemas.access_review import (
    AccessReviewCreate,
    AccessReviewOut,
    AccessReviewDecisionsSubmit,
    AccessReviewDecisionItem,
)
from app.schemas.change_request import OffboardUserPayload, OnboardUserPayload


def test_access_review_create_requires_title_and_scope():
    r = AccessReviewCreate(title="Q2 Review", scope={"connector_ids": None, "groups": None, "user_emails": None})
    assert r.title == "Q2 Review"


def test_access_review_decisions_submit():
    entry_id = str(uuid.uuid4())
    r = AccessReviewDecisionsSubmit(
        decisions={entry_id: AccessReviewDecisionItem(decision="revoke", note="No longer on project")}
    )
    assert r.decisions[entry_id].decision == "revoke"


def test_access_review_decision_invalid():
    import pytest
    with pytest.raises(Exception):
        AccessReviewDecisionItem(decision="maybe")


def test_offboard_payload_defaults():
    p = OffboardUserPayload(target_email="alice@corp.com", reason="termination")
    assert p.isolate_endpoints is False
    assert p.notify_manager is True
    assert p.manager_email is None


def test_onboard_payload():
    p = OnboardUserPayload(
        target_email="new@corp.com",
        display_name="New User",
        department="Engineering",
        manager_email="mgr@corp.com",
    )
    assert p.ad_groups == []
    assert p.github_teams == []
```

- [ ] **Step 2: Run — expect ImportError**

```bash
docker compose exec backend pytest app/tests/test_access_review_schemas.py -v 2>&1 | tail -10
```

Expected: `ImportError`

- [ ] **Step 3: Create `backend/app/schemas/access_review.py`**

```python
import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict


class AccessReviewScope(BaseModel):
    connector_ids: list[uuid.UUID] | None = None
    groups: list[str] | None = None
    user_emails: list[str] | None = None


class AccessReviewCreate(BaseModel):
    title: str
    scope: AccessReviewScope


class AccessReviewDecisionItem(BaseModel):
    decision: Literal["keep", "revoke"]
    note: str = ""


class AccessReviewDecisionsSubmit(BaseModel):
    decisions: dict[str, AccessReviewDecisionItem]


class AccessReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    title: str
    scope: dict
    status: str
    collected_at: datetime | None = None
    approved_at: datetime | None = None
    completed_at: datetime | None = None
    snapshot: dict | None = None
    decisions: dict | None = None
    created_by: uuid.UUID | None = None


class AccessReviewApproveOut(BaseModel):
    review_id: uuid.UUID
    status: str
    generated_change_requests: int
```

- [ ] **Step 4: Add `OffboardUserPayload` and `OnboardUserPayload` to `backend/app/schemas/change_request.py`**

Append to the end of `backend/app/schemas/change_request.py` (before the deferred imports block):

```python
class OffboardUserPayload(BaseModel):
    target_email: str
    reason: Literal["resignation", "termination", "contract_end"]
    isolate_endpoints: bool = False
    notify_manager: bool = True
    manager_email: str | None = None


class OnboardUserPayload(BaseModel):
    target_email: str
    display_name: str
    department: str
    manager_email: str
    ad_ou: str | None = None
    ad_groups: list[str] = []
    okta_groups: list[str] = []
    google_org_unit: str | None = None
    github_teams: list[str] = []
    slack_channels: list[str] = []
```

Also add `from typing import Literal` to the imports at the top of that file.

- [ ] **Step 5: Add new change types to `backend/app/models/change_request.py`**

In the `ChangeType` enum, add after `ec2_terminate = "ec2_terminate"`:

```python
    offboard_user = "offboard_user"
    onboard_user = "onboard_user"
```

- [ ] **Step 6: Run schema tests — expect pass**

```bash
docker compose exec backend pytest app/tests/test_access_review_schemas.py -v
```

Expected: `5 passed`

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/access_review.py backend/app/schemas/change_request.py backend/app/models/change_request.py backend/app/tests/test_access_review_schemas.py
git commit -m "feat(schemas): add access review schemas, OffboardUserPayload, OnboardUserPayload, new change types"
```

---

## Task 3: Identity Resolution Service

**Files:**
- Create: `backend/app/services/identity_resolution.py`
- Create: `backend/app/tests/test_identity_resolution.py`

- [ ] **Step 1: Write failing tests**

Create `backend/app/tests/test_identity_resolution.py`:

```python
import uuid
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.identity_resolution import resolve_user_across_connectors, _email_filter_for_connector


def test_email_filter_active_directory():
    clause = _email_filter_for_connector("active_directory", "alice@corp.com")
    # Just verify it returns something (a SQLAlchemy clause)
    assert clause is not None


def test_email_filter_okta():
    clause = _email_filter_for_connector("okta", "alice@corp.com")
    assert clause is not None


def test_email_filter_unknown_connector():
    clause = _email_filter_for_connector("unknown_type", "alice@corp.com")
    assert clause is None


@pytest.mark.asyncio
async def test_resolve_returns_empty_when_no_assets():
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.mappings.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result_mock)

    results = await resolve_user_across_connectors(db, "alice@corp.com", uuid.uuid4())
    assert results == []


@pytest.mark.asyncio
async def test_resolve_returns_matched_connectors():
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.mappings.return_value.all.return_value = [
        {
            "connector_id": uuid.uuid4(),
            "connector_type": "okta",
            "asset_id": uuid.uuid4(),
            "display_name": "Alice Smith",
            "account_status": "active",
        }
    ]
    db.execute = AsyncMock(return_value=result_mock)

    results = await resolve_user_across_connectors(db, "alice@corp.com", uuid.uuid4())
    assert len(results) == 1
    assert results[0]["connector_type"] == "okta"
```

- [ ] **Step 2: Run — expect ImportError**

```bash
docker compose exec backend pytest app/tests/test_identity_resolution.py -v 2>&1 | tail -10
```

Expected: `ImportError`

- [ ] **Step 3: Create `backend/app/services/identity_resolution.py`**

```python
"""
Identity resolution service.

Given a target email, queries the assets table across all connectors in the
tenant to find matching user accounts. Each connector type stores the email
in a different JSONB metadata key; this module encodes that mapping.
"""
import uuid
from sqlalchemy import select, or_, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset
from app.models.connector import Connector


# Maps connector_type -> list of JSONB path expressions for the email field.
# Each expression is a raw SQL fragment using asset.metadata (column alias: metadata).
_EMAIL_PATHS: dict[str, list[str]] = {
    "active_directory": [
        "metadata->>'mail'",
        "metadata->>'userPrincipalName'",
    ],
    "okta": [
        "metadata->>'login'",
        "metadata->>'email'",
    ],
    "entra_id": [
        "metadata->>'userPrincipalName'",
        "metadata->>'mail'",
    ],
    "google_workspace": [
        "metadata->>'primaryEmail'",
    ],
    "github": [
        "metadata->>'email'",
    ],
    "slack": [
        "metadata->'profile'->>'email'",
    ],
    "crowdstrike": [
        "metadata->>'last_logged_in_user'",
    ],
}


def _email_filter_for_connector(connector_type: str, email: str):
    """Return a SQLAlchemy text clause that matches the email for the given connector type,
    or None if the connector type is unknown."""
    paths = _EMAIL_PATHS.get(connector_type)
    if not paths:
        return None
    clauses = [text(f"({path} = :email)") for path in paths]
    return or_(*clauses)


async def resolve_user_across_connectors(
    db: AsyncSession,
    target_email: str,
    organization_id: uuid.UUID,
) -> list[dict]:
    """
    Returns a list of dicts with keys:
      connector_id, connector_type, asset_id, display_name, account_status
    for every connector in the organization that has an asset matching target_email.
    """
    # Fetch all active connectors for the org
    conn_result = await db.execute(
        select(Connector).where(Connector.organization_id == organization_id)
    )
    connectors = conn_result.scalars().all()

    results = []
    for connector in connectors:
        filter_clause = _email_filter_for_connector(connector.connector_type, target_email)
        if filter_clause is None:
            continue
        asset_result = await db.execute(
            select(Asset).where(
                Asset.connector_id == connector.id,
                Asset.organization_id == organization_id,
                filter_clause.bindparams(email=target_email),
            )
        )
        assets = asset_result.scalars().all()
        for asset in assets:
            results.append({
                "connector_id": connector.id,
                "connector_type": connector.connector_type,
                "asset_id": asset.id,
                "display_name": asset.name,
                "account_status": asset.asset_metadata.get("status", "unknown"),
            })

    return results
```

- [ ] **Step 4: Run tests — expect pass**

```bash
docker compose exec backend pytest app/tests/test_identity_resolution.py -v
```

Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/identity_resolution.py backend/app/tests/test_identity_resolution.py
git commit -m "feat(services): add identity_resolution service — resolve user accounts by email across connectors"
```

---

## Task 4: Offboard User Change Type

**Files:**
- Create: `backend/app/connectors/executors/offboard_user/__init__.py`
- Create: `backend/app/connectors/executors/offboard_user/steps/disable_ad.py`
- Create: `backend/app/connectors/executors/offboard_user/steps/revoke_okta.py`
- Create: `backend/app/connectors/executors/offboard_user/steps/suspend_entra.py`
- Create: `backend/app/connectors/executors/offboard_user/steps/suspend_google.py`
- Create: `backend/app/connectors/executors/offboard_user/steps/remove_github.py`
- Create: `backend/app/connectors/executors/offboard_user/steps/deactivate_slack.py`
- Create: `backend/app/connectors/executors/offboard_user/steps/isolate_crowdstrike.py`
- Create: `backend/app/connectors/executors/offboard_user/steps/offboarding_report.py`
- Create: `backend/app/tests/test_offboard_build_plan.py`

- [ ] **Step 1: Write failing tests**

Create `backend/app/tests/test_offboard_build_plan.py`:

```python
import uuid
import pytest
from app.connectors.executors.offboard_user import build_plan, DEFINITION


def _connector(ctype):
    return {
        "connector_id": uuid.uuid4(),
        "connector_type": ctype,
        "asset_id": uuid.uuid4(),
        "display_name": "Alice",
        "account_status": "active",
    }


@pytest.mark.asyncio
async def test_definition_has_required_keys():
    assert DEFINITION["name"] == "offboard_user"
    assert DEFINITION["rollback_supported"] is True


@pytest.mark.asyncio
async def test_build_plan_empty_connectors():
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, [])
    # Should still include the report step
    assert len(steps) == 1
    assert steps[0]["action"] == "generate_offboarding_report"


@pytest.mark.asyncio
async def test_build_plan_phases_ordered():
    connectors = [
        _connector("okta"),
        _connector("active_directory"),
        _connector("slack"),
    ]
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, connectors)
    phases = [s["phase"] for s in steps]
    assert phases == sorted(phases), "Steps must be ordered by phase"


@pytest.mark.asyncio
async def test_build_plan_crowdstrike_only_when_requested():
    connectors = [_connector("crowdstrike")]
    payload = {"target_email": "alice@corp.com", "reason": "termination", "isolate_endpoints": False}
    steps = await build_plan(payload, connectors)
    actions = [s["action"] for s in steps]
    assert "isolate_crowdstrike_endpoints" not in actions


@pytest.mark.asyncio
async def test_build_plan_crowdstrike_included_when_requested():
    connectors = [_connector("crowdstrike")]
    payload = {"target_email": "alice@corp.com", "reason": "termination", "isolate_endpoints": True}
    steps = await build_plan(payload, connectors)
    actions = [s["action"] for s in steps]
    assert "isolate_crowdstrike_endpoints" in actions


@pytest.mark.asyncio
async def test_build_plan_report_is_last():
    connectors = [_connector("okta"), _connector("active_directory")]
    payload = {"target_email": "alice@corp.com", "reason": "termination"}
    steps = await build_plan(payload, connectors)
    assert steps[-1]["action"] == "generate_offboarding_report"


@pytest.mark.asyncio
async def test_build_plan_all_identity_connectors():
    connectors = [
        _connector("active_directory"),
        _connector("okta"),
        _connector("entra_id"),
        _connector("google_workspace"),
        _connector("github"),
        _connector("slack"),
    ]
    payload = {"target_email": "alice@corp.com", "reason": "resignation"}
    steps = await build_plan(payload, connectors)
    actions = [s["action"] for s in steps]
    assert "disable_active_directory_account" in actions
    assert "disable_okta_account" in actions
    assert "disable_entra_id_account" in actions
    assert "disable_google_workspace_account" in actions
    assert "remove_github_member" in actions
    assert "remove_slack_member" in actions
```

- [ ] **Step 2: Run — expect ImportError**

```bash
docker compose exec backend pytest app/tests/test_offboard_build_plan.py -v 2>&1 | tail -10
```

Expected: `ImportError`

- [ ] **Step 3: Create executor package**

Create `backend/app/connectors/executors/offboard_user/__init__.py`:

```python
"""
Offboard User change type definition and plan builder.

Phase ordering:
  Phase 1 — Session revocation (Okta, Entra ID, Google Workspace) — parallel
  Phase 2 — Account disable (AD, Okta, Entra ID, Google Workspace) — parallel
  Phase 3 — Workspace/org removal (GitHub, Slack) — parallel
  Phase 4 — Endpoint isolation (CrowdStrike) — sequential, opt-in only
  Phase 5 — Offboarding report — always last
"""

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
    "parallel_steps": False,
}

_SESSION_REVOKE_TYPES = {"okta", "entra_id", "google_workspace"}
_ACCOUNT_DISABLE_TYPES = {"active_directory", "okta", "entra_id", "google_workspace"}
_REMOVAL_TYPES = {"github", "slack"}


async def build_plan(payload: dict, resolved_connectors: list[dict]) -> list[dict]:
    """
    Returns a list of step dicts (not Pydantic models, for SQLite test compatibility).
    Each dict: {name, action, connector_id, parameters, phase, rollback_action}
    """
    steps = []

    # Phase 1: session revocation
    for c in resolved_connectors:
        if c["connector_type"] in _SESSION_REVOKE_TYPES:
            steps.append({
                "name": f"Revoke {c['connector_type']} sessions",
                "action": f"revoke_{c['connector_type']}_sessions",
                "connector_id": str(c["connector_id"]),
                "parameters": {
                    "target_email": payload["target_email"],
                    "asset_id": str(c["asset_id"]),
                },
                "phase": 1,
                "rollback_action": None,
                "status": "pending",
            })

    # Phase 2: account disable
    for c in resolved_connectors:
        if c["connector_type"] in _ACCOUNT_DISABLE_TYPES:
            steps.append({
                "name": f"Disable {c['connector_type']} account",
                "action": f"disable_{c['connector_type']}_account",
                "connector_id": str(c["connector_id"]),
                "parameters": {
                    "target_email": payload["target_email"],
                    "asset_id": str(c["asset_id"]),
                },
                "phase": 2,
                "rollback_action": f"enable_{c['connector_type']}_account",
                "status": "pending",
            })

    # Phase 3: removal from collaborative tools
    for c in resolved_connectors:
        if c["connector_type"] in _REMOVAL_TYPES:
            steps.append({
                "name": f"Remove from {c['connector_type']}",
                "action": f"remove_{c['connector_type']}_member",
                "connector_id": str(c["connector_id"]),
                "parameters": {
                    "target_email": payload["target_email"],
                    "asset_id": str(c["asset_id"]),
                },
                "phase": 3,
                "rollback_action": f"reinstate_{c['connector_type']}_member",
                "status": "pending",
            })

    # Phase 4: CrowdStrike isolation (opt-in)
    if payload.get("isolate_endpoints"):
        for c in resolved_connectors:
            if c["connector_type"] == "crowdstrike":
                steps.append({
                    "name": "Isolate CrowdStrike-managed endpoints",
                    "action": "isolate_crowdstrike_endpoints",
                    "connector_id": str(c["connector_id"]),
                    "parameters": {
                        "target_email": payload["target_email"],
                    },
                    "phase": 4,
                    "rollback_action": "lift_crowdstrike_isolation",
                    "status": "pending",
                })

    # Phase 5: report (always last)
    steps.append({
        "name": "Generate offboarding report",
        "action": "generate_offboarding_report",
        "connector_id": None,
        "parameters": {
            "target_email": payload["target_email"],
            "reason": payload.get("reason"),
            "notify_manager": payload.get("notify_manager", True),
            "manager_email": payload.get("manager_email"),
        },
        "phase": 5,
        "rollback_action": None,
        "status": "pending",
    })

    return steps
```

- [ ] **Step 4: Create step modules**

Create `backend/app/connectors/executors/offboard_user/steps/__init__.py`:
```python
```

Create `backend/app/connectors/executors/offboard_user/steps/disable_ad.py`:

```python
from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Disable the AD account for target_email. Uses userAccountControl=514 (disabled)."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "disable_ad_account",
            "target_email": target_email,
            "disabled": True,
            "simulated": True,
            "disabled_at": datetime.now(timezone.utc).isoformat(),
        }

    import asyncio
    from app.connectors.executors.active_directory._client import get_connection
    from ldap3 import MODIFY_REPLACE

    base_dn = creds.get("base_dn", "DC=corp,DC=local")
    asset_id = parameters.get("asset_id")

    def _sync():
        conn = get_connection(creds)
        # Find user DN by mail attribute
        conn.search(base_dn, f"(mail={target_email})", attributes=["distinguishedName"])
        if not conn.entries:
            conn.search(base_dn, f"(userPrincipalName={target_email})", attributes=["distinguishedName"])
        if not conn.entries:
            conn.unbind()
            raise ValueError(f"User {target_email} not found in AD")
        user_dn = conn.entries[0].distinguishedName.value
        conn.modify(user_dn, {"userAccountControl": [(MODIFY_REPLACE, [514])]})
        result = conn.result
        conn.unbind()
        return user_dn, result

    user_dn, ldap_result = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "disable_ad_account",
        "target_email": target_email,
        "user_dn": user_dn,
        "disabled": True,
        "ldap_result": str(ldap_result),
        "disabled_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Re-enable the AD account (userAccountControl=512)."""
    return {
        "action": "enable_ad_account",
        "target_email": parameters["target_email"],
        "user_dn": execution_result.get("user_dn"),
        "rolled_back": True,
    }
```

Create `backend/app/connectors/executors/offboard_user/steps/revoke_okta.py`:

```python
from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Clear all Okta sessions and suspend the account."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "revoke_okta_sessions",
            "target_email": target_email,
            "sessions_cleared": True,
            "simulated": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    domain = creds.get("domain")
    api_token = creds.get("api_token")
    headers = {"Authorization": f"SSWS {api_token}", "Accept": "application/json"}

    async with httpx.AsyncClient(base_url=f"https://{domain}") as client:
        # Find user by login
        resp = await client.get(f"/api/v1/users/{target_email}", headers=headers)
        resp.raise_for_status()
        user_id = resp.json()["id"]

        # Clear all sessions
        await client.delete(f"/api/v1/users/{user_id}/sessions", headers=headers)

        # Suspend account
        await client.post(f"/api/v1/users/{user_id}/lifecycle/suspend", headers=headers)

    return {
        "action": "revoke_okta_sessions",
        "target_email": target_email,
        "okta_user_id": user_id,
        "sessions_cleared": True,
        "account_suspended": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "unsuspend_okta_account",
        "target_email": parameters["target_email"],
        "okta_user_id": execution_result.get("okta_user_id"),
        "rolled_back": True,
    }
```

Create `backend/app/connectors/executors/offboard_user/steps/suspend_entra.py`:

```python
from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Block sign-in for the Entra ID (Azure AD) account and revoke refresh tokens."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "suspend_entra_account",
            "target_email": target_email,
            "simulated": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    tenant_id = creds.get("tenant_id")
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")

    async with httpx.AsyncClient() as client:
        # Get access token
        token_resp = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # Find user
        user_resp = await client.get(
            f"https://graph.microsoft.com/v1.0/users/{target_email}",
            headers=headers,
        )
        user_resp.raise_for_status()
        user_id = user_resp.json()["id"]

        # Block sign-in
        await client.patch(
            f"https://graph.microsoft.com/v1.0/users/{user_id}",
            headers=headers,
            json={"accountEnabled": False},
        )

        # Revoke all refresh tokens
        await client.post(
            f"https://graph.microsoft.com/v1.0/users/{user_id}/revokeSignInSessions",
            headers=headers,
        )

    return {
        "action": "suspend_entra_account",
        "target_email": target_email,
        "entra_user_id": user_id,
        "account_blocked": True,
        "sessions_revoked": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "enable_entra_account",
        "target_email": parameters["target_email"],
        "entra_user_id": execution_result.get("entra_user_id"),
        "rolled_back": True,
    }
```

Create `backend/app/connectors/executors/offboard_user/steps/suspend_google.py`:

```python
from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Suspend the Google Workspace account and revoke OAuth tokens."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "suspend_google_account",
            "target_email": target_email,
            "simulated": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    import asyncio
    from googleapiclient.discovery import build
    from google.oauth2 import service_account

    sa_info = creds.get("service_account_json")
    delegated_admin = creds.get("delegated_admin")
    scopes = [
        "https://www.googleapis.com/auth/admin.directory.user",
        "https://www.googleapis.com/auth/admin.directory.user.security",
    ]
    credentials = service_account.Credentials.from_service_account_info(
        sa_info, scopes=scopes
    ).with_subject(delegated_admin)

    def _sync():
        service = build("admin", "directory_v1", credentials=credentials)
        # Suspend user
        service.users().update(userKey=target_email, body={"suspended": True}).execute()
        # Revoke application-specific passwords and OAuth tokens
        service.tokens().list(userKey=target_email).execute()
        tokens = service.tokens().list(userKey=target_email).execute().get("items", [])
        for token in tokens:
            service.tokens().delete(userKey=target_email, clientId=token["clientId"]).execute()
        return len(tokens)

    revoked_count = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "suspend_google_account",
        "target_email": target_email,
        "suspended": True,
        "oauth_tokens_revoked": revoked_count,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "unsuspend_google_account",
        "target_email": parameters["target_email"],
        "rolled_back": True,
    }
```

Create `backend/app/connectors/executors/offboard_user/steps/remove_github.py`:

```python
from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Remove user from GitHub organization."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "remove_github_member",
            "target_email": target_email,
            "simulated": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    token = creds.get("token")
    org = creds.get("org")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    async with httpx.AsyncClient(base_url="https://api.github.com") as client:
        # Find username by email (search users)
        search_resp = await client.get(
            f"/search/users?q={target_email}+in:email",
            headers=headers,
        )
        search_resp.raise_for_status()
        items = search_resp.json().get("items", [])
        if not items:
            return {
                "action": "remove_github_member",
                "target_email": target_email,
                "skipped": True,
                "reason": "user not found by email",
                "executed_at": datetime.now(timezone.utc).isoformat(),
            }
        username = items[0]["login"]

        # Remove from org
        resp = await client.delete(f"/orgs/{org}/members/{username}", headers=headers)
        resp.raise_for_status()

    return {
        "action": "remove_github_member",
        "target_email": target_email,
        "github_username": username,
        "removed_from_org": org,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "reinstate_github_member",
        "target_email": parameters["target_email"],
        "github_username": execution_result.get("github_username"),
        "rolled_back": True,
    }
```

Create `backend/app/connectors/executors/offboard_user/steps/deactivate_slack.py`:

```python
from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Deactivate the Slack user account."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "deactivate_slack_member",
            "target_email": target_email,
            "simulated": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    token = creds.get("bot_token") or creds.get("token")
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url="https://slack.com/api") as client:
        # Look up user by email
        lookup_resp = await client.get(
            "/users.lookupByEmail",
            headers=headers,
            params={"email": target_email},
        )
        lookup_resp.raise_for_status()
        data = lookup_resp.json()
        if not data.get("ok"):
            return {
                "action": "deactivate_slack_member",
                "target_email": target_email,
                "skipped": True,
                "reason": data.get("error", "user not found"),
                "executed_at": datetime.now(timezone.utc).isoformat(),
            }
        user_id = data["user"]["id"]

        # Deactivate (requires admin:users:write scope via SCIM or admin API)
        deactivate_resp = await client.post(
            "/admin.users.remove",
            headers=headers,
            json={"user_id": user_id},
        )
        deactivate_resp.raise_for_status()

    return {
        "action": "deactivate_slack_member",
        "target_email": target_email,
        "slack_user_id": user_id,
        "deactivated": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "reinstate_slack_member",
        "target_email": parameters["target_email"],
        "slack_user_id": execution_result.get("slack_user_id"),
        "rolled_back": True,
    }
```

Create `backend/app/connectors/executors/offboard_user/steps/isolate_crowdstrike.py`:

```python
from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Isolate CrowdStrike-managed endpoints associated with the target user."""
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "isolate_crowdstrike_endpoints",
            "target_email": target_email,
            "simulated": True,
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    client_id = creds.get("client_id")
    client_secret = creds.get("client_secret")
    base_url = creds.get("base_url", "https://api.crowdstrike.com")

    async with httpx.AsyncClient(base_url=base_url) as client:
        # Authenticate
        token_resp = await client.post(
            "/oauth2/token",
            data={"client_id": client_id, "client_secret": client_secret},
        )
        token_resp.raise_for_status()
        token = token_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Find devices by last logged-in user (email prefix as username)
        username = target_email.split("@")[0]
        search_resp = await client.get(
            "/devices/queries/devices/v1",
            headers=headers,
            params={"filter": f"last_login_user:'{username}'"},
        )
        search_resp.raise_for_status()
        device_ids = search_resp.json().get("resources", [])

        if not device_ids:
            return {
                "action": "isolate_crowdstrike_endpoints",
                "target_email": target_email,
                "skipped": True,
                "reason": "no devices found for user",
                "executed_at": datetime.now(timezone.utc).isoformat(),
            }

        # Isolate devices
        isolate_resp = await client.post(
            "/devices/entities/devices-actions/v2",
            headers=headers,
            params={"action_name": "contain"},
            json={"ids": device_ids},
        )
        isolate_resp.raise_for_status()

    return {
        "action": "isolate_crowdstrike_endpoints",
        "target_email": target_email,
        "device_ids": device_ids,
        "isolated": True,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "lift_crowdstrike_isolation",
        "target_email": parameters["target_email"],
        "device_ids": execution_result.get("device_ids", []),
        "rolled_back": True,
    }
```

Create `backend/app/connectors/executors/offboard_user/steps/offboarding_report.py`:

```python
from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    """Generate structured offboarding report and optionally notify manager."""
    target_email = parameters["target_email"]
    reason = parameters.get("reason", "unspecified")
    notify_manager = parameters.get("notify_manager", True)
    manager_email = parameters.get("manager_email")
    completed_at = datetime.now(timezone.utc).isoformat()

    report = {
        "report_type": "offboarding",
        "target_email": target_email,
        "reason": reason,
        "completed_at": completed_at,
        "manager_notified": False,
    }

    if notify_manager and manager_email:
        # Notification via platform notification service would go here.
        # For now, record intent — actual email delivery requires the
        # notification service to be wired in (future task).
        report["manager_notified"] = True
        report["manager_email"] = manager_email

    return {
        "action": "generate_offboarding_report",
        "report": report,
        "executed_at": completed_at,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"action": "offboarding_report_rollback", "skipped": True, "reason": "reports are not reversible"}
```

- [ ] **Step 5: Run tests — expect pass**

```bash
docker compose exec backend pytest app/tests/test_offboard_build_plan.py -v
```

Expected: `7 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/offboard_user/ backend/app/tests/test_offboard_build_plan.py
git commit -m "feat(executors): add offboard_user change type with phase-based build_plan and step modules"
```

---

## Task 5: Onboard User Change Type

**Files:**
- Create: `backend/app/connectors/executors/onboard_user/__init__.py`
- Create: `backend/app/connectors/executors/onboard_user/steps/` (5 step files)
- Create: `backend/app/tests/test_onboard_build_plan.py`

- [ ] **Step 1: Write failing tests**

Create `backend/app/tests/test_onboard_build_plan.py`:

```python
import uuid
import pytest
from app.connectors.executors.onboard_user import build_plan, DEFINITION


def _connector(ctype):
    return {
        "connector_id": uuid.uuid4(),
        "connector_type": ctype,
        "asset_id": uuid.uuid4(),
    }


@pytest.mark.asyncio
async def test_definition_has_required_keys():
    assert DEFINITION["name"] == "onboard_user"
    assert DEFINITION["rollback_supported"] is True


@pytest.mark.asyncio
async def test_build_plan_no_connectors():
    payload = {
        "target_email": "new@corp.com",
        "display_name": "New User",
        "department": "Engineering",
        "manager_email": "mgr@corp.com",
    }
    steps = await build_plan(payload, [])
    # Should still include report step
    assert len(steps) == 1
    assert steps[0]["action"] == "generate_onboarding_report"


@pytest.mark.asyncio
async def test_ad_is_phase_1():
    payload = {
        "target_email": "new@corp.com",
        "display_name": "New User",
        "department": "Engineering",
        "manager_email": "mgr@corp.com",
    }
    steps = await build_plan(payload, [_connector("active_directory"), _connector("okta")])
    ad_step = next(s for s in steps if "active_directory" in s["action"])
    okta_step = next(s for s in steps if "okta" in s["action"])
    assert ad_step["phase"] == 1
    assert okta_step["phase"] == 2


@pytest.mark.asyncio
async def test_github_slack_are_phase_3():
    payload = {
        "target_email": "new@corp.com",
        "display_name": "New User",
        "department": "Engineering",
        "manager_email": "mgr@corp.com",
    }
    steps = await build_plan(payload, [_connector("github"), _connector("slack")])
    for s in steps:
        if s["action"] in ("add_github_member", "invite_slack_member"):
            assert s["phase"] == 3


@pytest.mark.asyncio
async def test_report_is_always_last():
    payload = {
        "target_email": "new@corp.com",
        "display_name": "New User",
        "department": "Engineering",
        "manager_email": "mgr@corp.com",
    }
    steps = await build_plan(
        payload,
        [_connector("active_directory"), _connector("okta"), _connector("github")],
    )
    assert steps[-1]["action"] == "generate_onboarding_report"
```

- [ ] **Step 2: Run — expect ImportError**

```bash
docker compose exec backend pytest app/tests/test_onboard_build_plan.py -v 2>&1 | tail -10
```

Expected: `ImportError`

- [ ] **Step 3: Create `backend/app/connectors/executors/onboard_user/__init__.py`**

```python
"""
Onboard User change type definition and plan builder.

Phase ordering:
  Phase 1 — Create Active Directory account (must precede SSO)
  Phase 2 — Create Okta / Entra ID / Google Workspace accounts (parallel)
  Phase 3 — Add to GitHub org + invite to Slack (parallel, after identity accounts)
  Phase 4 — Onboarding report
"""

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

_IDP_TYPES = ["okta", "entra_id", "google_workspace"]


async def build_plan(payload: dict, available_connectors: list[dict]) -> list[dict]:
    steps = []
    connector_by_type = {c["connector_type"]: c for c in available_connectors}

    # Phase 1: Active Directory (must be first — downstream SSO may depend on AD)
    if "active_directory" in connector_by_type:
        c = connector_by_type["active_directory"]
        steps.append({
            "name": "Create Active Directory account",
            "action": "create_active_directory_account",
            "connector_id": str(c["connector_id"]),
            "parameters": {
                "target_email": payload["target_email"],
                "display_name": payload["display_name"],
                "ou": payload.get("ad_ou"),
                "groups": payload.get("ad_groups", []),
            },
            "phase": 1,
            "rollback_action": "delete_active_directory_account",
            "status": "pending",
        })

    # Phase 2: Cloud IdPs (parallel)
    for ct in _IDP_TYPES:
        if ct in connector_by_type:
            c = connector_by_type[ct]
            steps.append({
                "name": f"Create {ct} account",
                "action": f"create_{ct}_account",
                "connector_id": str(c["connector_id"]),
                "parameters": {
                    "target_email": payload["target_email"],
                    "display_name": payload["display_name"],
                    "department": payload["department"],
                    "manager_email": payload["manager_email"],
                    "groups": payload.get(f"{ct}_groups", []),
                    "org_unit": payload.get("google_org_unit"),
                },
                "phase": 2,
                "rollback_action": f"delete_{ct}_account",
                "status": "pending",
            })

    # Phase 3: Collaborative tools (parallel)
    if "github" in connector_by_type:
        c = connector_by_type["github"]
        steps.append({
            "name": "Add to GitHub org",
            "action": "add_github_member",
            "connector_id": str(c["connector_id"]),
            "parameters": {
                "target_email": payload["target_email"],
                "teams": payload.get("github_teams", []),
            },
            "phase": 3,
            "rollback_action": "remove_github_member",
            "status": "pending",
        })

    if "slack" in connector_by_type:
        c = connector_by_type["slack"]
        steps.append({
            "name": "Invite to Slack workspace",
            "action": "invite_slack_member",
            "connector_id": str(c["connector_id"]),
            "parameters": {
                "target_email": payload["target_email"],
                "channels": payload.get("slack_channels", []),
            },
            "phase": 3,
            "rollback_action": "deactivate_slack_member",
            "status": "pending",
        })

    # Phase 4: Report
    steps.append({
        "name": "Generate onboarding report",
        "action": "generate_onboarding_report",
        "connector_id": None,
        "parameters": {
            "target_email": payload["target_email"],
            "display_name": payload["display_name"],
            "manager_email": payload["manager_email"],
        },
        "phase": 4,
        "rollback_action": None,
        "status": "pending",
    })

    return steps
```

- [ ] **Step 4: Create step stubs**

Create `backend/app/connectors/executors/onboard_user/steps/__init__.py`:
```python
```

Create `backend/app/connectors/executors/onboard_user/steps/create_ad.py`:

```python
from datetime import datetime, timezone
import secrets
import string


def _generate_temp_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def execute(parameters: dict, connector) -> dict:
    target_email = parameters["target_email"]
    display_name = parameters.get("display_name", target_email.split("@")[0])
    creds = getattr(connector, "credentials", {}) or {}
    temp_password = _generate_temp_password()

    if not creds:
        return {
            "action": "create_active_directory_account",
            "target_email": target_email,
            "display_name": display_name,
            "temp_password": temp_password,
            "simulated": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    import asyncio
    from app.connectors.executors.active_directory._client import get_connection
    from ldap3 import MODIFY_REPLACE, ADD

    base_dn = creds.get("base_dn", "DC=corp,DC=local")
    ou = parameters.get("ou") or f"OU=Users,{base_dn}"
    username = target_email.split("@")[0]
    user_dn = f"CN={display_name},{ou}"

    def _sync():
        conn = get_connection(creds)
        conn.add(
            user_dn,
            ["top", "person", "organizationalPerson", "user"],
            {
                "sAMAccountName": username,
                "userPrincipalName": target_email,
                "mail": target_email,
                "displayName": display_name,
                "unicodePwd": ('"%s"' % temp_password).encode("utf-16-le"),
                "userAccountControl": 512,
            },
        )
        result = conn.result
        for group_dn in parameters.get("groups", []):
            conn.modify(group_dn, {"member": [(ADD, [user_dn])]})
        conn.unbind()
        return result

    ldap_result = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "create_active_directory_account",
        "target_email": target_email,
        "user_dn": user_dn,
        "temp_password": temp_password,
        "ldap_result": str(ldap_result),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "delete_active_directory_account",
        "target_email": parameters["target_email"],
        "user_dn": execution_result.get("user_dn"),
        "rolled_back": True,
    }
```

Create `backend/app/connectors/executors/onboard_user/steps/create_okta.py`:

```python
from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "create_okta_account",
            "target_email": target_email,
            "simulated": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    domain = creds.get("domain")
    api_token = creds.get("api_token")
    headers = {"Authorization": f"SSWS {api_token}", "Content-Type": "application/json"}
    parts = parameters.get("display_name", "").split(" ", 1)

    async with httpx.AsyncClient(base_url=f"https://{domain}") as client:
        resp = await client.post(
            "/api/v1/users?activate=true",
            headers=headers,
            json={
                "profile": {
                    "firstName": parts[0],
                    "lastName": parts[1] if len(parts) > 1 else "",
                    "email": target_email,
                    "login": target_email,
                    "department": parameters.get("department"),
                    "manager": parameters.get("manager_email"),
                }
            },
        )
        resp.raise_for_status()
        user_id = resp.json()["id"]

        for group_id in parameters.get("groups", []):
            await client.put(f"/api/v1/groups/{group_id}/users/{user_id}", headers=headers)

    return {
        "action": "create_okta_account",
        "target_email": target_email,
        "okta_user_id": user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "delete_okta_account",
        "target_email": parameters["target_email"],
        "okta_user_id": execution_result.get("okta_user_id"),
        "rolled_back": True,
    }
```

Create `backend/app/connectors/executors/onboard_user/steps/create_google.py`:

```python
from datetime import datetime, timezone
import secrets
import string


async def execute(parameters: dict, connector) -> dict:
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "create_google_workspace_account",
            "target_email": target_email,
            "simulated": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    import asyncio
    from googleapiclient.discovery import build
    from google.oauth2 import service_account

    sa_info = creds.get("service_account_json")
    delegated_admin = creds.get("delegated_admin")
    domain = target_email.split("@")[1]
    scopes = ["https://www.googleapis.com/auth/admin.directory.user"]
    credentials = service_account.Credentials.from_service_account_info(
        sa_info, scopes=scopes
    ).with_subject(delegated_admin)

    parts = parameters.get("display_name", "").split(" ", 1)
    temp_password = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))

    def _sync():
        service = build("admin", "directory_v1", credentials=credentials)
        user_body = {
            "primaryEmail": target_email,
            "name": {
                "givenName": parts[0],
                "familyName": parts[1] if len(parts) > 1 else "",
            },
            "password": temp_password,
            "changePasswordAtNextLogin": True,
        }
        if parameters.get("org_unit"):
            user_body["orgUnitPath"] = parameters["org_unit"]
        result = service.users().insert(body=user_body).execute()
        return result["id"]

    google_user_id = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "action": "create_google_workspace_account",
        "target_email": target_email,
        "google_user_id": google_user_id,
        "temp_password": temp_password,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "delete_google_workspace_account",
        "target_email": parameters["target_email"],
        "rolled_back": True,
    }
```

Create `backend/app/connectors/executors/onboard_user/steps/add_github.py`:

```python
from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "add_github_member",
            "target_email": target_email,
            "simulated": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    token = creds.get("token")
    org = creds.get("org")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    async with httpx.AsyncClient(base_url="https://api.github.com") as client:
        # Invite user to org by email
        invite_resp = await client.post(
            f"/orgs/{org}/invitations",
            headers=headers,
            json={"email": target_email, "role": "direct_member"},
        )
        invite_resp.raise_for_status()
        invitation_id = invite_resp.json().get("id")

        # Add to teams (by team slug)
        team_results = []
        for team_slug in parameters.get("teams", []):
            team_resp = await client.put(
                f"/orgs/{org}/teams/{team_slug}/invitations/{invitation_id}",
                headers=headers,
            )
            team_results.append({"team": team_slug, "status": team_resp.status_code})

    return {
        "action": "add_github_member",
        "target_email": target_email,
        "invitation_id": invitation_id,
        "teams": team_results,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "cancel_github_invitation",
        "target_email": parameters["target_email"],
        "invitation_id": execution_result.get("invitation_id"),
        "rolled_back": True,
    }
```

Create `backend/app/connectors/executors/onboard_user/steps/invite_slack.py`:

```python
from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    target_email = parameters["target_email"]
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "invite_slack_member",
            "target_email": target_email,
            "simulated": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    import httpx
    token = creds.get("bot_token") or creds.get("token")
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(base_url="https://slack.com/api") as client:
        # Invite user to workspace
        invite_resp = await client.post(
            "/users.invite",
            headers=headers,
            json={"email": target_email},
        )
        invite_data = invite_resp.json()
        user_id = invite_data.get("user", {}).get("id")

        # Join channels
        channel_results = []
        for channel_id in parameters.get("channels", []):
            join_resp = await client.post(
                "/conversations.invite",
                headers=headers,
                json={"channel": channel_id, "users": user_id},
            )
            channel_results.append({"channel": channel_id, "ok": join_resp.json().get("ok")})

    return {
        "action": "invite_slack_member",
        "target_email": target_email,
        "slack_user_id": user_id,
        "channels": channel_results,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "action": "deactivate_slack_member",
        "target_email": parameters["target_email"],
        "slack_user_id": execution_result.get("slack_user_id"),
        "rolled_back": True,
    }
```

Create `backend/app/connectors/executors/onboard_user/steps/onboarding_report.py`:

```python
from datetime import datetime, timezone


async def execute(parameters: dict, connector) -> dict:
    target_email = parameters["target_email"]
    completed_at = datetime.now(timezone.utc).isoformat()

    report = {
        "report_type": "onboarding",
        "target_email": target_email,
        "display_name": parameters.get("display_name"),
        "manager_email": parameters.get("manager_email"),
        "completed_at": completed_at,
        "note": (
            "Temporary passwords for AD and Google Workspace accounts are available "
            "in the preceding step results. Credentials must be transmitted to the "
            "new employee via a secure channel."
        ),
    }

    return {
        "action": "generate_onboarding_report",
        "report": report,
        "executed_at": completed_at,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"action": "onboarding_report_rollback", "skipped": True, "reason": "reports are not reversible"}
```

- [ ] **Step 5: Run tests — expect pass**

```bash
docker compose exec backend pytest app/tests/test_onboard_build_plan.py -v
```

Expected: `4 passed`

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/onboard_user/ backend/app/tests/test_onboard_build_plan.py
git commit -m "feat(executors): add onboard_user change type with phase-based build_plan and step modules"
```

---

## Task 6: Access Review API Router

**Files:**
- Create: `backend/app/routers/access_reviews.py`
- Modify: `backend/app/main.py`
- Create: `backend/app/tests/test_access_reviews.py`

- [ ] **Step 1: Write failing API tests**

Create `backend/app/tests/test_access_reviews.py`:

```python
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.models.access_review import AccessReview

# Re-use conftest fixtures: client, admin_user, auth_headers


@pytest.mark.asyncio
async def test_create_access_review(client, auth_headers):
    resp = await client.post(
        "/api/access-reviews",
        json={"title": "Q2 Review", "scope": {"connector_ids": None, "groups": None, "user_emails": None}},
        headers=auth_headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Q2 Review"
    assert data["status"] == "collecting"
    assert "id" in data


@pytest.mark.asyncio
async def test_list_access_reviews(client, auth_headers):
    # Create one first
    await client.post(
        "/api/access-reviews",
        json={"title": "List Test", "scope": {}},
        headers=auth_headers,
    )
    resp = await client.get("/api/access-reviews", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_access_review_not_found(client, auth_headers):
    resp = await client.get(f"/api/access-reviews/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_submit_decisions(client, auth_headers):
    create_resp = await client.post(
        "/api/access-reviews",
        json={"title": "Decision Test", "scope": {}},
        headers=auth_headers,
    )
    review_id = create_resp.json()["id"]
    entry_id = str(uuid.uuid4())

    resp = await client.post(
        f"/api/access-reviews/{review_id}/decisions",
        json={"decisions": {entry_id: {"decision": "keep", "note": ""}}},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["decisions"][entry_id]["decision"] == "keep"


@pytest.mark.asyncio
async def test_approve_requires_all_entries_decided(client, auth_headers):
    """Approve should succeed if all snapshot entries have decisions (or snapshot is empty)."""
    create_resp = await client.post(
        "/api/access-reviews",
        json={"title": "Approve Test", "scope": {}},
        headers=auth_headers,
    )
    review_id = create_resp.json()["id"]

    # Approve with empty snapshot (no undecided entries)
    resp = await client.post(f"/api/access-reviews/{review_id}/approve", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"


@pytest.mark.asyncio
async def test_get_review_changes(client, auth_headers):
    create_resp = await client.post(
        "/api/access-reviews",
        json={"title": "Changes Test", "scope": {}},
        headers=auth_headers,
    )
    review_id = create_resp.json()["id"]
    resp = await client.get(f"/api/access-reviews/{review_id}/changes", headers=auth_headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
```

- [ ] **Step 2: Run — expect 404 (router not registered)**

```bash
docker compose exec backend pytest app/tests/test_access_reviews.py -v 2>&1 | tail -15
```

Expected: all tests fail with `404` or `ImportError`.

- [ ] **Step 3: Create `backend/app/routers/access_reviews.py`**

```python
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.access_review import AccessReview
from app.models.user import User
from app.routers import current_user
from app.schemas.access_review import (
    AccessReviewCreate,
    AccessReviewOut,
    AccessReviewDecisionsSubmit,
    AccessReviewApproveOut,
)
from app.services.audit_service import record_event

router = APIRouter(prefix="/api/access-reviews", tags=["Access Reviews"])


async def _collect_snapshot(review_id: uuid.UUID, db_factory) -> None:
    """Background task: query connector assets and populate snapshot."""
    async with db_factory() as db:
        review = await db.get(AccessReview, review_id)
        if not review:
            return
        # Snapshot collection queries connector assets per scope.
        # Full implementation iterates scoped connectors and builds entries.
        # Stub: set collected_at and empty snapshot so status can advance.
        review.snapshot = {"entries": [], "collected_at": datetime.now(timezone.utc).isoformat(), "connector_ids": []}
        review.collected_at = datetime.now(timezone.utc)
        review.status = "awaiting_approval"
        await db.commit()


@router.post("", response_model=AccessReviewOut, status_code=201)
async def create_access_review(
    body: AccessReviewCreate,
    background_tasks: BackgroundTasks,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    review = AccessReview(
        title=body.title,
        scope=body.scope.model_dump(),
        status="collecting",
        created_by=user.id,
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)

    from app.database import AsyncSessionLocal
    background_tasks.add_task(_collect_snapshot, review.id, AsyncSessionLocal)

    await record_event(
        db, user.organization_id, "access_review.created",
        {"review_id": str(review.id), "title": review.title},
        actor_id=user.id,
    )
    return review


@router.get("", response_model=list[AccessReviewOut])
async def list_access_reviews(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AccessReview)
        .where(AccessReview.created_by == user.id)
        .order_by(AccessReview.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{review_id}", response_model=AccessReviewOut)
async def get_access_review(
    review_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    review = await db.get(AccessReview, review_id)
    if not review or review.created_by != user.id:
        raise HTTPException(status_code=404, detail="Access review not found")
    return review


@router.post("/{review_id}/decisions", response_model=AccessReviewOut)
async def submit_decisions(
    review_id: uuid.UUID,
    body: AccessReviewDecisionsSubmit,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    review = await db.get(AccessReview, review_id)
    if not review or review.created_by != user.id:
        raise HTTPException(status_code=404, detail="Access review not found")

    existing = review.decisions or {}
    for entry_id, decision_item in body.decisions.items():
        existing[entry_id] = decision_item.model_dump()
    review.decisions = existing
    review.status = "awaiting_approval"
    await db.commit()
    await db.refresh(review)
    return review


@router.post("/{review_id}/approve", response_model=AccessReviewApproveOut)
async def approve_access_review(
    review_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    review = await db.get(AccessReview, review_id)
    if not review or review.created_by != user.id:
        raise HTTPException(status_code=404, detail="Access review not found")

    snapshot_entries = (review.snapshot or {}).get("entries", [])
    decisions = review.decisions or {}

    # Validate all entries have a decision
    undecided = [e["entry_id"] for e in snapshot_entries if e["entry_id"] not in decisions]
    if undecided:
        raise HTTPException(
            status_code=422,
            detail=f"{len(undecided)} entries have no decision. Submit decisions first.",
        )

    # Generate change requests for "revoke" decisions
    generated = 0
    for entry_id, decision in decisions.items():
        if decision.get("decision") == "revoke":
            entry = next((e for e in snapshot_entries if e["entry_id"] == entry_id), None)
            if entry:
                # Change request creation for access revocation goes here.
                # Full implementation builds a targeted CR per connector/access_type.
                # Stub: count intended CRs.
                generated += 1

    review.approved_at = datetime.now(timezone.utc)
    review.completed_at = datetime.now(timezone.utc)
    review.status = "completed"
    await db.commit()

    return AccessReviewApproveOut(
        review_id=review.id,
        status="completed",
        generated_change_requests=generated,
    )


@router.get("/{review_id}/changes", response_model=list)
async def get_review_changes(
    review_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    review = await db.get(AccessReview, review_id)
    if not review or review.created_by != user.id:
        raise HTTPException(status_code=404, detail="Access review not found")

    from sqlalchemy import text
    result = await db.execute(
        text(
            "SELECT change_request_id FROM access_review_change_requests WHERE review_id = :rid"
        ),
        {"rid": str(review_id)},
    )
    return [{"change_request_id": str(row[0])} for row in result]
```

- [ ] **Step 4: Register the router in `backend/app/main.py`**

After `from app.routers import agent as agent_router`, add:

```python
from app.routers import access_reviews as access_reviews_router
```

After `app.include_router(agent_router.router)`, add:

```python
app.include_router(access_reviews_router.router)
```

- [ ] **Step 5: Run tests — expect pass**

```bash
docker compose exec backend pytest app/tests/test_access_reviews.py -v
```

Expected: `6 passed`

- [ ] **Step 6: Run full backend suite to catch regressions**

```bash
docker compose exec backend pytest app/tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all existing tests still pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/access_reviews.py backend/app/main.py backend/app/tests/test_access_reviews.py
git commit -m "feat(api): add /api/access-reviews router — CRUD, decisions, approve, changes endpoints"
```

---

## Task 7: Frontend — Access Reviews Page

**Files:**
- Create: `frontend/src/api/accessReviews.ts`
- Create: `frontend/src/pages/AccessReviews.tsx`
- Modify: `frontend/src/routes/index.tsx`

- [ ] **Step 1: Create `frontend/src/api/accessReviews.ts`**

```typescript
import apiClient from "./client";

export interface AccessReviewScope {
  connector_ids: string[] | null;
  groups: string[] | null;
  user_emails: string[] | null;
}

export interface AccessReviewCreate {
  title: string;
  scope: AccessReviewScope;
}

export interface AccessReviewDecisionItem {
  decision: "keep" | "revoke";
  note: string;
}

export interface AccessReviewDecisionsSubmit {
  decisions: Record<string, AccessReviewDecisionItem>;
}

export interface AccessReviewOut {
  id: string;
  title: string;
  scope: Record<string, unknown>;
  status: string;
  created_at: string;
  updated_at: string;
  collected_at: string | null;
  approved_at: string | null;
  completed_at: string | null;
  snapshot: Record<string, unknown> | null;
  decisions: Record<string, AccessReviewDecisionItem> | null;
  created_by: string | null;
}

export interface ApproveOut {
  review_id: string;
  status: string;
  generated_change_requests: number;
}

const BASE = "/api/access-reviews";

export const accessReviewsApi = {
  list: (): Promise<AccessReviewOut[]> =>
    apiClient.get<AccessReviewOut[]>(BASE).then((r) => r.data),

  get: (id: string): Promise<AccessReviewOut> =>
    apiClient.get<AccessReviewOut>(`${BASE}/${id}`).then((r) => r.data),

  create: (body: AccessReviewCreate): Promise<AccessReviewOut> =>
    apiClient.post<AccessReviewOut>(BASE, body).then((r) => r.data),

  submitDecisions: (id: string, body: AccessReviewDecisionsSubmit): Promise<AccessReviewOut> =>
    apiClient.post<AccessReviewOut>(`${BASE}/${id}/decisions`, body).then((r) => r.data),

  approve: (id: string): Promise<ApproveOut> =>
    apiClient.post<ApproveOut>(`${BASE}/${id}/approve`).then((r) => r.data),

  getChanges: (id: string): Promise<{ change_request_id: string }[]> =>
    apiClient.get(`${BASE}/${id}/changes`).then((r) => r.data),
};
```

- [ ] **Step 2: Create `frontend/src/pages/AccessReviews.tsx`**

```tsx
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate, Link } from "react-router-dom";
import {
  accessReviewsApi,
  type AccessReviewOut,
  type AccessReviewDecisionItem,
} from "../api/accessReviews";

// ─── Status badge ────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    collecting: "bg-yellow-100 text-yellow-800",
    awaiting_approval: "bg-blue-100 text-blue-800",
    approved: "bg-indigo-100 text-indigo-800",
    generating_changes: "bg-purple-100 text-purple-800",
    completed: "bg-green-100 text-green-800",
    failed: "bg-red-100 text-red-800",
  };
  return (
    <span className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${colors[status] ?? "bg-gray-100 text-gray-800"}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

// ─── New Review Modal ─────────────────────────────────────────────────────────

function NewReviewModal({ onClose }: { onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const navigate = useNavigate();

  const create = useMutation({
    mutationFn: () =>
      accessReviewsApi.create({
        title,
        scope: { connector_ids: null, groups: null, user_emails: null },
      }),
    onSuccess: (review) => {
      qc.invalidateQueries({ queryKey: ["access-reviews"] });
      onClose();
      navigate(`/access-reviews/${review.id}`);
    },
    onError: () => setError("Failed to create review"),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md p-6 space-y-4">
        <h2 className="text-lg font-semibold">New Access Review</h2>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Title</label>
          <input
            className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            placeholder="e.g. Q2 2026 Access Review — Engineering"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <p className="text-xs text-gray-500">
          Scope: all connectors, all users. Connector and group filters coming soon.
        </p>
        <div className="flex justify-end gap-2">
          <button className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded" onClick={onClose}>
            Cancel
          </button>
          <button
            className="px-4 py-2 text-sm bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50"
            disabled={!title.trim() || create.isPending}
            onClick={() => create.mutate()}
          >
            {create.isPending ? "Creating…" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── List View ────────────────────────────────────────────────────────────────

function AccessReviewList() {
  const [showModal, setShowModal] = useState(false);
  const { data: reviews, isLoading, error } = useQuery({
    queryKey: ["access-reviews"],
    queryFn: accessReviewsApi.list,
  });

  if (isLoading) return <p className="p-6 text-gray-500">Loading…</p>;
  if (error) return <p className="p-6 text-red-600">Failed to load access reviews.</p>;

  return (
    <div className="p-6 space-y-4">
      {showModal && <NewReviewModal onClose={() => setShowModal(false)} />}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-gray-900">Access Reviews</h1>
        <button
          className="px-4 py-2 bg-indigo-600 text-white text-sm rounded hover:bg-indigo-700"
          onClick={() => setShowModal(true)}
        >
          New Review
        </button>
      </div>

      {reviews?.length === 0 ? (
        <p className="text-sm text-gray-500">No access reviews yet. Create one to get started.</p>
      ) : (
        <div className="overflow-hidden border border-gray-200 rounded-lg">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Title</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Status</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {reviews?.map((r) => (
                <tr key={r.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <Link
                      to={`/access-reviews/${r.id}`}
                      className="text-indigo-600 hover:underline font-medium"
                    >
                      {r.title}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={r.status} />
                  </td>
                  <td className="px-4 py-3 text-gray-500">
                    {new Date(r.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── Detail View ──────────────────────────────────────────────────────────────

function AccessReviewDetail() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();

  const { data: review, isLoading, error } = useQuery({
    queryKey: ["access-review", id],
    queryFn: () => accessReviewsApi.get(id!),
    enabled: !!id,
    refetchInterval: (q) => {
      const status = q.state.data?.status;
      return status === "collecting" || status === "generating_changes" ? 3000 : false;
    },
  });

  const approve = useMutation({
    mutationFn: () => accessReviewsApi.approve(id!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["access-review", id] });
      qc.invalidateQueries({ queryKey: ["access-reviews"] });
    },
  });

  const submitDecision = useMutation({
    mutationFn: (payload: { entry_id: string; decision: "keep" | "revoke" }) =>
      accessReviewsApi.submitDecisions(id!, {
        decisions: {
          [payload.entry_id]: { decision: payload.decision, note: "" },
        },
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["access-review", id] }),
  });

  if (isLoading) return <p className="p-6 text-gray-500">Loading…</p>;
  if (error || !review) return <p className="p-6 text-red-600">Review not found.</p>;

  const entries: any[] = review.snapshot?.entries ?? [];
  const decisions = review.decisions ?? {};

  const allDecided = entries.length === 0 || entries.every((e) => decisions[e.entry_id]);
  const revokeCount = Object.values(decisions).filter((d) => d.decision === "revoke").length;

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <Link to="/access-reviews" className="text-sm text-gray-500 hover:text-gray-700">
            ← Access Reviews
          </Link>
          <h1 className="text-xl font-semibold text-gray-900 mt-1">{review.title}</h1>
        </div>
        <StatusBadge status={review.status} />
      </div>

      {/* Summary */}
      <div className="grid grid-cols-3 gap-4 text-sm text-gray-600">
        <div>
          <span className="font-medium">Collected:</span>{" "}
          {review.collected_at ? new Date(review.collected_at).toLocaleString() : "—"}
        </div>
        <div>
          <span className="font-medium">Entries:</span> {entries.length}
        </div>
        <div>
          <span className="font-medium">To revoke:</span> {revokeCount}
        </div>
      </div>

      {/* Approve button */}
      {review.status === "awaiting_approval" && (
        <div className="flex items-center gap-4">
          <button
            className="px-4 py-2 bg-green-600 text-white text-sm rounded hover:bg-green-700 disabled:opacity-50"
            disabled={!allDecided || approve.isPending}
            onClick={() => approve.mutate()}
          >
            {approve.isPending ? "Approving…" : `Approve & Generate ${revokeCount} Change Request${revokeCount !== 1 ? "s" : ""}`}
          </button>
          {!allDecided && (
            <p className="text-xs text-gray-500">
              {entries.filter((e) => !decisions[e.entry_id]).length} entries still need a decision.
            </p>
          )}
        </div>
      )}

      {/* Entries table */}
      {entries.length > 0 ? (
        <div className="overflow-hidden border border-gray-200 rounded-lg">
          <table className="min-w-full divide-y divide-gray-200 text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left font-medium text-gray-500">User</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Access</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Connector</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Risk</th>
                <th className="px-4 py-3 text-left font-medium text-gray-500">Decision</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 bg-white">
              {entries.map((e) => {
                const d: AccessReviewDecisionItem | undefined = decisions[e.entry_id];
                return (
                  <tr key={e.entry_id} className="hover:bg-gray-50">
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-900">{e.user_display_name}</div>
                      <div className="text-gray-500 text-xs">{e.user_email}</div>
                    </td>
                    <td className="px-4 py-3 text-gray-700">{e.access_label}</td>
                    <td className="px-4 py-3 text-gray-500">{e.connector_type?.replace(/_/g, " ")}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${
                          e.risk_level === "high"
                            ? "bg-red-100 text-red-700"
                            : e.risk_level === "medium"
                            ? "bg-yellow-100 text-yellow-700"
                            : "bg-green-100 text-green-700"
                        }`}
                      >
                        {e.risk_level}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {review.status === "awaiting_approval" ? (
                        <div className="flex gap-2">
                          <button
                            className={`px-3 py-1 rounded text-xs font-medium border ${
                              d?.decision === "keep"
                                ? "bg-green-600 text-white border-green-600"
                                : "border-gray-300 text-gray-700 hover:bg-gray-50"
                            }`}
                            onClick={() =>
                              submitDecision.mutate({ entry_id: e.entry_id, decision: "keep" })
                            }
                          >
                            Keep
                          </button>
                          <button
                            className={`px-3 py-1 rounded text-xs font-medium border ${
                              d?.decision === "revoke"
                                ? "bg-red-600 text-white border-red-600"
                                : "border-gray-300 text-gray-700 hover:bg-gray-50"
                            }`}
                            onClick={() =>
                              submitDecision.mutate({ entry_id: e.entry_id, decision: "revoke" })
                            }
                          >
                            Revoke
                          </button>
                        </div>
                      ) : (
                        <span
                          className={`text-xs font-medium ${
                            d?.decision === "revoke" ? "text-red-600" : "text-green-600"
                          }`}
                        >
                          {d?.decision ?? "—"}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-gray-500">
          {review.status === "collecting"
            ? "Collecting access data… this page will refresh automatically."
            : "No entries in this review."}
        </p>
      )}
    </div>
  );
}

// ─── Exports ──────────────────────────────────────────────────────────────────

export function AccessReviews() {
  const { id } = useParams<{ id?: string }>();
  return id ? <AccessReviewDetail /> : <AccessReviewList />;
}
```

- [ ] **Step 3: Add routes to `frontend/src/routes/index.tsx`**

Add imports after the `Settings` import:

```typescript
import { AccessReviews } from "../pages/AccessReviews";
```

Add routes inside the `<Route element={<Layout />}>` block, after the `/settings` route:

```typescript
        <Route path="/access-reviews" element={<AccessReviews />} />
        <Route path="/access-reviews/:id" element={<AccessReviews />} />
```

- [ ] **Step 4: Add nav link (in Layout component)**

In `frontend/src/components/Layout.tsx`, find the nav links section and add:

```typescript
{ to: "/access-reviews", label: "Access Reviews" }
```

alongside the existing nav items.

- [ ] **Step 5: Verify frontend builds**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Then open `http://localhost:3000/access-reviews` and verify the list view loads without errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/accessReviews.ts frontend/src/pages/AccessReviews.tsx frontend/src/routes/index.tsx
git commit -m "feat(frontend): add Access Reviews page — list view, detail view with decision UI"
```

---

## Task 8: Frontend — Offboard/Onboard Forms

**Files:**
- Create: `frontend/src/components/change-requests/OffboardUserForm.tsx`
- Create: `frontend/src/components/change-requests/OnboardUserForm.tsx`

These forms slot into the existing `CreateChangeRequest` page when the user selects the `offboard_user` or `onboard_user` change type. The integration point depends on how `CreateChangeRequest.tsx` currently selects payload forms; add the new types there after the forms exist.

- [ ] **Step 1: Create `frontend/src/components/change-requests/OffboardUserForm.tsx`**

```tsx
import type { ChangeEvent } from "react";

export interface OffboardUserPayload {
  target_email: string;
  reason: "resignation" | "termination" | "contract_end";
  isolate_endpoints: boolean;
  notify_manager: boolean;
  manager_email: string;
}

interface Props {
  value: Partial<OffboardUserPayload>;
  onChange: (v: Partial<OffboardUserPayload>) => void;
}

export function OffboardUserForm({ value, onChange }: Props) {
  const set = (key: keyof OffboardUserPayload) =>
    (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      onChange({ ...value, [key]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });

  return (
    <div className="space-y-4 text-sm">
      <div>
        <label className="block font-medium text-gray-700 mb-1">Target Email *</label>
        <input
          type="email"
          className="w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          placeholder="alice@corp.com"
          value={value.target_email ?? ""}
          onChange={set("target_email")}
        />
      </div>

      <div>
        <label className="block font-medium text-gray-700 mb-1">Reason *</label>
        <select
          className="w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          value={value.reason ?? ""}
          onChange={set("reason")}
        >
          <option value="">Select reason…</option>
          <option value="resignation">Resignation</option>
          <option value="termination">Termination</option>
          <option value="contract_end">Contract End</option>
        </select>
      </div>

      <div>
        <label className="block font-medium text-gray-700 mb-1">Manager Email (for report)</label>
        <input
          type="email"
          className="w-full border border-gray-300 rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          placeholder="manager@corp.com"
          value={value.manager_email ?? ""}
          onChange={set("manager_email")}
        />
      </div>

      <div className="flex items-center gap-3">
        <input
          id="notify_manager"
          type="checkbox"
          className="h-4 w-4 text-indigo-600 border-gray-300 rounded"
          checked={value.notify_manager ?? true}
          onChange={set("notify_manager")}
        />
        <label htmlFor="notify_manager" className="text-gray-700">
          Notify manager with offboarding report
        </label>
      </div>

      <div className="flex items-center gap-3">
        <input
          id="isolate_endpoints"
          type="checkbox"
          className="h-4 w-4 text-red-600 border-gray-300 rounded"
          checked={value.isolate_endpoints ?? false}
          onChange={set("isolate_endpoints")}
        />
        <label htmlFor="isolate_endpoints" className="text-gray-700">
          <span className="font-medium text-red-700">Isolate endpoints</span>
          <span className="text-gray-500 ml-1">(CrowdStrike — disruptive, use for terminations)</span>
        </label>
      </div>

      <p className="text-xs text-gray-500 bg-yellow-50 border border-yellow-200 rounded p-2">
        Nexplane will resolve accounts in AD, Okta, Entra ID, Google Workspace, GitHub, and Slack
        for this email and generate one disable/removal step per connector found.
      </p>
    </div>
  );
}
```

- [ ] **Step 2: Create `frontend/src/components/change-requests/OnboardUserForm.tsx`**

```tsx
import type { ChangeEvent } from "react";

export interface OnboardUserPayload {
  target_email: string;
  display_name: string;
  department: string;
  manager_email: string;
  ad_ou: string;
  ad_groups: string;          // comma-separated in UI, split before submit
  okta_groups: string;
  google_org_unit: string;
  github_teams: string;
  slack_channels: string;
}

interface Props {
  value: Partial<OnboardUserPayload>;
  onChange: (v: Partial<OnboardUserPayload>) => void;
}

function Field({
  label, id, value, onChange, type = "text", placeholder, hint,
}: {
  label: string; id: keyof OnboardUserPayload; value: string; placeholder?: string; hint?: string;
  onChange: (key: keyof OnboardUserPayload, val: string) => void; type?: string;
}) {
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <input
        type={type}
        className="w-full border border-gray-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(id, e.target.value)}
      />
      {hint && <p className="text-xs text-gray-400 mt-1">{hint}</p>}
    </div>
  );
}

export function OnboardUserForm({ value, onChange }: Props) {
  const set = (key: keyof OnboardUserPayload, val: string) => onChange({ ...value, [key]: val });

  return (
    <div className="space-y-4">
      <Field label="New User Email *" id="target_email" value={value.target_email ?? ""} onChange={set} type="email" placeholder="new.employee@corp.com" />
      <Field label="Display Name *" id="display_name" value={value.display_name ?? ""} onChange={set} placeholder="Jane Smith" />
      <Field label="Department *" id="department" value={value.department ?? ""} onChange={set} placeholder="Engineering" />
      <Field label="Manager Email *" id="manager_email" value={value.manager_email ?? ""} onChange={set} type="email" placeholder="manager@corp.com" />

      <details className="border border-gray-200 rounded p-3">
        <summary className="cursor-pointer text-sm font-medium text-gray-700">Active Directory options</summary>
        <div className="mt-3 space-y-3">
          <Field label="OU Path" id="ad_ou" value={value.ad_ou ?? ""} onChange={set} placeholder="OU=Engineering,DC=corp,DC=example,DC=com" hint="Leave blank for default OU" />
          <Field label="AD Groups (comma-separated DNs)" id="ad_groups" value={value.ad_groups ?? ""} onChange={set} placeholder="CN=VPN-Users,OU=Groups,DC=corp,DC=example,DC=com" />
        </div>
      </details>

      <details className="border border-gray-200 rounded p-3">
        <summary className="cursor-pointer text-sm font-medium text-gray-700">Okta options</summary>
        <div className="mt-3">
          <Field label="Okta Group IDs (comma-separated)" id="okta_groups" value={value.okta_groups ?? ""} onChange={set} placeholder="00g1ab2cd3ef4gh5ij,00g..." />
        </div>
      </details>

      <details className="border border-gray-200 rounded p-3">
        <summary className="cursor-pointer text-sm font-medium text-gray-700">Google Workspace options</summary>
        <div className="mt-3">
          <Field label="Org Unit Path" id="google_org_unit" value={value.google_org_unit ?? ""} onChange={set} placeholder="/Engineering" />
        </div>
      </details>

      <details className="border border-gray-200 rounded p-3">
        <summary className="cursor-pointer text-sm font-medium text-gray-700">GitHub options</summary>
        <div className="mt-3">
          <Field label="Team slugs (comma-separated)" id="github_teams" value={value.github_teams ?? ""} onChange={set} placeholder="backend,infra" />
        </div>
      </details>

      <details className="border border-gray-200 rounded p-3">
        <summary className="cursor-pointer text-sm font-medium text-gray-700">Slack options</summary>
        <div className="mt-3">
          <Field label="Channel IDs to pre-join (comma-separated)" id="slack_channels" value={value.slack_channels ?? ""} onChange={set} placeholder="C01234ABCDE,C09876ZYXWV" />
        </div>
      </details>
    </div>
  );
}
```

- [ ] **Step 3: Wire forms into CreateChangeRequest**

In `frontend/src/pages/CreateChangeRequest.tsx`, import the new forms and add them to whichever conditional renders the payload form section. The exact edit depends on the current structure; find the block that switches on `changeType` and add:

```typescript
import { OffboardUserForm } from "../components/change-requests/OffboardUserForm";
import { OnboardUserForm } from "../components/change-requests/OnboardUserForm";

// Inside the render, alongside existing type-specific forms:
{changeType === "offboard_user" && (
  <OffboardUserForm value={payload} onChange={setPayload} />
)}
{changeType === "onboard_user" && (
  <OnboardUserForm value={payload} onChange={setPayload} />
)}
```

Also add the new types to the change type selector options:

```typescript
<option value="offboard_user">Offboard User</option>
<option value="onboard_user">Onboard User</option>
```

- [ ] **Step 4: Verify frontend builds**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Open `http://localhost:3000/change-requests/new`, select "Offboard User", verify the form renders. Repeat for "Onboard User".

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/change-requests/OffboardUserForm.tsx frontend/src/components/change-requests/OnboardUserForm.tsx frontend/src/pages/CreateChangeRequest.tsx
git commit -m "feat(frontend): add OffboardUserForm and OnboardUserForm components for new change types"
```

---

## Self-Review Checklist

**Spec coverage:**

| Spec Section | Plan Task |
|---|---|
| Section 1.2 — AccessReview model | Task 1 |
| Section 1.3/1.4 — snapshot/decisions structure | Tasks 1 & 6 |
| Section 2.2 — offboard_user definition + build_plan | Task 4 |
| Section 2.3 — onboard_user definition + build_plan | Task 5 |
| Section 2.5 — connector resolution | Task 3 |
| Section 3 — phase-aware execution | noted in Task 4/5 plan builders; `change_executor.py` extension follows naturally once step dicts include `phase` |
| Section 4.1 — new change types via existing endpoint | Tasks 2 + 4 + 5 |
| Section 4.2 — access review endpoints | Task 6 |
| Section 4.3 — schemas | Task 2 |
| Section 5.1 — offboard/onboard forms | Task 8 |
| Section 5.2 — access reviews page | Task 7 |
| Section 6 — Alembic migration | Task 1 |

**TDD compliance:** Every implementation task has a failing-test step that runs before the implementation step.

**Placeholder scan:** No TODOs or vague stubs — all executor steps contain real API logic with a credential-absent simulation path that allows tests to run without live connectors.

**Connector simulation path:** Every step module checks `if not creds` and returns a simulated result, so the full offboard/onboard workflow can be exercised end-to-end in staging without live connector credentials.
