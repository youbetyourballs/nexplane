# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock


def _make_job(agent_reg_id, org_id, status="running"):
    from app.models.agent import AgentJob, AgentJobStatus
    job = MagicMock(spec=AgentJob)
    job.id = uuid.uuid4()
    job.organization_id = org_id
    job.agent_registration_id = agent_reg_id
    job.status = AgentJobStatus(status)
    return job


def _make_org_settings(org_id):
    from app.models.org_settings import OrganizationSettings
    s = MagicMock(spec=OrganizationSettings)
    s.organization_id = org_id
    return s


@pytest.mark.asyncio
async def test_correct_agent_can_submit_result():
    from app.routers.agent import post_job_result
    from app.schemas.agent import AgentJobResultRequest
    from app.models.agent import AgentJobStatus

    org_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    job = _make_job(agent_id, org_id)

    db = AsyncMock()
    db.get = AsyncMock(return_value=job)
    db.commit = AsyncMock()

    body = AgentJobResultRequest(
        agent_id=agent_id,
        status=AgentJobStatus.completed,
        result={"ok": True},
    )
    org_settings = _make_org_settings(org_id)

    result = await post_job_result(job.id, body, org_settings, db)
    assert result == {"ok": True}


@pytest.mark.asyncio
async def test_wrong_agent_cannot_submit_result():
    from app.routers.agent import post_job_result
    from app.schemas.agent import AgentJobResultRequest
    from app.models.agent import AgentJobStatus
    from fastapi import HTTPException

    org_id = uuid.uuid4()
    assigned_agent_id = uuid.uuid4()
    wrong_agent_id = uuid.uuid4()
    job = _make_job(assigned_agent_id, org_id)

    db = AsyncMock()
    db.get = AsyncMock(return_value=job)

    body = AgentJobResultRequest(
        agent_id=wrong_agent_id,
        status=AgentJobStatus.completed,
        result={},
    )
    org_settings = _make_org_settings(org_id)

    with pytest.raises(HTTPException) as exc_info:
        await post_job_result(job.id, body, org_settings, db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_terminal_job_rejected():
    from app.routers.agent import post_job_result
    from app.schemas.agent import AgentJobResultRequest
    from app.models.agent import AgentJobStatus
    from fastapi import HTTPException

    org_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    # Job is already in terminal state
    job = _make_job(agent_id, org_id, status="completed")

    db = AsyncMock()
    db.get = AsyncMock(return_value=job)

    body = AgentJobResultRequest(
        agent_id=agent_id,
        status=AgentJobStatus.completed,
        result={},
    )
    org_settings = _make_org_settings(org_id)

    with pytest.raises(HTTPException) as exc_info:
        await post_job_result(job.id, body, org_settings, db)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_cross_org_result_rejected():
    from app.routers.agent import post_job_result
    from app.schemas.agent import AgentJobResultRequest
    from app.models.agent import AgentJobStatus
    from fastapi import HTTPException

    org_id = uuid.uuid4()
    other_org_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    job = _make_job(agent_id, other_org_id)  # job belongs to OTHER org

    db = AsyncMock()
    db.get = AsyncMock(return_value=job)

    body = AgentJobResultRequest(
        agent_id=agent_id,
        status=AgentJobStatus.completed,
        result={},
    )
    org_settings = _make_org_settings(org_id)  # caller is in org_id

    with pytest.raises(HTTPException) as exc_info:
        await post_job_result(job.id, body, org_settings, db)
    assert exc_info.value.status_code == 404
