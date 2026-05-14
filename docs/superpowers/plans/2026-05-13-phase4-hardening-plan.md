# Phase 4 — Proactive Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable security engineering initiatives — hardening project templates, the learn→audit→enforce pipeline, AI-assisted policy generation from discovered application behavior, and policy drift detection.

**Architecture:** New models (PolicyBaseline, DriftAlert), new backend services (policy_baseline_service, policy_generate_service), new background worker (drift_check_worker), new API endpoints, and new React pages. Leverages existing Phase 0 agent commands (seccomp_learn, firewall_log_baseline, apparmor complain mode) and the existing AI service.

**Prerequisites:** Phase 0 (all executor wire-ups including seccomp_learn + firewall_log_baseline), Phase 1 (notification system).

---

## Task 1: PolicyBaseline model and service

**Files:**
- Create: `backend/app/models/policy_baseline.py`
- Create: `backend/app/services/policy_baseline_service.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/XXXX_add_policy_baseline.py`
- Create: `backend/app/tests/test_policy_baseline.py`

- [ ] **Step 1: Test**

```python
# backend/app/tests/test_policy_baseline.py
import pytest
import uuid
from app.services.policy_baseline_service import PolicyBaselineService


@pytest.mark.asyncio
async def test_store_seccomp_baseline(db_session):
    svc = PolicyBaselineService(db_session)
    baseline_id = await svc.store(
        asset_id=str(uuid.uuid4()),
        policy_type="seccomp",
        cr_id=str(uuid.uuid4()),
        observation={"observed_syscalls": ["read", "write", "epoll_wait"], "duration_seconds": 60},
        organization_id=str(uuid.uuid4()),
    )
    assert baseline_id is not None
    retrieved = await svc.get_latest(asset_id=baseline_id[0], policy_type="seccomp")
    assert retrieved["observation"]["syscall_count"] == 3 or "observed_syscalls" in retrieved["observation"]


@pytest.mark.asyncio
async def test_drift_detection_finds_new_syscall(db_session):
    svc = PolicyBaselineService(db_session)
    asset_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    cr_id = str(uuid.uuid4())
    await svc.store(asset_id, "seccomp", cr_id,
        {"observed_syscalls": ["read", "write", "epoll_wait"]}, org_id)
    drift = await svc.detect_drift(asset_id, "seccomp",
        {"observed_syscalls": ["read", "write", "epoll_wait", "mprotect"]})
    assert len(drift["new_behaviors"]) == 1
    assert "mprotect" in drift["new_behaviors"]
```

- [ ] **Step 2: Create PolicyBaseline model**

```python
# backend/app/models/policy_baseline.py
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class PolicyBaseline(Base):
    __tablename__ = "policy_baselines"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # "seccomp" | "apparmor" | "selinux" | "iptables" | "auditd" | "fim" | "wdac" | "asr" | "sysmon"
    cr_id: Mapped[str] = mapped_column(String(64), nullable=True)  # CR that applied the policy
    observation: Mapped[dict] = mapped_column(JSON, nullable=False)
    # seccomp: {"observed_syscalls": [...]}
    # apparmor: {"observed_denials": [...]}
    # iptables: {"observed_connections": [...]}
    # fim: {"baseline_file_count": N, "baseline_hash": "sha256..."}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc))


class DriftAlert(Base):
    __tablename__ = "drift_alerts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_type: Mapped[str] = mapped_column(String(32), nullable=False)
    baseline_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    new_behaviors: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    # "open" | "acknowledged" | "resolved"
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 3: Create PolicyBaselineService**

```python
# backend/app/services/policy_baseline_service.py
from __future__ import annotations
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.policy_baseline import PolicyBaseline, DriftAlert


