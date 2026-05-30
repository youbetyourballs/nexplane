# Linux Security Policy Auto-Generation SP1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a soak-session pipeline that observes workload syscalls via `seccomp_learn`, synthesizes a seccomp JSON profile, diffs against a stored baseline, and proposes a `configure_seccomp` CR — with the session/baseline infrastructure generic enough that SP2 (AppArmor) and SP3 (eBPF) plug in without a rewrite.

**Architecture:** A `SecurityPolicySoakSession` model scoped to a CR project orchestrates `seccomp_learn` dispatcher calls across project assets on session start. On stop, a synthesizer aggregates `syscalls_seen` lists into a seccomp JSON allowlist, computes a delta against the project's stored `SecurityPolicyBaseline`, and either auto-proposes a `configure_seccomp` CR (no prior baseline) or gates on operator approval (baseline exists). The `configure_seccomp` agent executor is already built on the Go side; the Python rollback bug is fixed in Task 1.

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL JSONB, existing `_dispatch.dispatch_agent_job`, existing `ChangeRequest` model, React + Tailwind, existing project detail page.

---

## File Map

**Create:**
- `backend/app/models/security_policy.py` — `SecurityPolicySoakSession` + `SecurityPolicyBaseline` SQLAlchemy models
- `backend/app/schemas/security_policy.py` — Pydantic request/response schemas
- `backend/app/services/security_policy/synthesizer.py` — profile synthesis + delta computation (pure functions, no DB)
- `backend/app/services/security_policy/soak_service.py` — session orchestration, observation dispatch, CR creation
- `backend/app/routers/security_policy.py` — 6 API endpoints
- `backend/alembic/versions/066_add_security_policy_soak.py` — migration for two new tables
- `backend/tests/unit/test_security_policy_synthesizer.py` — synthesizer unit tests
- `backend/tests/unit/test_soak_service_logic.py` — soak service unit tests (mocked DB + dispatcher)
- `frontend/src/components/SecurityPolicySoakPanel.tsx` — soak session panel for project detail page

**Modify:**
- `backend/app/connectors/executors/nexplane_agent/configure_seccomp.py` — fix rollback bug (passing `snapshot_id` instead of `snapshot` + `service_name`)
- `backend/app/models/__init__.py` — import new models
- `backend/app/main.py` — register security_policy router
- `frontend/src/pages/ProjectDetail.tsx` — add `<SecurityPolicySoakPanel>` below CR list

---

## Task 1: Fix configure_seccomp Rollback Bug

The existing rollback in `backend/app/connectors/executors/nexplane_agent/configure_seccomp.py` passes `snapshot_id` to the agent, but the Go agent handler (`seccomp_linux.go`) expects `snapshot` (string content) and `service_name`. This breaks rollback for any configure_seccomp CR.

**Files:**
- Modify: `backend/app/connectors/executors/nexplane_agent/configure_seccomp.py`
- Test: `backend/tests/unit/test_configure_seccomp_rollback.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_configure_seccomp_rollback.py
"""Verify configure_seccomp rollback passes correct parameters to the agent dispatcher."""
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_rollback_passes_snapshot_and_service_name():
    """Rollback must pass 'snapshot' and 'service_name', not 'snapshot_id'."""
    from app.connectors.executors.nexplane_agent import configure_seccomp

    execution_result = {
        "_asset_ids": ["asset-uuid-1"],
        "service_name": "nginx",
        "snapshot": "[Service]\nSeccompFilter=/old/path.json\n",
    }
    parameters = {"service_name": "nginx", "profile": "{}"}

    captured = {}

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        captured["command"] = command
        captured["parameters"] = parameters
        captured["asset_ids"] = asset_ids
        return {"rolled_back": True}

    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        side_effect=fake_dispatch,
    ):
        result = await configure_seccomp.rollback(parameters, execution_result, connector=None)

    assert captured["command"] == "configure_seccomp"
    assert captured["parameters"]["snapshot"] == "[Service]\nSeccompFilter=/old/path.json\n"
    assert captured["parameters"]["service_name"] == "nginx"
    assert "snapshot_id" not in captured["parameters"]
    assert result["rolled_back"] is True


@pytest.mark.asyncio
async def test_rollback_with_no_snapshot_passes_empty_string():
    """When no prior config existed, snapshot should be empty string (agent removes the drop-in)."""
    from app.connectors.executors.nexplane_agent import configure_seccomp

    execution_result = {
        "_asset_ids": ["asset-uuid-1"],
        "service_name": "nginx",
        "snapshot": "",
    }
    parameters = {"service_name": "nginx", "profile": "{}"}

    captured = {}

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        captured["parameters"] = parameters
        return {"rolled_back": True}

    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        side_effect=fake_dispatch,
    ):
        await configure_seccomp.rollback(parameters, execution_result, connector=None)

    assert captured["parameters"]["snapshot"] == ""
    assert captured["parameters"]["service_name"] == "nginx"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker exec nexplane-backend-1 pytest backend/tests/unit/test_configure_seccomp_rollback.py -v
```

Expected: FAIL — `captured["parameters"]` has `snapshot_id` not `snapshot`.

- [ ] **Step 3: Fix the rollback**

```python
# backend/app/connectors/executors/nexplane_agent/configure_seccomp.py
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="configure_seccomp",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await _dispatch.dispatch_agent_job(
        command="configure_seccomp",
        parameters={
            "action": "restore",
            "service_name": execution_result.get("service_name", parameters.get("service_name", "")),
            "snapshot": execution_result.get("snapshot", ""),
        },
        asset_ids=asset_ids,
        timeout_seconds=120,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker exec nexplane-backend-1 pytest backend/tests/unit/test_configure_seccomp_rollback.py -v
```

Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/configure_seccomp.py \
        backend/tests/unit/test_configure_seccomp_rollback.py
