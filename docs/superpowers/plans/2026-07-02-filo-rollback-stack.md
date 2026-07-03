# FILO Rollback Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce FILO rollback ordering in project rollback, implement `to_cr_id` partial rollback, and prove both with a live smoke test.

**Architecture:** Fix A patches `rollback_project` in `projects.py` to filter members by `sequence_order` when `to_cr_id` is given; Fix B changes `project_rollback_service.initiate()` to sort by `application_sequence` first. Task 3 writes a 5-phase smoke test using existing MCP tools for CR lifecycle and httpx for HTTP-only endpoints.

**Tech Stack:** Python 3.12, SQLAlchemy async, FastAPI, pytest-asyncio, httpx, Nexplane MCP tools, `apply_sysctl_hardening` executor.

## Global Constraints

- NEVER use `from __future__ import annotations` in any file containing FastMCP `@mcp.tool()` decorators — FastMCP inspects annotations at runtime and crashes with string annotations.
- All smoke tests run on EC2 via Tailscale (`ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39`); never from laptop.
- Smoke env vars: `API_TOKEN` (nxp_... token), `ASSET_ID` (UUID of platform asset with running Nexplane agent).
- Maximum two hosts for smoke infrastructure — all phases run on the single existing platform asset.
- The `_auth()` helper returns `(principal, db, db_cm)`. For FK fields needing a user UUID, check `isinstance(principal, User)` and use `principal.created_by_user_id` for AgentTokens.
- All smoke changes go through the Nexplane CR lifecycle (create → approve → execute → rollback) using MCP tools or REST API — not direct executor calls.
- Smoke must verify rollback happened by checking CR status in DB (via MCP tool or direct query), not just absence of error.

---

## File Map

| File | Role |
|------|------|
| `backend/app/mcp_tools/projects.py` | Fix A: enforce `to_cr_id` in `rollback_project` |
| `backend/app/services/project_rollback_service.py` | Fix B: sort by `application_sequence` first |
| `backend/tests/smoke/test_smoke_filo_rollback.py` | New: 5-phase FILO smoke suite |
| `backend/tests/unit/test_rollback_project_to_cr_id.py` | New: unit tests for Fix A |
| `backend/tests/unit/test_project_rollback_ordering.py` | New: unit tests for Fix B |

---

### Task 1: Fix A — `to_cr_id` enforcement in `rollback_project`

**Files:**
- Modify: `backend/app/mcp_tools/projects.py` (lines ~901–966, `rollback_project` function)
- Create: `backend/tests/unit/test_rollback_project_to_cr_id.py`

**Interfaces:**
- Consumes: `project.members` list of `ProjectChangeRequest` (already eagerly loaded in the existing query with `.options(selectinload(Project.members).selectinload(ProjectChangeRequest.change_request))`)
- Produces: `rollback_project` now respects `to_cr_id`; later tasks rely on this working correctly

**Context:** In `projects.py`, `rollback_project` currently has this block at lines ~940–946:
```python
cr_ids_filter = None
if to_cr_id is not None:
    logger.info(
        "rollback_project: to_cr_id=%s provided — partial rollback not yet enforced, "
        "initiating full rollback",
        to_cr_id,
    )
```
The `prs.initiate(cr_ids=cr_ids_filter)` below it receives `None`, ignoring `to_cr_id` entirely. The existing query already loads `project.members` with their `.change_request` relationship, so no new eager load is needed.

- [ ] **Step 1: Write the failing unit test**