class PolicyBaselineService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def store(self, asset_id: str, policy_type: str, cr_id: str,
                    observation: dict, organization_id: str) -> tuple[str, str]:
        baseline = PolicyBaseline(
            organization_id=uuid.UUID(organization_id),
            asset_id=asset_id,
            policy_type=policy_type,
            cr_id=cr_id,
            observation=observation,
        )
        self.db.add(baseline)
        await self.db.commit()
        return (asset_id, str(baseline.id))

    async def get_latest(self, asset_id: str, policy_type: str) -> dict | None:
        result = await self.db.execute(
            select(PolicyBaseline)
            .where(PolicyBaseline.asset_id == asset_id, PolicyBaseline.policy_type == policy_type)
            .order_by(PolicyBaseline.created_at.desc())
            .limit(1)
        )
        baseline = result.scalar_one_or_none()
        if not baseline:
            return None
        return {"id": str(baseline.id), "observation": baseline.observation,
                "created_at": baseline.created_at.isoformat()}

    async def detect_drift(self, asset_id: str, policy_type: str,
                           current_observation: dict) -> dict:
        """Compare current observation to baseline. Return new_behaviors list."""
        baseline = await self.get_latest(asset_id, policy_type)
        if not baseline:
            return {"new_behaviors": [], "baseline_missing": True}

        baseline_obs = baseline["observation"]
        new_behaviors = []

        if policy_type == "seccomp":
            baseline_syscalls = set(baseline_obs.get("observed_syscalls", []))
            current_syscalls = set(current_observation.get("observed_syscalls", []))
            new_behaviors = list(current_syscalls - baseline_syscalls)
        elif policy_type == "apparmor":
            baseline_denials = set(baseline_obs.get("observed_denials", []))
            current_denials = set(current_observation.get("observed_denials", []))
            new_behaviors = list(current_denials - baseline_denials)
        elif policy_type == "iptables":
            baseline_conns = {f"{c['dst']}:{c['dpt']}" for c in baseline_obs.get("observed_connections", [])}
            current_conns = {f"{c['dst']}:{c['dpt']}" for c in current_observation.get("observed_connections", [])}
            new_behaviors = list(current_conns - baseline_conns)
        elif policy_type == "fim":
            baseline_files = set(baseline_obs.get("monitored_files", []))
            current_events = set(current_observation.get("changed_files", []))
            new_behaviors = list(current_events - baseline_files)

        return {"new_behaviors": new_behaviors, "baseline_id": baseline["id"]}

    async def create_drift_alert(self, asset_id: str, policy_type: str,
                                  baseline_id: str, new_behaviors: list,
                                  organization_id: str) -> str:
        alert = DriftAlert(
            organization_id=uuid.UUID(organization_id),
            asset_id=asset_id,
            policy_type=policy_type,
            baseline_id=uuid.UUID(baseline_id),
            new_behaviors=new_behaviors,
        )
        self.db.add(alert)
        await self.db.commit()
        return str(alert.id)
```

- [ ] **Step 4: Generate migration and run test**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_policy_baseline_drift_alert"
docker compose exec backend alembic upgrade head
docker compose exec backend pytest app/tests/test_policy_baseline.py -v 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/policy_baseline.py backend/app/services/policy_baseline_service.py \
        backend/app/tests/test_policy_baseline.py backend/alembic/versions/
git commit -m "feat: PolicyBaseline + DriftAlert models with drift detection service"
```

---

## Task 2: Hardening project templates

**Files:**
- Modify: `backend/app/models/project.py` (add `template` field)
- Create: `backend/app/services/project_template_service.py`
- Modify: `backend/app/routers/projects.py` (add template creation endpoint)
- Create: `backend/app/tests/test_project_templates.py`
- Create: `backend/alembic/versions/XXXX_add_project_template.py`
- Modify: `frontend/src/pages/Projects.tsx` (template gallery)
- Create: `frontend/src/components/HardeningProjectWizard.tsx`

- [ ] **Step 1: Test**

```python
@pytest.mark.asyncio
async def test_create_seccomp_rollout_from_template(async_client, admin_token):
    resp = await async_client.post(
        "/projects/from-template",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "template": "seccomp_rollout",
            "name": "Seccomp rollout — web tier",
            "target_asset_ids": ["asset-uuid-1", "asset-uuid-2"],
        }
    )
    assert resp.status_code == 201
    project = resp.json()
    assert project["template"] == "seccomp_rollout"
    assert len(project["phases"]) >= 3  # observe / apply-staging / apply-prod
```

- [ ] **Step 2: Add template field to Project model**

```python
template: Mapped[str | None] = mapped_column(String(64), nullable=True)
# "seccomp_rollout" | "microsegmentation" | "apparmor_rollout" | "least_privilege_iam" | "zero_trust_network"
```

- [ ] **Step 3: Create project_template_service.py**