git commit -m "fix: configure_seccomp rollback passes snapshot+service_name not snapshot_id"
```

---

## Task 2: DB Models + Migration

**Files:**
- Create: `backend/app/models/security_policy.py`
- Create: `backend/alembic/versions/066_add_security_policy_soak.py`
- Modify: `backend/app/models/__init__.py`

- [ ] **Step 1: Write the model file**

```python
# backend/app/models/security_policy.py
import uuid
import enum
from datetime import datetime
from sqlalchemy import String, Boolean, Integer, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PGUUID, JSONB
from app.database import Base


class SoakSessionStatus(str, enum.Enum):
    running = "running"
    stopped = "stopped"
    synthesized = "synthesized"
    cr_proposed = "cr_proposed"


class PolicyType(str, enum.Enum):
    seccomp = "seccomp"
    apparmor = "apparmor"
    selinux = "selinux"
    network_policy = "network_policy"


class SecurityPolicySoakSession(Base):
    __tablename__ = "security_policy_soak_sessions"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_type: Mapped[str] = mapped_column(String(32), nullable=False, default="seccomp")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=600)
    asset_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    raw_observations: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    synthesized_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    baseline_delta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    partial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cr_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SecurityPolicyBaseline(Base):
    __tablename__ = "security_policy_baselines"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    policy_type: Mapped[str] = mapped_column(String(32), nullable=False, default="seccomp")
    profile: Mapped[dict] = mapped_column(JSONB, nullable=False)
    cr_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 2: Write the migration**

```python
# backend/alembic/versions/066_add_security_policy_soak.py
"""add security_policy_soak_sessions and security_policy_baselines tables

Revision ID: 066_security_policy_soak
Revises: 065_ldap_change_type
Create Date: 2026-05-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "066_security_policy_soak"
down_revision: str | None = "065_ldap_change_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "security_policy_soak_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_type", sa.String(32), nullable=False, server_default="seccomp"),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("window_seconds", sa.Integer, nullable=False, server_default="600"),
        sa.Column("asset_ids", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("raw_observations", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("synthesized_profile", postgresql.JSONB, nullable=True),
        sa.Column("baseline_delta", postgresql.JSONB, nullable=True),
        sa.Column("partial", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("cr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('running','stopped','synthesized','cr_proposed')",
            name="ck_soak_sessions_status",
        ),
        sa.CheckConstraint(
            "policy_type IN ('seccomp','apparmor','selinux','network_policy')",
            name="ck_soak_sessions_policy_type",
        ),
    )
    op.create_index("ix_soak_sessions_organization_id", "security_policy_soak_sessions", ["organization_id"])
    op.create_index("ix_soak_sessions_project_id", "security_policy_soak_sessions", ["project_id"])

    op.create_table(
        "security_policy_baselines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_type", sa.String(32), nullable=False, server_default="seccomp"),
        sa.Column("profile", postgresql.JSONB, nullable=False),
        sa.Column("cr_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("change_requests.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "policy_type", name="uq_baselines_project_policy_type"),
    )
    op.create_index("ix_baselines_organization_id", "security_policy_baselines", ["organization_id"])
    op.create_index("ix_baselines_project_id", "security_policy_baselines", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_baselines_project_id", table_name="security_policy_baselines")
    op.drop_index("ix_baselines_organization_id", table_name="security_policy_baselines")
    op.drop_table("security_policy_baselines")
    op.drop_index("ix_soak_sessions_project_id", table_name="security_policy_soak_sessions")
    op.drop_index("ix_soak_sessions_organization_id", table_name="security_policy_soak_sessions")
    op.drop_table("security_policy_soak_sessions")
```

- [ ] **Step 3: Register models in `__init__.py`**

Add at the end of `backend/app/models/__init__.py`:

```python
from app.models.security_policy import SecurityPolicySoakSession, SecurityPolicyBaseline  # noqa: F401
```

- [ ] **Step 4: Run the migration**

```bash
docker exec nexplane-backend-1 alembic upgrade head
```

Expected: `Running upgrade 065_ldap_change_type -> 066_security_policy_soak, add security_policy_soak_sessions...`

- [ ] **Step 5: Verify tables exist**

```bash
docker exec nexplane-backend-1 python -c "
from app.database import Base
from app.models.security_policy import SecurityPolicySoakSession, SecurityPolicyBaseline
print('SecurityPolicySoakSession table:', SecurityPolicySoakSession.__tablename__)
print('SecurityPolicyBaseline table:', SecurityPolicyBaseline.__tablename__)
print('OK')
"
```

Expected: prints both table names and `OK`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/security_policy.py \
        backend/alembic/versions/066_add_security_policy_soak.py \
        backend/app/models/__init__.py
git commit -m "feat: add SecurityPolicySoakSession and SecurityPolicyBaseline models + migration 066"
```

---

## Task 3: Profile Synthesizer (TDD)

Pure functions — no DB, no async. Takes observation dicts, returns seccomp JSON and delta dicts.

**Files:**
- Create: `backend/app/services/security_policy/synthesizer.py`
- Create: `backend/tests/unit/test_security_policy_synthesizer.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_security_policy_synthesizer.py
"""Unit tests for the seccomp profile synthesizer."""
import pytest
from app.services.security_policy.synthesizer import synthesize_seccomp, compute_delta


def test_synthesize_merges_all_asset_syscalls():
    raw = {
        "asset-1": ["read", "write", "open"],
        "asset-2": ["read", "close", "mmap"],
    }
    profile = synthesize_seccomp(raw)
    allowed = set(profile["syscalls"][0]["names"])
    assert allowed == {"read", "write", "open", "close", "mmap"}


def test_synthesize_deduplicates():
    raw = {"asset-1": ["read", "write", "read"], "asset-2": ["write"]}
    profile = synthesize_seccomp(raw)
    names = profile["syscalls"][0]["names"]
    assert names == sorted(set(names))
    assert names.count("read") == 1


def test_synthesize_structure():
    raw = {"asset-1": ["read"]}
    profile = synthesize_seccomp(raw)
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"
    assert "SCMP_ARCH_X86_64" in profile["architectures"]
    assert profile["syscalls"][0]["action"] == "SCMP_ACT_ALLOW"