Create `backend/tests/unit/test_rollback_project_to_cr_id.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for rollback_project to_cr_id enforcement."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _make_member(seq_order: int, status: str = "completed") -> MagicMock:
    cr_id = uuid.uuid4()
    cr = MagicMock()
    cr.id = cr_id
    cr.status = status
    cr.application_sequence = seq_order * 100
    m = MagicMock()
    m.change_request_id = cr_id
    m.sequence_order = seq_order
    m.change_request = cr
    return m


async def test_to_cr_id_none_passes_none_to_initiate():
    """When to_cr_id is None, cr_ids=None is passed (full rollback)."""
    token = "nxp_test"
    project_id = str(uuid.uuid4())
    member_a = _make_member(seq_order=1)
    member_b = _make_member(seq_order=2)

    mock_project = MagicMock()
    mock_project.id = uuid.UUID(project_id)
    mock_project.organization_id = uuid.uuid4()
    mock_project.members = [member_a, member_b]

    mock_rollback = MagicMock()
    mock_rollback.id = uuid.uuid4()

    with (
        patch("app.mcp_tools.projects._auth", new=AsyncMock(return_value=(
            MagicMock(organization_id=mock_project.organization_id, id=uuid.uuid4()),
            MagicMock(), MagicMock(__aexit__=AsyncMock()),
        ))),
        patch("app.mcp_tools.projects.select"),
        patch("app.mcp_tools.projects.project_rollback_service") as mock_prs,
    ):
        from sqlalchemy.ext.asyncio import AsyncSession
        mock_db = AsyncMock(spec=AsyncSession)
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=mock_project)
        ))
        mock_prs.initiate = AsyncMock(return_value=(mock_rollback, []))

        # Patch _auth to return a User principal
        from app.models.user import User
        principal = MagicMock(spec=User)
        principal.id = uuid.uuid4()
        principal.organization_id = mock_project.organization_id
        db_cm = MagicMock()
        db_cm.__aexit__ = AsyncMock()

        with patch("app.mcp_tools.projects._auth", new=AsyncMock(
            return_value=(principal, mock_db, db_cm)
        )):
            from app.mcp_tools.projects import rollback_project
            result = await rollback_project(token=token, project_id=project_id)

        call_kwargs = mock_prs.initiate.call_args.kwargs
        assert call_kwargs["cr_ids"] is None
        assert "rollback_id" in result


async def test_to_cr_id_not_in_project_returns_error():
    """When to_cr_id is not a member of the project, return cr_not_in_project."""
    token = "nxp_test"
    project_id = str(uuid.uuid4())
    member_a = _make_member(seq_order=1)

    mock_project = MagicMock()
    mock_project.id = uuid.UUID(project_id)
    mock_project.organization_id = uuid.uuid4()
    mock_project.members = [member_a]

    foreign_cr_id = str(uuid.uuid4())

    with patch("app.mcp_tools.projects._auth"):
        from app.models.user import User
        principal = MagicMock(spec=User)
        principal.id = uuid.uuid4()
        principal.organization_id = mock_project.organization_id
        db_cm = MagicMock()
        db_cm.__aexit__ = AsyncMock()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=mock_project)
        ))

        with patch("app.mcp_tools.projects._auth", new=AsyncMock(
            return_value=(principal, mock_db, db_cm)
        )):
            from app.mcp_tools.projects import rollback_project
            result = await rollback_project(
                token=token, project_id=project_id, to_cr_id=foreign_cr_id
            )

    assert result["error"] == "cr_not_in_project"
    assert result["cr_id"] == foreign_cr_id


async def test_to_cr_id_not_executed_returns_error():
    """When to_cr_id references a non-completed CR, return cr_not_executed."""
    token = "nxp_test"
    project_id = str(uuid.uuid4())
    member_a = _make_member(seq_order=1, status="draft")  # not completed
    to_cr_id = str(member_a.change_request_id)

    mock_project = MagicMock()
    mock_project.id = uuid.UUID(project_id)
    mock_project.organization_id = uuid.uuid4()
    mock_project.members = [member_a]

    with patch("app.mcp_tools.projects._auth"):
        from app.models.user import User
        principal = MagicMock(spec=User)
        principal.id = uuid.uuid4()
        principal.organization_id = mock_project.organization_id
        db_cm = MagicMock()
        db_cm.__aexit__ = AsyncMock()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(
            scalar_one_or_none=MagicMock(return_value=mock_project)
        ))

        with patch("app.mcp_tools.projects._auth", new=AsyncMock(
            return_value=(principal, mock_db, db_cm)
        )):
            from app.mcp_tools.projects import rollback_project
            result = await rollback_project(
                token=token, project_id=project_id, to_cr_id=to_cr_id
            )

    assert result["error"] == "cr_not_executed"


async def test_to_cr_id_filters_to_sequence_order_gte():
    """When to_cr_id is valid, only CRs with sequence_order >= target are rolled back."""
    token = "nxp_test"
    project_id = str(uuid.uuid4())
    member_1 = _make_member(seq_order=1)
    member_2 = _make_member(seq_order=2)
    member_3 = _make_member(seq_order=3)
    to_cr_id = str(member_2.change_request_id)

    mock_project = MagicMock()
    mock_project.id = uuid.UUID(project_id)
    mock_project.organization_id = uuid.uuid4()
    mock_project.members = [member_1, member_2, member_3]

    mock_rollback = MagicMock()
    mock_rollback.id = uuid.uuid4()

    from app.models.user import User
    principal = MagicMock(spec=User)
    principal.id = uuid.uuid4()
    principal.organization_id = mock_project.organization_id
    db_cm = MagicMock()
    db_cm.__aexit__ = AsyncMock()
    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=mock_project)
    ))

    with patch("app.mcp_tools.projects.project_rollback_service") as mock_prs:
        mock_prs.initiate = AsyncMock(return_value=(mock_rollback, []))

        with patch("app.mcp_tools.projects._auth", new=AsyncMock(
            return_value=(principal, mock_db, db_cm)
        )):
            from app.mcp_tools.projects import rollback_project
            result = await rollback_project(
                token=token, project_id=project_id, to_cr_id=to_cr_id
            )

    call_kwargs = mock_prs.initiate.call_args.kwargs
    filtered_ids = set(call_kwargs["cr_ids"])
    # member_2 and member_3 should be included; member_1 should not
    assert member_2.change_request_id in filtered_ids
    assert member_3.change_request_id in filtered_ids
    assert member_1.change_request_id not in filtered_ids
    assert "rollback_id" in result
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd backend
pytest tests/unit/test_rollback_project_to_cr_id.py -v 2>&1 | tail -20
```
Expected: `FAILED` on `test_to_cr_id_filters_to_sequence_order_gte` (the key filtering test) and `test_to_cr_id_not_in_project_returns_error` and `test_to_cr_id_not_executed_returns_error` — the current code ignores `to_cr_id`.