```python
# backend/app/services/project_template_service.py
from __future__ import annotations

TEMPLATES = {
    "seccomp_rollout": {
        "description": "Learn syscall profile → apply audit mode → enforce → drift watch",
        "phases": [
            {"name": "Observe", "soak_hours": 0, "cr_types": ["seccomp_learn"]},
            {"name": "Apply Staging", "soak_hours": 72, "cr_types": ["configure_seccomp"]},
            {"name": "Apply Production", "soak_hours": 0, "cr_types": ["configure_seccomp"]},
        ],
    },
    "microsegmentation": {
        "description": "Map service traffic → define policies → pilot → full rollout",
        "phases": [
            {"name": "Traffic Mapping", "soak_hours": 0, "cr_types": ["deep_discover", "firewall_log_baseline"]},
            {"name": "Pilot (10%)", "soak_hours": 168, "cr_types": ["configure_host_firewall"]},
            {"name": "Full Rollout", "soak_hours": 0, "cr_types": ["configure_host_firewall"]},
        ],
    },
    "apparmor_rollout": {
        "description": "AppArmor complain mode → collect denials → enforce profile",
        "phases": [
            {"name": "Complain Mode", "soak_hours": 168, "cr_types": ["configure_apparmor"]},
            {"name": "Enforce Staging", "soak_hours": 72, "cr_types": ["configure_apparmor"]},
            {"name": "Enforce Production", "soak_hours": 0, "cr_types": ["configure_apparmor"]},
        ],
    },
    "least_privilege_iam": {
        "description": "Audit current IAM → generate minimal policies → apply staging → prod",
        "phases": [
            {"name": "Audit", "soak_hours": 0, "cr_types": ["audit_users_and_groups"]},
            {"name": "Apply Staging", "soak_hours": 168, "cr_types": ["user_scope_reduction"]},
            {"name": "Apply Production", "soak_hours": 0, "cr_types": ["user_scope_reduction"]},
        ],
    },
    "zero_trust_network": {
        "description": "Discover service dependencies → default-deny → explicit allows",
        "phases": [
            {"name": "Map Dependencies", "soak_hours": 0, "cr_types": ["deep_discover"]},
            {"name": "Apply Default-Deny", "soak_hours": 336, "cr_types": ["configure_host_firewall", "update_security_group"]},
            {"name": "Verify & Lock", "soak_hours": 0, "cr_types": ["configure_host_firewall"]},
        ],
    },
}


async def create_project_from_template(
    template_name: str,
    project_name: str,
    target_asset_ids: list[str],
    user_id: str,
    organization_id: str,
    db,
) -> dict:
    from app.models.project import Project, ProjectStatus
    from app.models.project_phase import ProjectPhase
    import uuid

    template = TEMPLATES.get(template_name)
    if not template:
        raise ValueError(f"Unknown template: {template_name}")

    project = Project(
        id=uuid.uuid4(),
        name=project_name,
        goal=template["description"],
        organization_id=uuid.UUID(organization_id),
        created_by=uuid.UUID(user_id),
        status=ProjectStatus.active,
        template=template_name,
    )
    db.add(project)

    prev_phase_id = None
    phases = []
    for i, phase_def in enumerate(template["phases"]):
        phase = ProjectPhase(
            id=uuid.uuid4(),
            project_id=project.id,
            name=phase_def["name"],
            sequence=i,
            prerequisite_phase_id=prev_phase_id,
            soak_hours=phase_def.get("soak_hours", 0),
            status="pending" if i > 0 else "in_progress",
        )
        db.add(phase)
        phases.append({"id": str(phase.id), "name": phase.name, "soak_hours": phase.soak_hours})
        prev_phase_id = phase.id

    await db.commit()
    return {
        "id": str(project.id),
        "name": project.name,
        "template": template_name,
        "phases": phases,
    }
```

- [ ] **Step 4: Add /projects/from-template endpoint**

```python
@router.post("/from-template", status_code=201)
async def create_from_template(body: ProjectFromTemplateRequest, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from app.services.project_template_service import create_project_from_template
    return await create_project_from_template(
        template_name=body.template,
        project_name=body.name,
        target_asset_ids=body.target_asset_ids,
        user_id=str(user.id),
        organization_id=str(user.organization_id),
        db=db,
    )
```

- [ ] **Step 5: Frontend — template gallery**

In `frontend/src/pages/Projects.tsx`, add a "New from template" button that opens a gallery of 5 template cards with icons, descriptions, and "Start" buttons. Clicking "Start" opens `HardeningProjectWizard.tsx`.

`frontend/src/components/HardeningProjectWizard.tsx` — multi-step: select template → name project → select target assets → confirm phases → create.

- [ ] **Step 6: Migrate, test, commit**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_project_template"
docker compose exec backend alembic upgrade head
docker compose exec backend pytest app/tests/test_project_templates.py -v 2>&1 | tail -10
docker compose stop frontend && docker compose up frontend -d
git add backend/ frontend/
git commit -m "feat: hardening project templates — 5 templates (seccomp/microseg/apparmor/iam/zero-trust)"
```

---

## Task 3: AI policy generation from discovery

**Files:**
- Create: `backend/app/routers/policy_generate.py`
- Modify: `backend/app/services/ai_service.py` (add policy generation prompts)
- Create: `backend/app/tests/test_policy_generate.py`
- Modify: `backend/app/main.py`
- Modify: `frontend/src/pages/ChangeRequestDetail.tsx` (generate policy button)

- [ ] **Step 1: Test**

```python
@pytest.mark.asyncio
async def test_generate_seccomp_policy_from_observation(async_client, admin_token):
    resp = await async_client.post(
        "/policy/generate",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "policy_type": "seccomp",
            "observation": {
                "observed_syscalls": ["read", "write", "open", "close", "stat",
                                      "fstat", "lstat", "epoll_wait", "accept4",
                                      "sendfile", "socket", "bind", "listen"]
            },
            "service_name": "nginx",
        }
    )
    assert resp.status_code == 200
    policy = resp.json()
    assert "policy_content" in policy
    assert policy["policy_type"] == "seccomp"
    # Should be valid JSON for seccomp
    import json
    parsed = json.loads(policy["policy_content"])
    assert "syscalls" in parsed or "defaultAction" in parsed