def test_synthesize_empty_observations():
    profile = synthesize_seccomp({})
    assert profile["syscalls"][0]["names"] == []


def test_synthesize_empty_asset_list():
    raw = {"asset-1": [], "asset-2": []}
    profile = synthesize_seccomp(raw)
    assert profile["syscalls"][0]["names"] == []


def test_compute_delta_added_and_removed():
    prior = _make_profile(["read", "write", "open"])
    current = _make_profile(["read", "write", "mmap"])
    delta = compute_delta(prior, current)
    assert delta["added"] == ["mmap"]
    assert delta["removed"] == ["open"]


def test_compute_delta_no_change():
    prior = _make_profile(["read", "write"])
    current = _make_profile(["read", "write"])
    delta = compute_delta(prior, current)
    assert delta["added"] == []
    assert delta["removed"] == []


def test_compute_delta_only_additions():
    prior = _make_profile(["read"])
    current = _make_profile(["read", "write", "open"])
    delta = compute_delta(prior, current)
    assert set(delta["added"]) == {"write", "open"}
    assert delta["removed"] == []


def test_compute_delta_only_removals():
    prior = _make_profile(["read", "write", "open"])
    current = _make_profile(["read"])
    delta = compute_delta(prior, current)
    assert delta["added"] == []
    assert set(delta["removed"]) == {"write", "open"}


def _make_profile(syscalls: list[str]) -> dict:
    return {
        "defaultAction": "SCMP_ACT_ERRNO",
        "architectures": ["SCMP_ARCH_X86_64"],
        "syscalls": [{"names": sorted(syscalls), "action": "SCMP_ACT_ALLOW"}],
    }
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 pytest backend/tests/unit/test_security_policy_synthesizer.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.security_policy'`

- [ ] **Step 3: Write the synthesizer**

Create `backend/app/services/security_policy/__init__.py` (empty):
```python
```

Create `backend/app/services/security_policy/synthesizer.py`:
```python
# backend/app/services/security_policy/synthesizer.py


def synthesize_seccomp(raw_observations: dict[str, list[str]]) -> dict:
    """Merge per-asset syscall lists into a single seccomp allowlist profile."""
    all_syscalls: set[str] = set()
    for syscalls in raw_observations.values():
        all_syscalls.update(syscalls)
    return {
        "defaultAction": "SCMP_ACT_ERRNO",
        "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
        "syscalls": [{"names": sorted(all_syscalls), "action": "SCMP_ACT_ALLOW"}],
    }


def compute_delta(prior: dict, current: dict) -> dict:
    """Return {added, removed} syscall sets between two seccomp profiles."""
    prior_set = set(prior["syscalls"][0]["names"])
    current_set = set(current["syscalls"][0]["names"])
    return {
        "added": sorted(current_set - prior_set),
        "removed": sorted(prior_set - current_set),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker exec nexplane-backend-1 pytest backend/tests/unit/test_security_policy_synthesizer.py -v
```

Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/security_policy/__init__.py \
        backend/app/services/security_policy/synthesizer.py \
        backend/tests/unit/test_security_policy_synthesizer.py
git commit -m "feat: add seccomp profile synthesizer with delta computation"
```

---

## Task 4: Soak Service

Orchestrates observation dispatch and CR creation. Uses `_dispatch.dispatch_agent_job` directly (seccomp_learn has no persistent state, so no CR needed for the observation phase).

**Files:**
- Create: `backend/app/services/security_policy/soak_service.py`
- Create: `backend/tests/unit/test_soak_service_logic.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/unit/test_soak_service_logic.py
"""Unit tests for SoakService business logic."""
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_session(raw_observations=None, synthesized_profile=None, baseline_delta=None,
                  status="running", cr_id=None, partial=False):
    s = MagicMock()
    s.id = uuid.uuid4()
    s.organization_id = uuid.uuid4()
    s.project_id = uuid.uuid4()
    s.policy_type = "seccomp"
    s.status = status
    s.window_seconds = 30
    s.asset_ids = ["asset-1", "asset-2"]
    s.raw_observations = raw_observations or {}
    s.synthesized_profile = synthesized_profile
    s.baseline_delta = baseline_delta
    s.partial = partial
    s.cr_id = cr_id
    return s


@pytest.mark.asyncio
async def test_collect_observations_aggregates_syscalls():
    from app.services.security_policy.soak_service import _collect_observations

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        if asset_ids == ["asset-1"]:
            return {"syscalls_seen": ["read", "write"], "_asset_ids": ["asset-1"]}
        return {"syscalls_seen": ["open", "close"], "_asset_ids": ["asset-2"]}

    with patch(
        "app.services.security_policy.soak_service._dispatch.dispatch_agent_job",
        side_effect=fake_dispatch,
    ):
        obs, partial = await _collect_observations(
            asset_ids=["asset-1", "asset-2"],
            window_seconds=0,
        )

    assert obs["asset-1"] == ["read", "write"]
    assert obs["asset-2"] == ["open", "close"]
    assert partial is False


@pytest.mark.asyncio
async def test_collect_observations_marks_partial_on_failure():
    from app.services.security_policy.soak_service import _collect_observations

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        if asset_ids == ["asset-1"]:
            raise RuntimeError("agent unreachable")
        return {"syscalls_seen": ["read"], "_asset_ids": ["asset-2"]}

    with patch(
        "app.services.security_policy.soak_service._dispatch.dispatch_agent_job",
        side_effect=fake_dispatch,
    ):
        obs, partial = await _collect_observations(
            asset_ids=["asset-1", "asset-2"],
            window_seconds=0,
        )

    assert "asset-1" not in obs
    assert obs["asset-2"] == ["read"]
    assert partial is True


def test_build_cr_params_no_baseline():
    from app.services.security_policy.soak_service import _build_cr_params

    profile = {"defaultAction": "SCMP_ACT_ERRNO", "syscalls": [{"names": ["read"], "action": "SCMP_ACT_ALLOW"}]}
    session = _make_session()
    params = _build_cr_params(session=session, profile=profile, service_name="nginx")

    assert params["service_name"] == "nginx"
    import json
    loaded = json.loads(params["profile"])
    assert loaded["syscalls"][0]["names"] == ["read"]
    assert params["session_id"] == str(session.id)