- [ ] **Step 3: Implement Fix A in `rollback_project`**

In `backend/app/mcp_tools/projects.py`, find the `rollback_project` function. Replace these lines (the no-op block):

```python
cr_ids_filter = None
if to_cr_id is not None:
    logger.info(
        "rollback_project: to_cr_id=%s provided — partial rollback not yet enforced, "
        "initiating full rollback",
        to_cr_id,
    )
```

With:

```python
cr_ids_filter = None
if to_cr_id is not None:
    try:
        to_cr_uuid = _uuid.UUID(to_cr_id)
    except ValueError:
        return {"error": "cr_not_in_project", "cr_id": to_cr_id}

    target_member = next(
        (m for m in project.members if m.change_request_id == to_cr_uuid),
        None,
    )
    if target_member is None:
        return {"error": "cr_not_in_project", "cr_id": to_cr_id}

    from app.models.change_request import ChangeRequestStatus as _CRStatus
    if target_member.change_request.status != _CRStatus.completed:
        return {"error": "cr_not_executed", "cr_id": to_cr_id}

    cr_ids_filter = [
        m.change_request_id
        for m in project.members
        if (
            m.sequence_order >= target_member.sequence_order
            and m.change_request.status == _CRStatus.completed
        )
    ]
    if not cr_ids_filter:
        return {"rollback_id": None, "message": "no executed CRs to roll back in range"}
```

Also update the docstring of `rollback_project` — change "to_cr_id is reserved for future partial rollback support and is currently documented but not enforced" to "to_cr_id: if provided, rolls back from the most recent CR down to and including this CR (FILO order). CRs with sequence_order < to_cr_id's order are left untouched."

Also remove the `"note"` key from the return dict at the bottom of the function (the one that says "to_cr_id is reserved for future"):

Find and replace:
```python
        return {
            "rollback_initiated": True,
            "rollback_id": str(rollback.id),
            "warnings": warnings,
            "note": (
                "to_cr_id is reserved for future partial rollback support"
                if to_cr_id
                else None
            ),
        }
```

With:
```python
        return {
            "rollback_initiated": True,
            "rollback_id": str(rollback.id),
            "warnings": warnings,
        }
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd backend
pytest tests/unit/test_rollback_project_to_cr_id.py -v 2>&1 | tail -20
```
Expected: all 4 tests `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/mcp_tools/projects.py backend/tests/unit/test_rollback_project_to_cr_id.py
git commit -m "feat(rollback): enforce to_cr_id partial rollback in rollback_project"
```

---

### Task 2: Fix B — `application_sequence`-first ordering in project rollback

**Files:**
- Modify: `backend/app/services/project_rollback_service.py` (line ~126, inside `initiate()`)
- Create: `backend/tests/unit/test_project_rollback_ordering.py`

**Interfaces:**
- Consumes: `eligible_members: list[ProjectChangeRequest]` — each member's `.change_request` is already loaded (the docstring on `initiate()` requires eager loading); each CR has `.application_sequence` (int or None) and the member has `.sequence_order` (int)
- Produces: `sorted_members` ordered by actual execution order (DESC), used to create `ProjectRollbackStep` records in reverse

**Context:** The current sort at line ~126:
```python
sorted_members = sorted(eligible_members, key=lambda m: m.sequence_order, reverse=True)
```
This uses only the project-defined order. If CRs were reordered in the project after some had already executed, `application_sequence` reflects the true execution order and `sequence_order` may not. We want to use `application_sequence` (the global monotonic counter stamped at completion) as the authoritative ordering.

- [ ] **Step 1: Write the failing unit tests**