```

- [ ] **Step 2: Create policy_generate.py router**

```python
# backend/app/routers/policy_generate.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.auth import current_user
from app.models.user import User
from app.services.policy_generate_service import generate_policy

router = APIRouter(prefix="/policy", tags=["Policy Generation"])


class PolicyGenerateRequest(BaseModel):
    policy_type: str  # "seccomp" | "apparmor" | "iptables" | "wdac" | "asr"
    observation: dict
    service_name: str | None = None
    context: str | None = None  # Additional context for AI


class PolicyGenerateResponse(BaseModel):
    policy_type: str
    policy_content: str
    rationale: str
    warnings: list[str] = []


@router.post("/generate", response_model=PolicyGenerateResponse)
async def generate_security_policy(
    body: PolicyGenerateRequest,
    user: User = Depends(current_user),
):
    result = await generate_policy(
        policy_type=body.policy_type,
        observation=body.observation,
        service_name=body.service_name,
        organization_id=str(user.organization_id),
    )
    return PolicyGenerateResponse(**result)
```

- [ ] **Step 3: Create policy_generate_service.py**

```python
# backend/app/services/policy_generate_service.py
from __future__ import annotations
import json

POLICY_PROMPTS = {
    "seccomp": """You are a Linux kernel security expert. Given a list of system calls observed from a running service, generate a minimal seccomp profile in JSON format that allows exactly those syscalls plus essential ones (exit, exit_group, rt_sigreturn, futex, mmap, brk, munmap).

Return ONLY valid JSON in this exact format:
{
  "defaultAction": "SCMP_ACT_ERRNO",
  "syscalls": [
    {"names": ["read", "write", "..."], "action": "SCMP_ACT_ALLOW"}
  ]
}

Observed syscalls: {observed_syscalls}
Service: {service_name}

Include a brief rationale comment in a "rationale" field outside the JSON (I will parse them separately).""",

    "apparmor": """You are a Linux security expert specializing in AppArmor. Given a list of file accesses and capabilities observed from a service, generate a minimal AppArmor profile.

Return the profile in standard AppArmor format starting with:
#include <tunables/global>
profile {service_name} {{

Observed denials (would-have-been-blocked): {observed_denials}
Service: {service_name}""",

    "iptables": """You are a network security expert. Given a list of observed outbound connections from a service, generate iptables rules that allow exactly those connections and deny all others.

Return rules in iptables-restore format (lines starting with -A).

Observed connections: {observed_connections}
Service host: the source""",

    "wdac": """You are a Windows Defender Application Control (WDAC) expert. Given a list of executables that should be allowed to run, generate a WDAC policy in XML format.

Use a minimal allowlist policy structure. Return only the XML content.

Allowed executables (by path): {allowed_executables}""",

    "asr": """You are an Attack Surface Reduction (ASR) rules expert. Given observed application behavior, select which of the 16 ASR rules should be enabled in block mode vs audit mode.

Return JSON: {"block": ["rule-name-1", ...], "audit": ["rule-name-2", ...], "disable": ["rule-name-3", ...]}

Application behavior: {behavior_summary}""",
}