def test_should_auto_propose_when_no_baseline():
    from app.services.security_policy.soak_service import should_auto_propose
    assert should_auto_propose(baseline=None) is True


def test_should_not_auto_propose_when_baseline_exists():
    from app.services.security_policy.soak_service import should_auto_propose
    baseline = MagicMock()
    assert should_auto_propose(baseline=baseline) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 pytest backend/tests/unit/test_soak_service_logic.py -v
```

Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the soak service**

```python
# backend/app/services/security_policy/soak_service.py
"""Orchestrates soak session lifecycle: observation dispatch, synthesis, CR creation."""
from __future__ import annotations
import asyncio
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.executors.nexplane_agent import _dispatch
from app.models.change_request import ChangeRequest, ChangeRequestStatus, ChangeType, RiskLevel
from app.models.security_policy import SecurityPolicySoakSession, SecurityPolicyBaseline
from app.services.security_policy.synthesizer import compute_delta, synthesize_seccomp


async def _collect_observations(
    asset_ids: list[str],
    window_seconds: int,
) -> tuple[dict[str, list[str]], bool]:
    """Run seccomp_learn on each asset concurrently. Returns (observations, partial)."""
    partial = False

    async def _observe_one(asset_id: str) -> tuple[str, list[str] | None]:
        try:
            result = await _dispatch.dispatch_agent_job(
                command="seccomp_learn",
                parameters={"duration_seconds": window_seconds},
                asset_ids=[asset_id],
                timeout_seconds=window_seconds + 60,
            )
            return asset_id, result.get("syscalls_seen", [])
        except Exception:
            return asset_id, None

    results = await asyncio.gather(*[_observe_one(aid) for aid in asset_ids])
    observations: dict[str, list[str]] = {}
    for asset_id, syscalls in results:
        if syscalls is None:
            partial = True
        else:
            observations[asset_id] = syscalls
    return observations, partial


def _build_cr_params(session: SecurityPolicySoakSession, profile: dict, service_name: str) -> dict:
    return {
        "session_id": str(session.id),
        "service_name": service_name,
        "profile": json.dumps(profile),
    }


def should_auto_propose(baseline: SecurityPolicyBaseline | None) -> bool:
    return baseline is None