Create `backend/tests/unit/test_project_rollback_ordering.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for application_sequence-first ordering in project_rollback_service.initiate()."""
import uuid
import logging
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

pytestmark = pytest.mark.asyncio


def _make_member(seq_order: int, app_seq: int | None) -> MagicMock:
    cr = MagicMock()
    cr.id = uuid.uuid4()
    cr.status = "completed"
    cr.application_sequence = app_seq
    cr.change_type = "apply_sysctl_hardening"
    m = MagicMock()
    m.change_request_id = cr.id
    m.sequence_order = seq_order
    m.change_request = cr
    return m


async def test_sorts_by_application_sequence_desc_when_available():
    """Members with application_sequence are sorted by it DESC (newest first)."""
    member_a = _make_member(seq_order=1, app_seq=100)  # executed first
    member_b = _make_member(seq_order=2, app_seq=200)  # executed second

    mock_project = MagicMock()
    mock_project.id = uuid.uuid4()
    mock_project.members = [member_a, member_b]
    mock_project.status = "active"

    mock_rollback = MagicMock()
    mock_rollback.id = uuid.uuid4()

    with (
        patch("app.services.project_rollback_service.db") if False else patch("builtins.open") if False else patch("app.services.project_rollback_service.asyncio.ensure_future"),
        patch("app.services.project_rollback_service.ProjectRollback", return_value=mock_rollback),
        patch("app.services.project_rollback_service.ProjectRollbackStep"),
        patch("app.services.project_rollback_service._build_preflight_warnings", return_value=[]),
        patch("app.services.project_rollback_service._get_permanent_types", return_value=set()),
    ):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        from app.services import project_rollback_service as prs
        rollback, warnings = await prs.initiate(
            db=mock_db,
            project=mock_project,
            triggered_by_user_id=uuid.uuid4(),
            notes=None,
            cr_ids=None,
        )

    # ProjectRollbackStep was called with sequence_order=1 for member_b (app_seq=200, rolled back first)
    from app.services.project_rollback_service import ProjectRollbackStep
    calls = ProjectRollbackStep.call_args_list
    first_step_cr_id = calls[0].kwargs["change_request_id"]
    assert first_step_cr_id == member_b.change_request.id, (
        f"Expected member_b (app_seq=200) to be rolled back first, got {first_step_cr_id}"
    )


async def test_falls_back_to_sequence_order_when_app_seq_is_none():
    """Members without application_sequence fall back to sequence_order DESC."""
    member_a = _make_member(seq_order=1, app_seq=None)
    member_b = _make_member(seq_order=2, app_seq=None)

    mock_project = MagicMock()
    mock_project.id = uuid.uuid4()
    mock_project.members = [member_a, member_b]
    mock_project.status = "active"

    mock_rollback = MagicMock()
    mock_rollback.id = uuid.uuid4()

    with (
        patch("app.services.project_rollback_service.asyncio.ensure_future"),
        patch("app.services.project_rollback_service.ProjectRollback", return_value=mock_rollback),
        patch("app.services.project_rollback_service.ProjectRollbackStep"),
        patch("app.services.project_rollback_service._build_preflight_warnings", return_value=[]),
        patch("app.services.project_rollback_service._get_permanent_types", return_value=set()),
    ):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        from app.services import project_rollback_service as prs
        await prs.initiate(
            db=mock_db,
            project=mock_project,
            triggered_by_user_id=uuid.uuid4(),
            notes=None,
            cr_ids=None,
        )

    from app.services.project_rollback_service import ProjectRollbackStep
    calls = ProjectRollbackStep.call_args_list
    first_step_cr_id = calls[0].kwargs["change_request_id"]
    assert first_step_cr_id == member_b.change_request.id, (
        f"Expected member_b (seq_order=2) rolled back first, got {first_step_cr_id}"
    )


async def test_emits_warning_when_ordering_diverges(caplog):
    """When application_sequence order diverges from sequence_order, a warning is logged."""
    # member_a has seq_order=1 but app_seq=200 (executed second despite being planned first)
    # member_b has seq_order=2 but app_seq=100 (executed first despite being planned second)
    member_a = _make_member(seq_order=1, app_seq=200)
    member_b = _make_member(seq_order=2, app_seq=100)

    mock_project = MagicMock()
    mock_project.id = uuid.uuid4()
    mock_project.members = [member_a, member_b]
    mock_project.status = "active"

    mock_rollback = MagicMock()
    mock_rollback.id = uuid.uuid4()

    with (
        patch("app.services.project_rollback_service.asyncio.ensure_future"),
        patch("app.services.project_rollback_service.ProjectRollback", return_value=mock_rollback),
        patch("app.services.project_rollback_service.ProjectRollbackStep"),
        patch("app.services.project_rollback_service._build_preflight_warnings", return_value=[]),
        patch("app.services.project_rollback_service._get_permanent_types", return_value=set()),
    ):
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with caplog.at_level(logging.WARNING, logger="app.services.project_rollback_service"):
            from app.services import project_rollback_service as prs
            await prs.initiate(
                db=mock_db,
                project=mock_project,
                triggered_by_user_id=uuid.uuid4(),
                notes=None,
                cr_ids=None,
            )

    assert any("diverges" in record.message for record in caplog.records), (
        "Expected a warning about ordering divergence, got: " + str([r.message for r in caplog.records])
    )
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd backend
pytest tests/unit/test_project_rollback_ordering.py -v 2>&1 | tail -20
```
Expected: `FAILED` on `test_sorts_by_application_sequence_desc_when_available` and `test_emits_warning_when_ordering_diverges` (current code ignores `application_sequence`).