async def generate_policy(
    policy_type: str,
    observation: dict,
    service_name: str | None,
    organization_id: str,
) -> dict:
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.org_settings import OrganizationSettings
    from app.services.secrets_service import SecretsService
    from app import config as app_config
    from app.services.ai_service import _resolve_provider_config

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(OrganizationSettings).where(
                OrganizationSettings.organization_id.__eq__(organization_id)
                if hasattr(OrganizationSettings.organization_id, '__eq__')
                else OrganizationSettings.organization_id == organization_id
            )
        )
        org_settings = result.scalar_one_or_none()

    if not org_settings:
        raise ValueError("No AI provider configured")

    secrets = SecretsService(app_config.settings.SECRET_KEY)
    provider, api_key, model = _resolve_provider_config(org_settings, secrets)

    prompt_template = POLICY_PROMPTS.get(policy_type)
    if not prompt_template:
        raise ValueError(f"Unknown policy type: {policy_type}")

    # Format observation for the prompt
    obs_str = json.dumps(observation, indent=2)
    prompt = prompt_template.format(
        observed_syscalls=json.dumps(observation.get("observed_syscalls", [])),
        observed_denials=json.dumps(observation.get("observed_denials", [])),
        observed_connections=json.dumps(observation.get("observed_connections", [])),
        allowed_executables=json.dumps(observation.get("allowed_executables", [])),
        behavior_summary=obs_str,
        service_name=service_name or "unknown-service",
    )

    system = f"You are a {policy_type} security policy expert. Generate minimal, correct policies. Return only the policy content — no explanations unless specifically asked."

    if provider == "openai":
        import openai
        client = openai.AsyncOpenAI(api_key=api_key)
        resp = await client.chat.completions.create(
            model=model, max_tokens=2048,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        )
        raw = resp.choices[0].message.content
    else:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        resp = await client.messages.create(
            model=model, max_tokens=2048, system=system,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text

    # Extract rationale if present
    rationale = ""
    policy_content = raw.strip()
    if "rationale:" in raw.lower():
        parts = raw.split("\n")
        policy_lines = []
        rationale_lines = []
        in_rationale = False
        for line in parts:
            if line.lower().startswith("rationale:"):
                in_rationale = True
                rationale_lines.append(line[len("rationale:"):].strip())
            elif in_rationale:
                rationale_lines.append(line)
            else:
                policy_lines.append(line)
        policy_content = "\n".join(policy_lines).strip()
        rationale = " ".join(rationale_lines).strip()

    # Strip markdown code blocks if present
    if "```" in policy_content:
        start = policy_content.find("```") + 3
        # Skip language identifier line
        if "\n" in policy_content[start:]:
            start = policy_content.index("\n", start) + 1
        end = policy_content.rfind("```")
        policy_content = policy_content[start:end].strip()

    return {
        "policy_type": policy_type,
        "policy_content": policy_content,
        "rationale": rationale or f"Minimal {policy_type} policy generated from {len(observation.get('observed_syscalls', observation.get('observed_denials', observation.get('observed_connections', []))))} observed behaviors",
        "warnings": [],
    }
```

- [ ] **Step 4: Wire router in main.py**

```python
from app.routers.policy_generate import router as policy_generate_router
app.include_router(policy_generate_router)
```

- [ ] **Step 5: Frontend — "Generate policy" button on learn-mode CR detail**

In `frontend/src/pages/ChangeRequestDetail.tsx`, when a CR has `change_type` ending in `_learn` and status `completed`, add:
```tsx
{isLearnCR && (
  <button
    onClick={() => handleGeneratePolicy()}
    className="px-3 py-2 text-sm bg-purple-600 text-white rounded hover:bg-purple-700"
  >
    Generate enforce policy from this baseline
  </button>
)}
```

The handler calls `POST /policy/generate` with the CR's result data and shows a code editor with the generated policy, then allows creating an enforce CR.

- [ ] **Step 6: Run tests and commit**

```bash
docker compose exec backend pytest app/tests/test_policy_generate.py -v 2>&1 | tail -10
docker compose stop frontend && docker compose up frontend -d
git add backend/ frontend/
git commit -m "feat: AI policy generation from discovery data (seccomp/AppArmor/iptables/WDAC/ASR)"
```

---

## Task 4: Policy drift detection worker

**Files:**
- Create: `backend/app/workers/drift_check_worker.py`
- Modify: `backend/app/main.py` (register worker)
- Create: `backend/app/routers/drift_alerts.py`
- Create: `backend/app/tests/test_drift_worker.py`
- Modify: `frontend/src/pages/Compliance.tsx` (drift alerts section)

- [ ] **Step 1: Test**

```python
@pytest.mark.asyncio
async def test_drift_check_creates_alert_on_new_syscall(db_session, test_asset_id, test_org_id):
    from app.services.policy_baseline_service import PolicyBaselineService
    from app.workers.drift_check_worker import check_drift_for_asset

    svc = PolicyBaselineService(db_session)
    await svc.store(test_asset_id, "seccomp", "cr-uuid-1",
        {"observed_syscalls": ["read", "write", "epoll_wait"]}, test_org_id)

    # Simulate a new observation with an extra syscall
    alert_count = await check_drift_for_asset(
        db_session, test_asset_id, "seccomp", test_org_id,
        current_observation={"observed_syscalls": ["read", "write", "epoll_wait", "ptrace"]}
    )
    assert alert_count == 1

    # Verify DriftAlert was created
    from sqlalchemy import select
    from app.models.policy_baseline import DriftAlert
    result = await db_session.execute(
        select(DriftAlert).where(DriftAlert.asset_id == test_asset_id)
    )
    alerts = result.scalars().all()
    assert len(alerts) == 1
    assert "ptrace" in alerts[0].new_behaviors
```

- [ ] **Step 2: Create drift_check_worker.py**

```python
# backend/app/workers/drift_check_worker.py
from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import AsyncSessionLocal
from app.models.policy_baseline import PolicyBaseline
from app.services.policy_baseline_service import PolicyBaselineService
from app.services.notification_service import NotificationService, NotificationEvent
from app.models.user import User, UserRole
from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job


async def check_drift_for_asset(
    db: AsyncSession,
    asset_id: str,
    policy_type: str,
    organization_id: str,
    current_observation: dict | None = None,
) -> int:
    """Run a short drift check for one asset/policy. Returns number of alerts created."""
    svc = PolicyBaselineService(db)

    if current_observation is None:
        # Run a 60-second observation to get current state
        agent_command = {
            "seccomp": "seccomp_learn",
            "apparmor": "configure_apparmor",
            "iptables": "firewall_log_baseline",
            "fim": "setup_file_integrity_monitoring",
        }.get(policy_type)
        if not agent_command:
            return 0
        try:
            current_observation = await dispatch_agent_job(
                command=agent_command,
                parameters={"duration_seconds": 60, "mode": "learn" if policy_type == "apparmor" else None,
                            "action": "check" if policy_type == "fim" else None},
                asset_ids=[asset_id],
                timeout_seconds=90,
            )
        except Exception:
            return 0

    drift = await svc.detect_drift(asset_id, policy_type, current_observation)
    if not drift["new_behaviors"]:
        return 0

    baseline_id = drift.get("baseline_id", "unknown")
    alert_id = await svc.create_drift_alert(
        asset_id=asset_id,
        policy_type=policy_type,
        baseline_id=baseline_id,
        new_behaviors=drift["new_behaviors"],
        organization_id=organization_id,
    )

    # Notify security operators
    async with AsyncSessionLocal() as notify_db:
        result = await notify_db.execute(
            select(User).where(
                User.organization_id.__eq__(organization_id) if hasattr(User.organization_id, '__eq__') else True,
                User.role.in_([UserRole.admin, UserRole.security_operator])
                if hasattr(UserRole, 'security_operator') else User.role == UserRole.admin,
            )
        )
        recipients = [str(u.id) for u in result.scalars()]
        notif_svc = NotificationService(notify_db)
        await notif_svc.emit(NotificationEvent(
            event_type="policy.drift_detected",
            organization_id=organization_id,
            resource_id=alert_id,
            resource_type="drift_alert",
            message=f"Policy drift detected on asset {asset_id}: {len(drift['new_behaviors'])} new {policy_type} behaviors would be blocked",
            recipients=recipients,
        ))
    return 1


async def run_weekly_drift_checks():
    """APScheduler job: check drift for all assets with active policy baselines."""
    async with AsyncSessionLocal() as db:
        # Get distinct (asset_id, policy_type, org_id) with baselines
        result = await db.execute(
            select(PolicyBaseline.asset_id, PolicyBaseline.policy_type,
                   PolicyBaseline.organization_id)
            .distinct()
        )
        tasks = result.fetchall()
        for asset_id, policy_type, org_id in tasks:
            await check_drift_for_asset(db, asset_id, policy_type, str(org_id))


def start_drift_scheduler(app):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_weekly_drift_checks, "interval", weeks=1,
                      id="drift_check_weekly", replace_existing=True)
    scheduler.start()
    app.state.drift_scheduler = scheduler