async def start_session(
    db: AsyncSession,
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    policy_type: str,
    asset_ids: list[str],
    window_seconds: int,
    user_id: uuid.UUID,
) -> SecurityPolicySoakSession:
    session = SecurityPolicySoakSession(
        organization_id=organization_id,
        project_id=project_id,
        policy_type=policy_type,
        asset_ids=asset_ids,
        window_seconds=window_seconds,
        status="running",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def stop_and_synthesize(
    db: AsyncSession,
    session: SecurityPolicySoakSession,
    service_name: str,
    user_id: uuid.UUID,
) -> SecurityPolicySoakSession:
    """Collect observations, synthesize profile, compare baseline, create CR if auto-propose."""
    observations, partial = await _collect_observations(
        asset_ids=[str(a) for a in session.asset_ids],
        window_seconds=session.window_seconds,
    )

    profile = synthesize_seccomp(observations)

    # Fetch baseline
    result = await db.execute(
        select(SecurityPolicyBaseline).where(
            SecurityPolicyBaseline.project_id == session.project_id,
            SecurityPolicyBaseline.policy_type == session.policy_type,
        )
    )
    baseline = result.scalar_one_or_none()

    delta = None
    if baseline:
        delta = compute_delta(baseline.profile, profile)

    session.raw_observations = observations
    session.synthesized_profile = profile
    session.baseline_delta = delta
    session.partial = partial
    session.stopped_at = datetime.now(timezone.utc)
    session.status = "synthesized"

    if should_auto_propose(baseline):
        cr = await _create_configure_seccomp_cr(db, session, profile, service_name, user_id)
        await _upsert_baseline(db, session, profile, cr.id)
        session.cr_id = cr.id
        session.status = "cr_proposed"

    await db.commit()
    await db.refresh(session)
    return session


async def accept_diff(
    db: AsyncSession,
    session: SecurityPolicySoakSession,
    service_name: str,
    user_id: uuid.UUID,
) -> SecurityPolicySoakSession:
    """Operator accepted the diff — create CR and update baseline."""
    if session.status not in ("synthesized",):
        raise ValueError(f"Session status must be 'synthesized', got {session.status!r}")
    if session.synthesized_profile is None:
        raise ValueError("Session has no synthesized profile")

    cr = await _create_configure_seccomp_cr(
        db, session, session.synthesized_profile, service_name, user_id
    )
    await _upsert_baseline(db, session, session.synthesized_profile, cr.id)
    session.cr_id = cr.id
    session.status = "cr_proposed"
    await db.commit()
    await db.refresh(session)
    return session


async def _create_configure_seccomp_cr(
    db: AsyncSession,
    session: SecurityPolicySoakSession,
    profile: dict,
    service_name: str,
    user_id: uuid.UUID,
) -> ChangeRequest:
    params = _build_cr_params(session, profile, service_name)
    cr = ChangeRequest(
        organization_id=session.organization_id,
        requester_id=user_id,
        title=f"Apply seccomp profile (project soak session {str(session.id)[:8]})",
        description=f"Seccomp profile synthesized from soak session. "
                    f"Syscalls allowed: {len(profile['syscalls'][0]['names'])}. "
                    f"Partial observation: {session.partial}.",
        change_type=ChangeType.configure_seccomp,
        target_asset_ids=[str(a) for a in session.asset_ids],
        desired_outcome=params,
        status=ChangeRequestStatus.draft,
        risk_level=RiskLevel.medium,
    )
    db.add(cr)
    await db.flush()
    return cr


async def _upsert_baseline(
    db: AsyncSession,
    session: SecurityPolicySoakSession,
    profile: dict,
    cr_id: uuid.UUID,
) -> None:
    result = await db.execute(
        select(SecurityPolicyBaseline).where(
            SecurityPolicyBaseline.project_id == session.project_id,
            SecurityPolicyBaseline.policy_type == session.policy_type,
        )
    )
    baseline = result.scalar_one_or_none()
    if baseline:
        baseline.profile = profile
        baseline.cr_id = cr_id
        baseline.updated_at = datetime.now(timezone.utc)
    else:
        baseline = SecurityPolicyBaseline(
            organization_id=session.organization_id,
            project_id=session.project_id,
            policy_type=session.policy_type,
            profile=profile,
            cr_id=cr_id,
        )
        db.add(baseline)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker exec nexplane-backend-1 pytest backend/tests/unit/test_soak_service_logic.py -v
```

Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/security_policy/soak_service.py \
        backend/tests/unit/test_soak_service_logic.py
git commit -m "feat: add security policy soak service (observation, synthesis, CR creation)"
```

---

## Task 5: Pydantic Schemas

**Files:**
- Create: `backend/app/schemas/security_policy.py`

- [ ] **Step 1: Write schemas**

```python
# backend/app/schemas/security_policy.py
import uuid
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Any


class SoakSessionCreate(BaseModel):
    project_id: uuid.UUID
    policy_type: str = "seccomp"
    asset_ids: list[uuid.UUID]
    window_seconds: int = Field(default=600, ge=30, le=86400)


class SoakSessionRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    policy_type: str
    status: str
    window_seconds: int
    asset_ids: list[Any]
    raw_observations: dict
    synthesized_profile: dict | None
    baseline_delta: dict | None
    partial: bool
    cr_id: uuid.UUID | None
    started_at: datetime
    stopped_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SoakSessionStopBody(BaseModel):
    service_name: str


class AcceptDiffBody(BaseModel):
    service_name: str


class BaselineRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    policy_type: str
    profile: dict
    cr_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 2: Verify import works**

```bash
docker exec nexplane-backend-1 python -c "from app.schemas.security_policy import SoakSessionCreate, SoakSessionRead; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/security_policy.py
git commit -m "feat: add security policy Pydantic schemas"
```

---

## Task 6: API Router

**Files:**
- Create: `backend/app/routers/security_policy.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write the router**

```python
# backend/app/routers/security_policy.py
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.security_policy import SecurityPolicySoakSession, SecurityPolicyBaseline
from app.models.user import User
from app.routers import current_user
from app.schemas.security_policy import (
    AcceptDiffBody, BaselineRead, SoakSessionCreate, SoakSessionRead, SoakSessionStopBody,
)
from app.services.security_policy import soak_service

router = APIRouter(prefix="/security-policy", tags=["Security Policy"])


@router.post("/soak-sessions", response_model=SoakSessionRead, status_code=201)
async def start_soak_session(
    body: SoakSessionCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await soak_service.start_session(
        db=db,
        organization_id=user.organization_id,
        project_id=body.project_id,
        policy_type=body.policy_type,
        asset_ids=[str(a) for a in body.asset_ids],
        window_seconds=body.window_seconds,
        user_id=user.id,
    )
    return session


@router.get("/soak-sessions/{session_id}", response_model=SoakSessionRead)
async def get_soak_session(
    session_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SecurityPolicySoakSession).where(
            SecurityPolicySoakSession.id == session_id,
            SecurityPolicySoakSession.organization_id == user.organization_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Soak session not found")
    return session


@router.post("/soak-sessions/{session_id}/stop", response_model=SoakSessionRead)
async def stop_soak_session(
    session_id: uuid.UUID,
    body: SoakSessionStopBody,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SecurityPolicySoakSession).where(
            SecurityPolicySoakSession.id == session_id,
            SecurityPolicySoakSession.organization_id == user.organization_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Soak session not found")
    if session.status != "running":
        raise HTTPException(status_code=409, detail=f"Session is not running (status={session.status})")
    session = await soak_service.stop_and_synthesize(
        db=db, session=session, service_name=body.service_name, user_id=user.id
    )
    return session


@router.get("/soak-sessions/{session_id}/diff")
async def get_diff(
    session_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SecurityPolicySoakSession).where(
            SecurityPolicySoakSession.id == session_id,
            SecurityPolicySoakSession.organization_id == user.organization_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Soak session not found")
    if session.status == "running":
        raise HTTPException(status_code=409, detail="Session is still running")
    if session.baseline_delta is None:
        raise HTTPException(status_code=404, detail="No prior baseline — session will auto-propose CR")
    return session.baseline_delta


@router.post("/soak-sessions/{session_id}/accept", response_model=SoakSessionRead)
async def accept_diff(
    session_id: uuid.UUID,
    body: AcceptDiffBody,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SecurityPolicySoakSession).where(
            SecurityPolicySoakSession.id == session_id,
            SecurityPolicySoakSession.organization_id == user.organization_id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Soak session not found")
    try:
        session = await soak_service.accept_diff(
            db=db, session=session, service_name=body.service_name, user_id=user.id
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return session


@router.get("/baselines/{project_id}", response_model=BaselineRead)
async def get_baseline(
    project_id: uuid.UUID,
    policy_type: str = "seccomp",
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SecurityPolicyBaseline).where(
            SecurityPolicyBaseline.project_id == project_id,
            SecurityPolicyBaseline.organization_id == user.organization_id,
            SecurityPolicyBaseline.policy_type == policy_type,
        )
    )
    baseline = result.scalar_one_or_none()
    if not baseline:
        raise HTTPException(status_code=404, detail="No baseline for this project")
    return baseline
```

- [ ] **Step 2: Register in `main.py`**

Add after the `backup_router` import and `include_router` line in `backend/app/main.py`:

```python
# Add to imports:
from app.routers import security_policy as security_policy_router

# Add after app.include_router(backup_router.router):
app.include_router(security_policy_router.router)
```

- [ ] **Step 3: Restart backend and verify router loads**

```bash
docker compose restart backend
sleep 5
curl -s http://localhost:8000/openapi.json | python3 -c "import sys,json; paths=json.load(sys.stdin)['paths']; print([p for p in paths if 'security-policy' in p])"
```

Expected: list of 6 `/security-policy/...` paths.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/security_policy.py backend/app/main.py
git commit -m "feat: add security policy API router (6 endpoints)"
```

---

## Task 7: Frontend — Soak Session Panel

Add a collapsible panel to the Project detail page for starting, monitoring, and accepting soak sessions.

**Files:**
- Create: `frontend/src/components/SecurityPolicySoakPanel.tsx`
- Modify: `frontend/src/pages/ProjectDetail.tsx`

- [ ] **Step 1: Write the panel component**

```tsx
// frontend/src/components/SecurityPolicySoakPanel.tsx
import { useState } from "react";
import { useAuth } from "../context/AuthContext";

interface Asset {
  id: string;
  name: string;
}

interface SoakSession {
  id: string;
  status: string;
  policy_type: string;
  window_seconds: number;
  synthesized_profile: { syscalls: { names: string[] }[] } | null;
  baseline_delta: { added: string[]; removed: string[] } | null;
  cr_id: string | null;
  partial: boolean;
  stopped_at: string | null;
}

interface Props {
  projectId: string;
  assets: Asset[];
}

export function SecurityPolicySoakPanel({ projectId, assets }: Props) {
  const { token } = useAuth();
  const [expanded, setExpanded] = useState(false);
  const [session, setSession] = useState<SoakSession | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedAssets, setSelectedAssets] = useState<string[]>([]);
  const [windowSeconds, setWindowSeconds] = useState(600);
  const [serviceName, setServiceName] = useState("");

  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };

  async function startSession() {
    if (!selectedAssets.length || !serviceName) {
      setError("Select at least one asset and enter a service name.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await fetch("/api/security-policy/soak-sessions", {
        method: "POST",
        headers,
        body: JSON.stringify({
          project_id: projectId,
          policy_type: "seccomp",
          asset_ids: selectedAssets,
          window_seconds: windowSeconds,
        }),
      });
      if (!r.ok) throw new Error(await r.text());
      setSession(await r.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function stopSession() {
    if (!session || !serviceName) return;
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`/api/security-policy/soak-sessions/${session.id}/stop`, {
        method: "POST",
        headers,
        body: JSON.stringify({ service_name: serviceName }),
      });
      if (!r.ok) throw new Error(await r.text());
      setSession(await r.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function acceptDiff() {
    if (!session || !serviceName) return;
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`/api/security-policy/soak-sessions/${session.id}/accept`, {
        method: "POST",
        headers,
        body: JSON.stringify({ service_name: serviceName }),
      });
      if (!r.ok) throw new Error(await r.text());
      setSession(await r.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  const syscallCount = session?.synthesized_profile?.syscalls?.[0]?.names?.length ?? 0;
  const delta = session?.baseline_delta;

  return (
    <div className="mt-4 border border-slate-200 rounded-lg bg-white">
      <button
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-slate-700 hover:bg-slate-50"
        onClick={() => setExpanded(!expanded)}
      >
        <span>Security Policy Soak</span>
        <span className="text-slate-400">{expanded ? "▲" : "▼"}</span>
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-3 border-t border-slate-100">
          {error && (
            <div className="text-red-600 text-xs mt-2">{error}</div>
          )}

          {!session && (
            <>
              <div className="mt-3">
                <label className="block text-xs text-slate-500 mb-1">Service name (e.g. nginx)</label>
                <input
                  className="w-full border border-slate-300 rounded px-2 py-1 text-sm"
                  value={serviceName}
                  onChange={e => setServiceName(e.target.value)}
                  placeholder="nginx"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">
                  Window: {Math.round(windowSeconds / 60)} min
                </label>
                <input
                  type="range" min={30} max={3600} step={30}
                  value={windowSeconds}
                  onChange={e => setWindowSeconds(Number(e.target.value))}
                  className="w-full"
                />
              </div>
              <div>
                <label className="block text-xs text-slate-500 mb-1">Assets to observe</label>
                <div className="space-y-1 max-h-32 overflow-y-auto">
                  {assets.map(a => (
                    <label key={a.id} className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={selectedAssets.includes(a.id)}
                        onChange={e => setSelectedAssets(
                          e.target.checked
                            ? [...selectedAssets, a.id]
                            : selectedAssets.filter(x => x !== a.id)
                        )}
                      />
                      {a.name}
                    </label>
                  ))}
                </div>
              </div>
              <button
                className="w-full bg-indigo-600 text-white text-sm py-1.5 rounded hover:bg-indigo-700 disabled:opacity-50"
                onClick={startSession}
                disabled={loading}
              >
                {loading ? "Starting..." : "Start Soak Session"}
              </button>
            </>
          )}

          {session && session.status === "running" && (
            <div className="mt-3 space-y-2">
              <div className="text-sm text-slate-600">
                Session running — observing {session.asset_ids?.length ?? "?"} assets for {Math.round(session.window_seconds / 60)} min.
              </div>
              <input
                className="w-full border border-slate-300 rounded px-2 py-1 text-sm"
                value={serviceName}
                onChange={e => setServiceName(e.target.value)}
                placeholder="Service name (required to stop)"
              />
              <button
                className="w-full bg-orange-600 text-white text-sm py-1.5 rounded hover:bg-orange-700 disabled:opacity-50"
                onClick={stopSession}
                disabled={loading}
              >
                {loading ? "Stopping..." : "Stop & Synthesize"}
              </button>
            </div>
          )}

          {session && session.status === "synthesized" && delta && (
            <div className="mt-3 space-y-3">
              <div className="text-sm font-medium text-slate-700">Profile diff vs baseline</div>
              {session.partial && (
                <div className="text-xs text-yellow-700 bg-yellow-50 border border-yellow-200 rounded px-2 py-1">
                  Partial observation — some assets were unreachable
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="text-xs text-green-700 font-medium mb-1">Added ({delta.added.length})</div>
                  <div className="max-h-32 overflow-y-auto text-xs font-mono text-green-800 bg-green-50 rounded p-2 space-y-0.5">
                    {delta.added.length === 0
                      ? <span className="text-slate-400">none</span>
                      : delta.added.map(s => <div key={s}>{s}</div>)}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-red-700 font-medium mb-1">Removed ({delta.removed.length})</div>
                  <div className="max-h-32 overflow-y-auto text-xs font-mono text-red-800 bg-red-50 rounded p-2 space-y-0.5">
                    {delta.removed.length === 0
                      ? <span className="text-slate-400">none</span>
                      : delta.removed.map(s => <div key={s}>{s}</div>)}
                  </div>
                </div>
              </div>
              <button
                className="w-full bg-indigo-600 text-white text-sm py-1.5 rounded hover:bg-indigo-700 disabled:opacity-50"
                onClick={acceptDiff}
                disabled={loading}
              >
                {loading ? "Proposing..." : "Accept & Propose CR"}
              </button>
              <button
                className="w-full border border-slate-300 text-slate-600 text-sm py-1.5 rounded hover:bg-slate-50"
                onClick={() => setSession(null)}
              >
                Discard
              </button>
            </div>
          )}

          {session && (session.status === "cr_proposed" || (session.status === "synthesized" && !delta)) && (
            <div className="mt-3 space-y-2">
              <div className="text-sm text-green-700 font-medium">
                {syscallCount} syscalls — configure_seccomp CR proposed
              </div>
              {session.cr_id && (
                <a
                  href={`/change-requests/${session.cr_id}`}
                  className="text-indigo-600 text-sm underline"
                >
                  View CR →
                </a>
              )}
              {session.partial && (
                <div className="text-xs text-yellow-700 bg-yellow-50 border border-yellow-200 rounded px-2 py-1">
                  Partial observation — profile may not be complete
                </div>
              )}
              <button
                className="text-xs text-slate-400 hover:text-slate-600"
                onClick={() => setSession(null)}
              >
                Start new session
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Add panel to ProjectDetail**

In `frontend/src/pages/ProjectDetail.tsx`:

Find the import section and add:
```tsx
import { SecurityPolicySoakPanel } from "../components/SecurityPolicySoakPanel";
```

Find the project assets — look for where `members` or assets are rendered. Add the panel just before the closing `</div>` of the main content area (after the CR list section, around line 630):

```tsx
{/* After the CR list section, before closing the main content div */}
<SecurityPolicySoakPanel
  projectId={project.id}
  assets={(members ?? []).map((m: any) => ({
    id: m.change_request?.target_asset_ids?.[0] ?? m.id,
    name: m.change_request?.title ?? m.id,
  }))}
/>
```

Note: Check the exact variable names for project ID and asset list in ProjectDetail.tsx and adjust the props accordingly — the component expects `projectId: string` and `assets: {id, name}[]`.

- [ ] **Step 3: Restart frontend and verify**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Open a project detail page in the browser. Verify the "Security Policy Soak" collapsible panel appears at the bottom.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/SecurityPolicySoakPanel.tsx \
        frontend/src/pages/ProjectDetail.tsx
git commit -m "feat: add SecurityPolicySoakPanel to ProjectDetail page"
```

---

## Task 8: Smoke Test Phase — SECCOMP_AUTOGEN

Add a smoke test phase that exercises the full soak → synthesize → CR propose → execute → rollback flow against a live Linux asset.

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Find where to add the phase**

In `backend/tests/smoke/test_aws_live.py`, find the `PHASE_MAP` dict and the existing `VAULT_ROTATE` or `CREDENTIAL` phases as a pattern for a phase that uses an existing AMI asset.

- [ ] **Step 2: Add the SECCOMP_AUTOGEN phase**

Add to the phase functions (after the last existing phase function):

```python
def run_phase_seccomp_autogen(client, base_url, **kwargs):
    """
    Smoke test for security policy soak session auto-generation.
    Uses the Nexplane agent on an existing registered Linux asset.
    Exercises: start session → stop → profile synthesized → CR proposed → execute → rollback.
    """
    import time as _time

    log = lambda msg: print(f"  [SECCOMP_AUTOGEN] {msg}", flush=True)
    log("Starting SECCOMP_AUTOGEN smoke phase")

    # Find a registered Linux asset with a nexplane agent
    assets_r = client.get("/assets")
    assert assets_r.status_code == 200, f"GET /assets failed: {assets_r.text}"
    assets = assets_r.json()
    linux_assets = [a for a in assets if a.get("asset_type") in ("server", "ec2_instance")]
    assert linux_assets, "No Linux assets found — need at least one with agent registered"
    asset_id = linux_assets[0]["id"]
    log(f"Using asset {asset_id} ({linux_assets[0].get('name', 'unnamed')})")

    # Find a project to scope the session
    projects_r = client.get("/projects")
    assert projects_r.status_code == 200
    projects = projects_r.json()
    assert projects, "Need at least one project"
    project_id = projects[0]["id"]
    log(f"Using project {project_id} ({projects[0].get('name', 'unnamed')})")

    # 1. Start soak session (30s window for smoke)
    start_r = client.post("/security-policy/soak-sessions", json={
        "project_id": project_id,
        "policy_type": "seccomp",
        "asset_ids": [asset_id],
        "window_seconds": 30,
    })
    assert start_r.status_code == 201, f"Start session failed: {start_r.text}"
    session = start_r.json()
    session_id = session["id"]
    assert session["status"] == "running"
    log(f"Session {session_id} started, status=running")

    # 2. Stop session and synthesize (service_name uses sshd as it's always running)
    log("Stopping session and synthesizing profile...")
    stop_r = client.post(f"/security-policy/soak-sessions/{session_id}/stop", json={
        "service_name": "sshd",
    })
    assert stop_r.status_code == 200, f"Stop session failed: {stop_r.text}"
    session = stop_r.json()
    log(f"Session status after stop: {session['status']}")

    # First run: no baseline → should auto-propose CR
    assert session["status"] == "cr_proposed", (
        f"Expected cr_proposed (no prior baseline), got {session['status']}"
    )
    assert session["cr_id"] is not None, "Expected cr_id to be set after auto-propose"
    cr_id = session["cr_id"]
    log(f"CR proposed: {cr_id}")

    # 3. Verify CR is in draft/awaiting_approval state
    cr_r = client.get(f"/change-requests/{cr_id}")
    assert cr_r.status_code == 200
    cr = cr_r.json()
    assert cr["status"] in ("draft", "awaiting_approval"), f"Unexpected CR status: {cr['status']}"
    assert cr["change_type"] == "configure_seccomp"
    log(f"CR change_type=configure_seccomp, status={cr['status']} ✓")

    # 4. Verify baseline was stored
    baseline_r = client.get(f"/security-policy/baselines/{project_id}?policy_type=seccomp")
    assert baseline_r.status_code == 200, f"Expected baseline, got {baseline_r.status_code}"
    baseline = baseline_r.json()
    assert "syscalls" in baseline["profile"]
    syscall_count = len(baseline["profile"]["syscalls"][0]["names"])
    log(f"Baseline stored: {syscall_count} syscalls ✓")

    # 5. Submit CR for approval and approve
    submit_r = client.post(f"/change-requests/{cr_id}/submit-for-approval")
    assert submit_r.status_code in (200, 204), f"Submit failed: {submit_r.text}"
    approve_r = client.post(f"/change-requests/{cr_id}/approve")
    assert approve_r.status_code in (200, 204), f"Approve failed: {approve_r.text}"
    log("CR submitted and approved ✓")

    # 6. Execute CR — poll for completion
    execute_r = client.post(f"/change-requests/{cr_id}/execute")
    assert execute_r.status_code in (200, 204), f"Execute failed: {execute_r.text}"
    for _ in range(30):
        _time.sleep(5)
        cr = client.get(f"/change-requests/{cr_id}").json()
        if cr["status"] in ("completed", "failed", "rolled_back"):
            break
    assert cr["status"] == "completed", f"CR did not complete: {cr['status']}"
    log("CR executed successfully ✓")

    # 7. Rollback CR
    rollback_r = client.post(f"/change-requests/{cr_id}/rollback")
    assert rollback_r.status_code in (200, 204), f"Rollback failed: {rollback_r.text}"
    for _ in range(20):
        _time.sleep(5)
        cr = client.get(f"/change-requests/{cr_id}").json()
        if cr["status"] == "rolled_back":
            break
    assert cr["status"] == "rolled_back", f"Rollback did not complete: {cr['status']}"
    log("CR rolled back ✓")

    # 8. Run a second session to verify delta flow
    log("Starting second session to test diff flow...")
    start2_r = client.post("/security-policy/soak-sessions", json={
        "project_id": project_id,
        "policy_type": "seccomp",
        "asset_ids": [asset_id],
        "window_seconds": 30,
    })
    assert start2_r.status_code == 201
    session2_id = start2_r.json()["id"]
    stop2_r = client.post(f"/security-policy/soak-sessions/{session2_id}/stop", json={
        "service_name": "sshd",
    })
    assert stop2_r.status_code == 200
    session2 = stop2_r.json()
    # Second run: baseline exists → synthesized (not cr_proposed), delta returned
    assert session2["status"] == "synthesized", (
        f"Expected synthesized (baseline exists), got {session2['status']}"
    )
    assert session2["baseline_delta"] is not None
    assert "added" in session2["baseline_delta"]
    assert "removed" in session2["baseline_delta"]
    log(f"Delta: +{len(session2['baseline_delta']['added'])} -{len(session2['baseline_delta']['removed'])} syscalls ✓")

    # 9. Accept diff → propose second CR
    accept_r = client.post(f"/security-policy/soak-sessions/{session2_id}/accept", json={
        "service_name": "sshd",
    })
    assert accept_r.status_code == 200
    session2 = accept_r.json()
    assert session2["status"] == "cr_proposed"
    assert session2["cr_id"] is not None
    log(f"Second CR proposed: {session2['cr_id']} ✓")

    log("SECCOMP_AUTOGEN PASSED ✓")
    return {"status": "passed", "session_id": session_id, "cr_id": cr_id}
```

Add to `PHASE_MAP`:
```python
"SECCOMP_AUTOGEN": run_phase_seccomp_autogen,
```

- [ ] **Step 3: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "test: add SECCOMP_AUTOGEN smoke phase"
```

- [ ] **Step 4: Run the smoke phase**

```bash
docker exec nexplane-backend-1 bash /app/tests/smoke/run_smoke.sh SECCOMP_AUTOGEN
```

Expected: `SECCOMP_AUTOGEN PASSED ✓` in output. If agent not reachable on the first asset, check that the nexplane agent is deployed and registered for at least one Linux asset.

---

## Self-Review Checklist

- [x] **Spec coverage:**
  - Generic policy_type foundation → Tasks 2, 3 (`policy_type` enum, JSONB tables)
  - Soak session start/stop/accept API → Task 6
  - Synthesizer + delta → Task 3
  - Baseline store + upsert → Task 4
  - configure_seccomp CR creation → Task 4
  - Rollback fix → Task 1
  - Frontend panel → Task 7
  - Smoke test → Task 8

- [x] **Placeholder scan:** All code blocks are complete. No TBDs.

- [x] **Type consistency:**
  - `_collect_observations` returns `(dict[str, list[str]], bool)` — matches synthesizer input `dict[str, list[str]]`
  - `synthesize_seccomp` returns `dict` with key `syscalls[0]["names"]` — matches `compute_delta` expectation
  - `_build_cr_params` returns `service_name`, `profile` (JSON string), `session_id` — matches what `configure_seccomp` agent expects
  - `SoakSessionRead.asset_ids` typed `list[Any]` to handle both str and UUID from DB — correct

- [x] **One gap noted:** `ProjectDetail.tsx` asset extraction (Step 2, Task 7) says "check exact variable names" — intentional since ProjectDetail is ~630 lines and the correct extraction depends on the data shape. The implementer must read the file before adding the panel.