- [ ] **Step 3: Implement Fix B in `project_rollback_service.py`**

In `backend/app/services/project_rollback_service.py`, find the sort line inside `initiate()`:

```python
sorted_members = sorted(eligible_members, key=lambda m: m.sequence_order, reverse=True)
```

Replace it with:

```python
def _rollback_sort_key(m):
    app_seq = m.change_request.application_sequence
    return app_seq if app_seq is not None else m.sequence_order

sorted_members = sorted(eligible_members, key=_rollback_sort_key, reverse=True)

plan_order_ids = [
    m.change_request_id
    for m in sorted(eligible_members, key=lambda m: m.sequence_order, reverse=True)
]
actual_order_ids = [m.change_request_id for m in sorted_members]
if plan_order_ids != actual_order_ids:
    logger.warning(
        "project rollback order diverges from plan order for project %s: "
        "plan_order=%s actual_order=%s",
        project.id,
        [str(i) for i in plan_order_ids],
        [str(i) for i in actual_order_ids],
    )
```

Note: `_rollback_sort_key` is defined as a nested function inside `initiate()`, just above the `sorted_members` line.

The function signature for `initiate()` already has access to `project` in its local scope, so `project.id` is available for the warning log.

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd backend
pytest tests/unit/test_project_rollback_ordering.py -v 2>&1 | tail -20
```
Expected: all 3 tests `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/project_rollback_service.py backend/tests/unit/test_project_rollback_ordering.py
git commit -m "feat(rollback): use application_sequence for FILO ordering in project rollback"
```

---

### Task 3: FILO smoke test

**Files:**
- Create: `backend/tests/smoke/test_smoke_filo_rollback.py`

**Interfaces:**
- Consumes (Task 1): `rollback_project` now respects `to_cr_id`
- Consumes (Task 2): `project_rollback_service.initiate()` now orders by `application_sequence`
- Consumes (existing MCP tools): `create_change_request`, `approve_change_request`, `execute_change_request`, `get_change_request` from `app.mcp_tools.change_requests`; `create_project`, `add_cr_to_project`, `rollback_project` from `app.mcp_tools.projects`
- Consumes (existing REST endpoints): `POST /change-requests/{cr_id}/rollback` (FILO guard), `POST /assets/{asset_id}/rollback-all`
- Produces: passing smoke proof that FILO works end-to-end

**Auth note for HTTP phases:** PHASE_3 and PHASE_4 call REST endpoints that require JWT auth (`Depends(current_user)`), not MCP API token auth. The `_get_jwt` helper below derives a short-lived JWT from the `API_TOKEN` env var using `app.services.auth_service.create_access_token` and the `ApiToken` model. This JWT is used only within the smoke test.

**Sysctl values:** PHASE_1–4 use `net.ipv4.tcp_keepalive_time`. We change it from its default (7200) to test values. CR-A sets 7199, CR-B sets 7198. PHASE_5 uses a different param (`net.ipv4.tcp_keepalive_intvl`) to avoid conflicts with PHASE_1–4 state.

- [ ] **Step 1: Write the smoke test file**

Create `backend/tests/smoke/test_smoke_filo_rollback.py`:

```python
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""
FILO_ROLLBACK_SMOKE — live smoke test for FILO rollback ordering.

Run (on EC2 inside nexplane-backend-1 container):
    ASSET_ID=<uuid> API_TOKEN=<nxp_...> pytest tests/smoke/test_smoke_filo_rollback.py -v -s

Prerequisites:
  1. Nexplane agent running on ASSET_ID host (sudo, -mode service).
  2. API_TOKEN env var holds a valid nxp_... token with admin/approver role.
  3. ASSET_ID env var holds the UUID of an existing asset.

Phases:
  PHASE_1: Execute CR-A (sysctl 7199) — verify application_sequence stamped
  PHASE_2: Execute CR-B (sysctl 7198) — verify application_sequence > CR-A
  PHASE_3: FILO guard — attempt per-CR rollback of CR-A, expect 409
  PHASE_4: Asset rollback-all — rolls back CR-B then CR-A
  PHASE_5: Project rollback with to_cr_id — partial then full
