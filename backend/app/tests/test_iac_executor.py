# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for iac_executor.py — all I/O mocked."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.iac_executor import (
    run_plan_phase,
    run_apply_phase,
    is_iac_change_type,
    IAC_CHANGE_TYPES,
)
from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus
from app.models.change_plan import ChangePlan


def make_cr(change_type: ChangeType, desired_outcome: dict | None = None) -> ChangeRequest:
    cr = MagicMock(spec=ChangeRequest)
    cr.id = "cr-iac-test-001"
    cr.change_type = change_type
    cr.desired_outcome = desired_outcome or {"working_directory": "/tf/prod"}
    cr.change_plan = None
    cr.status = ChangeRequestStatus.draft
    return cr


@pytest.mark.asyncio
async def test_is_iac_change_type():
    assert is_iac_change_type(ChangeType.terraform_apply)
    assert is_iac_change_type(ChangeType.ansible_playbook)
    assert is_iac_change_type(ChangeType.helm_upgrade)
    assert not is_iac_change_type(ChangeType.dns_update)


@pytest.mark.asyncio
async def test_run_plan_phase_terraform_success():
    cr = make_cr(ChangeType.terraform_apply)
    db = AsyncMock()
    db.flush = AsyncMock()

    plan_output = "Plan: 3 to add, 0 to change, 0 to destroy."
    agent_dispatch = AsyncMock(return_value={
        "status": "completed",
        "data": {"plan_output": plan_output},
    })

    with patch("app.services.iac_executor.ChangePlan") as MockChangePlan:
        mock_plan = MagicMock()
        mock_plan.blast_radius = None
        MockChangePlan.return_value = mock_plan
        result = await run_plan_phase(db, cr, agent_dispatch, agent_id="agent-abc")

    assert result["status"] == "completed"
    assert cr.status == ChangeRequestStatus.planned
    agent_dispatch.assert_called_once()
    call_args = agent_dispatch.call_args
    assert call_args[0][1] == "terraform_plan"
    assert call_args[0][2]["change_id"] == "cr-iac-test-001"


@pytest.mark.asyncio
async def test_run_plan_phase_failure_raises():
    cr = make_cr(ChangeType.terraform_apply)
    db = AsyncMock()
    agent_dispatch = AsyncMock(return_value={
        "status": "failed",
        "error": "No such directory",
    })

    with pytest.raises(RuntimeError, match="IaC plan phase failed"):
        await run_plan_phase(db, cr, agent_dispatch, agent_id="agent-abc")


@pytest.mark.asyncio
async def test_run_apply_phase_ansible_success():
    cr = make_cr(
        ChangeType.ansible_playbook,
        desired_outcome={"playbook_path": "/etc/ansible/site.yml", "inventory": "/etc/ansible/hosts"},
    )
    db = AsyncMock()
    agent_dispatch = AsyncMock(return_value={
        "status": "completed",
        "data": {"run_output": "PLAY RECAP ok=5 changed=3"},
    })

    result = await run_apply_phase(db, cr, agent_dispatch, agent_id="agent-abc")

    assert result["status"] == "completed"
    call_args = agent_dispatch.call_args
    assert call_args[0][1] == "ansible_run"


@pytest.mark.asyncio
async def test_run_apply_phase_dry_run_skips():
    cr = make_cr(ChangeType.terraform_apply, {"working_directory": "/tf/prod", "dry_run": True})
    db = AsyncMock()
    agent_dispatch = AsyncMock()

    result = await run_apply_phase(db, cr, agent_dispatch, agent_id="agent-abc")

    assert result["data"]["skipped"] == "dry_run=true"
    agent_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_run_apply_phase_helm_success():
    cr = make_cr(
        ChangeType.helm_upgrade,
        desired_outcome={"release_name": "myapp", "chart": "stable/myapp", "namespace": "prod"},
    )
    db = AsyncMock()
    agent_dispatch = AsyncMock(return_value={
        "status": "completed",
        "data": {"upgrade_output": "Release deployed successfully."},
    })

    result = await run_apply_phase(db, cr, agent_dispatch, agent_id="agent-abc")

    assert result["status"] == "completed"
    call_args = agent_dispatch.call_args
    assert call_args[0][1] == "helm_upgrade"