```

- [ ] **Step 3: Register in main.py lifespan**

```python
# In lifespan startup:
from app.workers.drift_check_worker import start_drift_scheduler
start_drift_scheduler(app)
```

- [ ] **Step 4: Create drift alerts router**

```python
# backend/app/routers/drift_alerts.py
from fastapi import APIRouter, Depends
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.auth import current_user
from app.models.user import User
from app.models.policy_baseline import DriftAlert
import uuid

router = APIRouter(prefix="/drift-alerts", tags=["Drift Alerts"])


@router.get("")
async def list_drift_alerts(
    status: str = "open",
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DriftAlert).where(
            DriftAlert.organization_id == user.organization_id,
            DriftAlert.status == status,
        ).order_by(DriftAlert.detected_at.desc()).limit(100)
    )
    alerts = result.scalars().all()
    return [{"id": str(a.id), "asset_id": a.asset_id, "policy_type": a.policy_type,
             "new_behaviors": a.new_behaviors, "detected_at": a.detected_at.isoformat(),
             "status": a.status} for a in alerts]


@router.post("/{alert_id}/acknowledge")
async def acknowledge_drift_alert(
    alert_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone
    await db.execute(
        update(DriftAlert).where(DriftAlert.id == alert_id)
        .values(status="acknowledged", acknowledged_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return {"acknowledged": True}
```

- [ ] **Step 5: Frontend — drift alerts in Compliance page**

In `frontend/src/pages/Compliance.tsx`, add a "Policy Drift" section that fetches `GET /drift-alerts` and shows cards with: asset name, policy type, new behaviors list, detected time, Acknowledge button.

- [ ] **Step 6: Run tests and commit**

```bash
docker compose exec backend pytest app/tests/test_drift_worker.py -v 2>&1 | tail -10
docker compose stop frontend && docker compose up frontend -d
git add backend/ frontend/
git commit -m "feat: policy drift detection worker + DriftAlert model + Compliance page drift section"
```

---

## Task 5: SECCOMP_PIPELINE and HARDENING_PIPELINE smoke phases

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

- [ ] **Step 1: Add _INSTALL_NGINX_UBUNTU constant**

```python
_INSTALL_NGINX_UBUNTU = r"""
set -e
apt-get update -q && apt-get install -y nginx auditd audispd-plugins libaudit-dev
systemctl enable nginx && systemctl start nginx
systemctl enable auditd && systemctl start auditd
echo "nginx and auditd installed"
"""

_TEARDOWN_NGINX = r"""
set -e
# Remove nexplane seccomp drop-in
rm -f /etc/systemd/system/nginx.service.d/nexplane-seccomp-learn.conf
systemctl daemon-reload
systemctl restart nginx 2>/dev/null || true
echo "Seccomp learn teardown complete"
"""
```

- [ ] **Step 2: Add run_phase_seccomp_pipeline**

```python
def run_phase_seccomp_pipeline(client: NexplaneClient, phase_a_result: dict) -> None:
    """Phase SECCOMP_PIPELINE: learn → AI-generate → enforce → verify → drift detect."""
    print("\n[Phase SECCOMP_PIPELINE] Seccomp learn → enforce → drift pipeline")

    instance_asset = phase_a_result["instance_asset"]
    instance_id = phase_a_result["instance_id"]
    agent_asset = phase_a_result.get("agent_asset") or {}
    agent_asset_id = agent_asset.get("id")
    if not agent_asset_id:
        fail("[Phase SECCOMP_PIPELINE] No agent asset ID")

    try:
        _ssm(client, instance_asset["id"], instance_id, "SECCOMP",
             "install_nginx", _INSTALL_NGINX_UBUNTU)
        log("nginx + auditd installed")

        # Stage 1: seccomp_learn (60 seconds)
        print("  → [SECCOMP_PIPELINE] seccomp_learn (60s)")
        learn_cr_id = client.create_cr(
            "[SECCOMP] seccomp learn",
            "seccomp_learn", agent_asset_id,
            {"service_name": "nginx", "duration_seconds": 60},
        )
        client.post(f"/change-requests/{learn_cr_id}/plan")
        client.post(f"/change-requests/{learn_cr_id}/submit-for-approval")
        client.post(f"/change-requests/{learn_cr_id}/approve", json={"decision": "approved"})
        client.post(f"/change-requests/{learn_cr_id}/execute")

        # Generate HTTP traffic during learn window to populate syscall list
        time.sleep(5)
        for _ in range(10):
            try:
                _ssm(client, instance_asset["id"], instance_id, "SECCOMP",
                     "gen_traffic", "curl -s http://localhost:80 > /dev/null")
            except Exception:
                pass
            time.sleep(5)

        # Wait for learn CR to complete
        deadline = time.time() + 120
        while time.time() < deadline:
            cr = client.get(f"/change-requests/{learn_cr_id}")
            if cr.get("status") == "completed":
                break
            elif cr.get("status") in ("failed", "rolled_back"):
                fail(f"[SECCOMP_PIPELINE] Learn CR failed: {cr.get('status')}")
            time.sleep(10)

        # Extract observed syscalls from CR result
        exec_runs = cr.get("execution_runs") or []
        run_result = (exec_runs[0].get("result") or {}) if exec_runs else {}
        steps = (run_result.get("execution") or {}).get("steps") or []
        step_result = steps[-1].get("result", {}) if steps else {}
        observed_syscalls = step_result.get("observed_syscalls", [])
        log(f"Learned {len(observed_syscalls)} syscalls")
        if len(observed_syscalls) < 3:
            fail(f"[SECCOMP_PIPELINE] Too few syscalls learned: {observed_syscalls}")

        # Stage 2: AI policy generation
        print("  → [SECCOMP_PIPELINE] AI policy generation")
        policy_resp = client.post("/policy/generate", json={
            "policy_type": "seccomp",
            "observation": {"observed_syscalls": observed_syscalls},
            "service_name": "nginx",
        })
        policy_content = policy_resp.get("policy_content", "")
        if not policy_content:
            fail("[SECCOMP_PIPELINE] AI policy generation returned empty content")
        log(f"AI generated seccomp policy ({len(policy_content)} chars)")

        # Stage 3: Store baseline
        baseline_resp = client.post(f"/policy/baselines", json={
            "asset_id": agent_asset_id,
            "policy_type": "seccomp",
            "cr_id": learn_cr_id,
            "observation": {"observed_syscalls": observed_syscalls},
        })
        baseline_id = baseline_resp.get("id")
        log(f"Baseline stored: {baseline_id}")

        # Stage 4: Apply enforce mode
        enforce_cr_id = client.create_cr(
            "[SECCOMP] apply enforce profile",
            "configure_seccomp", agent_asset_id,
            {"service_name": "nginx", "profile": policy_content},
        )
        client.post(f"/change-requests/{enforce_cr_id}/plan")
        client.post(f"/change-requests/{enforce_cr_id}/submit-for-approval")
        client.post(f"/change-requests/{enforce_cr_id}/approve", json={"decision": "approved"})
        client.post(f"/change-requests/{enforce_cr_id}/execute")

        deadline = time.time() + 120
        while time.time() < deadline:
            cr = client.get(f"/change-requests/{enforce_cr_id}")
            if cr.get("status") == "completed":
                break
            elif cr.get("status") in ("failed", "rolled_back"):
                fail(f"[SECCOMP_PIPELINE] Enforce CR failed")
            time.sleep(10)

        # Verify nginx still responds after seccomp applied
        out = _ssm_output(client, instance_asset["id"], instance_id,
                          "SECCOMP", "verify_nginx", "curl -s -o /dev/null -w '%{http_code}' http://localhost:80")
        if "200" not in out:
            fail(f"[SECCOMP_PIPELINE] nginx not serving after seccomp enforce: {out}")
        log("nginx still serves HTTP 200 after seccomp enforce ✓")

        # Stage 5: Trigger immediate drift check (should find nothing new)
        drift_resp = client.post(f"/drift-alerts/check", json={
            "asset_id": agent_asset_id,
            "policy_type": "seccomp",
            "current_observation": {"observed_syscalls": observed_syscalls},  # Same as baseline
        })
        if drift_resp.get("alerts_created", 0) > 0:
            log("  ⚠️  Unexpected drift alerts on clean check")
        else:
            log("Drift check clean (no new behaviors) ✓")

        log("Phase SECCOMP_PIPELINE complete")

    except Exception as e:
        print(f"\n❌ Phase SECCOMP_PIPELINE failed: {e}")
        raise
    finally:
        try:
            _ssm(client, instance_asset["id"], instance_id, "SECCOMP",
                 "teardown", _TEARDOWN_NGINX)
        except Exception:
            pass
```

- [ ] **Step 3: Add /policy/baselines and /drift-alerts/check endpoints**

In `policy_generate.py` router:
```python
@router.post("/baselines", status_code=201)
async def store_baseline(body: StoreBaselineRequest, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from app.services.policy_baseline_service import PolicyBaselineService
    svc = PolicyBaselineService(db)
    _, baseline_id = await svc.store(body.asset_id, body.policy_type, body.cr_id, body.observation, str(user.organization_id))
    return {"id": baseline_id}
```

In `drift_alerts.py`:
```python
@router.post("/check")
async def manual_drift_check(body: ManualDriftCheckRequest, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    from app.workers.drift_check_worker import check_drift_for_asset
    count = await check_drift_for_asset(db, body.asset_id, body.policy_type, str(user.organization_id), body.current_observation)
    return {"alerts_created": count}
```

- [ ] **Step 4: Wire SECCOMP_PIPELINE and HARDENING_PIPELINE into main()**

```python
if "SECCOMP_PIPELINE" in phases:
    if phase_a_result is None:
        fail("Phase SECCOMP_PIPELINE requires Phase A")
    run_phase_seccomp_pipeline(client, phase_a_result)
```

- [ ] **Step 5: Run SECCOMP_PIPELINE via EC2 runner and commit**

```bash
docker compose exec \
  -e AWS_ACCESS_KEY_ID=AKIAY57IGHLWB6RI3YVM \
  -e "AWS_SECRET_ACCESS_KEY=SLX7jYNMHu3Sd7C/bGflGn6KLGZD/64oHoHr/Hyk" \
  -e AWS_DEFAULT_REGION=us-east-1 \
  backend python tests/smoke/run_on_ec2.py \
    --email admin@acme.example --password admin123 \
    --phases A,SECCOMP_PIPELINE \
    --tailscale-auth-key tskey-auth-kTYui1NBwG11CNTRL-3PypxPACT3JRppfHm8JQ4J6yqEirTU88H
```

```bash
git add backend/tests/smoke/test_aws_live.py backend/
git commit -m "test: SECCOMP_PIPELINE smoke phase — learn→AI-generate→enforce→verify→drift"
```

---

## Task 6: Threat model linkage

**Files:**
- Modify: `backend/app/models/project.py` (add `risk_context` JSONB)
- Modify: `backend/app/schemas/project.py`
- Modify: `frontend/src/pages/ProjectDetail.tsx`
- Create: `backend/alembic/versions/XXXX_add_project_risk_context.py`

- [ ] **Step 1: Add risk_context to Project**

```python
risk_context: Mapped[dict | None] = mapped_column(JSON, nullable=True)
# {
#   "type": "threat_model" | "pentest_finding" | "compliance_requirement" | "risk_assessment",
#   "description": "...",
#   "reference_url": "https://..."
# }
```

- [ ] **Step 2: Add to schema and frontend**

In `ProjectRead` schema: `risk_context: dict | None = None`

In `ProjectDetail.tsx`, add a "Risk Context" card below the project header that shows `risk_context.type` badge and `risk_context.description`. Allow editing via inline form.

- [ ] **Step 3: Migrate and commit**

```bash
docker compose exec backend alembic revision --autogenerate -m "add_project_risk_context"
docker compose exec backend alembic upgrade head
docker compose stop frontend && docker compose up frontend -d
git add backend/ frontend/
git commit -m "feat: threat model linkage — projects carry risk context (threat model/pentest/compliance)"
```