"""
import asyncio
import hashlib
import os
import uuid

import httpx
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")

_STATE: dict = {}

_BASE_URL = "http://localhost:8000/api/v1"


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        pytest.skip(f"Env var {key} not set — skipping FILO smoke test")
    return val


async def _get_jwt(api_token: str) -> str:
    """Derive a short-lived JWT from an nxp_... API token for REST endpoint auth."""
    import hashlib
    from app.database import AsyncSessionLocal
    from app.models.api_token import ApiToken
    from app.services.auth_service import create_access_token
    from sqlalchemy import select

    token_hash = hashlib.sha256(api_token.encode()).hexdigest()
    async with AsyncSessionLocal() as db:
        r = await db.execute(
            select(ApiToken).where(
                ApiToken.token_hash == token_hash,
                ApiToken.revoked == False,  # noqa: E712
            )
        )
        tok = r.scalar_one()
        return create_access_token(subject=str(tok.user_id))


async def _create_and_execute_sysctl_cr(
    token: str, asset_id: str, param: str, value: int, title: str
) -> str:
    """Create, approve, and execute an apply_sysctl_hardening CR. Return cr_id."""
    from app.mcp_tools.change_requests import (
        approve_change_request,
        create_change_request,
        execute_change_request,
        get_change_request,
    )

    # Create draft CR
    cr = await create_change_request(
        token=token,
        change_type="apply_sysctl_hardening",
        asset_id=asset_id,
        title=title,
        parameters={"settings": {param: value}},
    )
    assert "id" in cr, f"create_change_request failed: {cr}"
    cr_id = cr["id"]

    # Approve
    approved = await approve_change_request(token=token, cr_id=cr_id)
    assert "error" not in approved, f"approve_change_request failed: {approved}"

    # Execute
    executed = await execute_change_request(token=token, cr_id=cr_id)
    assert "error" not in executed, f"execute_change_request failed: {executed}"

    # Poll until completed (max 120s)
    for _ in range(24):
        await asyncio.sleep(5)
        detail = await get_change_request(token=token, cr_id=cr_id)
        status = detail.get("status")
        if status == "completed":
            return cr_id
        if status in ("failed", "rollback_failed"):
            pytest.fail(f"CR {cr_id} reached terminal failure state: {detail}")
    pytest.fail(f"CR {cr_id} timed out after 120s waiting for completed status")


# ---------------------------------------------------------------------------
# PHASE_1: Execute CR-A
# ---------------------------------------------------------------------------

async def test_PHASE_1_execute_cr_a():
    """Execute CR-A (sysctl tcp_keepalive_time=7199). Verify application_sequence is stamped."""
    from app.mcp_tools.change_requests import get_change_request

    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")

    cr_id = await _create_and_execute_sysctl_cr(
        token=token,
        asset_id=asset_id,
        param="net.ipv4.tcp_keepalive_time",
        value=7199,
        title="FILO smoke CR-A — tcp_keepalive_time=7199",
    )
    _STATE["cr_a_id"] = cr_id

    detail = await get_change_request(token=token, cr_id=cr_id)
    assert detail.get("status") == "completed", f"CR-A not completed: {detail}"
    app_seq = detail.get("application_sequence")
    assert app_seq is not None, "CR-A missing application_sequence after completion"
    assert isinstance(app_seq, int), f"application_sequence not int: {app_seq}"
    _STATE["cr_a_seq"] = app_seq
    print(f"\n  CR-A id={cr_id} application_sequence={app_seq}")


# ---------------------------------------------------------------------------
# PHASE_2: Execute CR-B
# ---------------------------------------------------------------------------

async def test_PHASE_2_execute_cr_b():
    """Execute CR-B (sysctl tcp_keepalive_time=7198). Verify seq > CR-A's."""
    from app.mcp_tools.change_requests import get_change_request

    if "cr_a_id" not in _STATE:
        pytest.skip("PHASE_1 did not run")

    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")

    cr_id = await _create_and_execute_sysctl_cr(
        token=token,
        asset_id=asset_id,
        param="net.ipv4.tcp_keepalive_time",
        value=7198,
        title="FILO smoke CR-B — tcp_keepalive_time=7198",
    )
    _STATE["cr_b_id"] = cr_id

    detail = await get_change_request(token=token, cr_id=cr_id)
    app_seq = detail.get("application_sequence")
    assert app_seq is not None, "CR-B missing application_sequence"
    assert app_seq > _STATE["cr_a_seq"], (
        f"CR-B application_sequence ({app_seq}) must be > CR-A ({_STATE['cr_a_seq']})"
    )
    _STATE["cr_b_seq"] = app_seq
    print(f"\n  CR-B id={cr_id} application_sequence={app_seq}")


# ---------------------------------------------------------------------------
# PHASE_3: FILO guard blocks out-of-order rollback
# ---------------------------------------------------------------------------

async def test_PHASE_3_filo_guard_blocks_cr_a_rollback():
    """Attempt per-CR rollback of CR-A while CR-B is still applied. Expect 409."""
    from app.mcp_tools.change_requests import get_change_request

    if "cr_a_id" not in _STATE or "cr_b_id" not in _STATE:
        pytest.skip("PHASE_1/2 did not run")

    token = _env("API_TOKEN")
    cr_a_id = _STATE["cr_a_id"]
    cr_b_id = _STATE["cr_b_id"]

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(
        base_url=_BASE_URL,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=30.0,
    ) as client:
        resp = await client.post(f"/change-requests/{cr_a_id}/rollback")

    assert resp.status_code == 409, (
        f"Expected 409 (FILO guard), got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert body.get("error") == "out_of_order_rollback", f"Unexpected error: {body}"
    assert cr_b_id in body.get("blocking_crs", []), (
        f"CR-B not in blocking_crs: {body.get('blocking_crs')}"
    )

    # CR-A must still be completed (rollback did not proceed)
    detail = await get_change_request(token=token, cr_id=cr_a_id)
    assert detail.get("status") == "completed", (
        f"CR-A status changed despite 409: {detail.get('status')}"
    )
    print(f"\n  FILO guard correctly blocked rollback of CR-A, blocking_crs={body['blocking_crs']}")


# ---------------------------------------------------------------------------
# PHASE_4: Asset rollback-all
# ---------------------------------------------------------------------------

async def test_PHASE_4_asset_rollback_all():
    """POST /assets/{id}/rollback-all rolls back CR-B then CR-A in FILO order."""
    from app.mcp_tools.change_requests import get_change_request

    if "cr_a_id" not in _STATE or "cr_b_id" not in _STATE:
        pytest.skip("PHASE_1/2 did not run")

    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")
    cr_a_id = _STATE["cr_a_id"]
    cr_b_id = _STATE["cr_b_id"]

    jwt = await _get_jwt(token)
    async with httpx.AsyncClient(
        base_url=_BASE_URL,
        headers={"Authorization": f"Bearer {jwt}"},
        timeout=300.0,  # rollback-all can take a while
    ) as client:
        resp = await client.post(f"/assets/{asset_id}/rollback-all")

    assert resp.status_code == 200, f"rollback-all failed ({resp.status_code}): {resp.text}"
    body = resp.json()
    assert body.get("failed_at") is None, f"rollback-all stopped at: {body.get('failed_at')}: {body}"
    rolled_back = body.get("rolled_back", [])
    assert cr_b_id in rolled_back, f"CR-B not in rolled_back: {rolled_back}"
    assert cr_a_id in rolled_back, f"CR-A not in rolled_back: {rolled_back}"

    # CR-B must appear before CR-A (FILO: newest rolled back first)
    assert rolled_back.index(cr_b_id) < rolled_back.index(cr_a_id), (
        f"Expected CR-B before CR-A in rollback order. Got: {rolled_back}"
    )

    # Confirm CR statuses via MCP tool
    cr_a_detail = await get_change_request(token=token, cr_id=cr_a_id)
    cr_b_detail = await get_change_request(token=token, cr_id=cr_b_id)
    assert cr_b_detail.get("status") == "rolled_back", f"CR-B status: {cr_b_detail.get('status')}"
    assert cr_a_detail.get("status") == "rolled_back", f"CR-A status: {cr_a_detail.get('status')}"
    print(f"\n  rollback-all rolled back: {rolled_back}")


# ---------------------------------------------------------------------------
# PHASE_5: Project rollback with to_cr_id
# ---------------------------------------------------------------------------

async def test_PHASE_5_project_rollback_with_to_cr_id():
    """Create project with 2 CRs, execute both, partial rollback with to_cr_id, then full cleanup."""
    from app.mcp_tools.change_requests import get_change_request
    from app.mcp_tools.projects import (
        add_cr_to_project,
        create_project,
        rollback_project,
    )

    token = _env("API_TOKEN")
    asset_id = _env("ASSET_ID")

    # Create project
    proj = await create_project(
        token=token,
        name="FILO smoke project",
        goal="Test to_cr_id partial rollback",
        description="Automated smoke — safe to delete",
    )
    assert "id" in proj, f"create_project failed: {proj}"
    project_id = proj["id"]
    _STATE["project_id"] = project_id

    # Execute CR-C (seq_order=1 in project) — use tcp_keepalive_intvl to avoid conflict
    cr_c_id = await _create_and_execute_sysctl_cr(
        token=token,
        asset_id=asset_id,
        param="net.ipv4.tcp_keepalive_intvl",
        value=74,
        title="FILO smoke CR-C — tcp_keepalive_intvl=74",
    )
    _STATE["cr_c_id"] = cr_c_id
    added_c = await add_cr_to_project(token=token, project_id=project_id, cr_id=cr_c_id)
    assert "error" not in added_c, f"add_cr_to_project CR-C failed: {added_c}"

    # Execute CR-D (seq_order=2 in project)
    cr_d_id = await _create_and_execute_sysctl_cr(
        token=token,
        asset_id=asset_id,
        param="net.ipv4.tcp_keepalive_intvl",
        value=73,
        title="FILO smoke CR-D — tcp_keepalive_intvl=73",
    )
    _STATE["cr_d_id"] = cr_d_id
    added_d = await add_cr_to_project(token=token, project_id=project_id, cr_id=cr_d_id)
    assert "error" not in added_d, f"add_cr_to_project CR-D failed: {added_d}"

    # Partial rollback: to_cr_id=CR-D means roll back only CR-D (most recent)
    partial = await rollback_project(
        token=token, project_id=project_id, to_cr_id=cr_d_id
    )
    assert "error" not in partial, f"partial rollback_project failed: {partial}"
    assert partial.get("rollback_initiated") is True, f"rollback not initiated: {partial}"

    # Wait for CR-D to be rolled back (poll up to 60s)
    for _ in range(12):
        await asyncio.sleep(5)
        cr_d_detail = await get_change_request(token=token, cr_id=cr_d_id)
        if cr_d_detail.get("status") == "rolled_back":
            break
    else:
        pytest.fail(f"CR-D not rolled back after 60s: {cr_d_detail}")

    # CR-C must still be completed (not rolled back — outside to_cr_id range)
    cr_c_detail = await get_change_request(token=token, cr_id=cr_c_id)
    assert cr_c_detail.get("status") == "completed", (
        f"CR-C should still be completed but got: {cr_c_detail.get('status')}"
    )
    print(f"\n  Partial rollback: CR-D rolled back, CR-C still completed")

    # Full cleanup: rollback without to_cr_id (rolls back CR-C)
    full = await rollback_project(token=token, project_id=project_id)
    assert "error" not in full, f"full rollback_project failed: {full}"
    assert full.get("rollback_initiated") is True

    for _ in range(12):
        await asyncio.sleep(5)
        cr_c_detail = await get_change_request(token=token, cr_id=cr_c_id)
        if cr_c_detail.get("status") == "rolled_back":
            break
    else:
        pytest.fail(f"CR-C not rolled back after 60s: {cr_c_detail}")

    print(f"\n  Full cleanup: CR-C rolled back. All sysctl params restored.")
```

- [ ] **Step 2: Verify the smoke test file is syntactically valid**

```bash
cd backend
python -m py_compile tests/smoke/test_smoke_filo_rollback.py && echo "syntax OK"
```
Expected: `syntax OK`

- [ ] **Step 3: Run the smoke test on EC2**

SSH to EC2 and run inside the backend container:
```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39
```
Then on EC2:
```bash
cd /home/ec2-user/nexplane
git pull
docker exec nexplane-backend-1 bash -c "
  cd /app &&
  ASSET_ID=1a7051be-7110-4a21-9cdf-b023231cdff8 \
  API_TOKEN=nxp_smoke_planning_3Q-dJ8zWvz8UcspMZFYhvgg0On2NqbhW \
  pytest tests/smoke/test_smoke_filo_rollback.py -v -s 2>&1 | tee /tmp/filo_smoke.log
"
cat /tmp/filo_smoke.log | tail -30
```

Expected: `5 passed` — all phases pass. If any fail, check `/tmp/filo_smoke.log` for the error.

Common failure modes:
- **PHASE_1/2 timeout**: Agent not running as root. Start agent: `sudo nohup /home/ec2-user/nexplane/agent/nexplane-agent-linux-amd64 -control-plane http://172.18.0.5:8000 -secret sk-agent-16e78dc83aca4958395b46733dd95d434ba80979342af3a6 -mode service -poll-interval 5s &`
- **PHASE_3 401**: JWT generation failed — check `app.services.auth_service.create_access_token` signature and `ApiToken.user_id` field name.
- **PHASE_4 no CRs found**: CRs from PHASE_1/2 may not have `target_asset_ids` set correctly — check that `create_change_request` with `asset_id` populates `target_asset_ids` in the CR.
- **PHASE_5 partial rollback not working**: Task 1 fix may not be deployed — verify `projects.py` was updated on EC2 (`git pull` + restart container).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_smoke_filo_rollback.py
git commit -m "test(smoke): add FILO rollback smoke — 5 phases (FILO guard, rollback-all, project partial rollback)"
```

After all 5 phases pass on EC2, add a final commit updating the progress ledger:
```bash
# Update .superpowers/sdd/progress.md to record completion
git add .superpowers/sdd/progress.md
git commit -m "docs: record FILO rollback stack complete in progress ledger"
```

---

## Self-Review Checklist

After writing all tasks, verify against spec:

- [x] Fix A: `to_cr_id` enforcement — Task 1 ✓
- [x] Fix B: `application_sequence`-first ordering — Task 2 ✓
- [x] PHASE_1: Execute CR-A, verify `application_sequence` — Task 3 ✓
- [x] PHASE_2: Execute CR-B, verify monotonic ordering — Task 3 ✓
- [x] PHASE_3: FILO guard blocks CR-A rollback while CR-B applied — Task 3 ✓
- [x] PHASE_4: Asset rollback-all, verify FILO order in response — Task 3 ✓
- [x] PHASE_5: Project rollback with `to_cr_id` partial + full cleanup — Task 3 ✓
- [x] Divergence warning when ordering differs — Task 2 unit test ✓
- [x] No `from __future__ import annotations` added to any MCP file ✓
- [x] Smoke verifies CR status via `get_change_request`, not just absence of error ✓
- [x] All phases run on single existing platform asset ✓
