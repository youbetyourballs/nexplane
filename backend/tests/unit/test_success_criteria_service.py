# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from app.models.project_success_criteria import CriteriaType, CriteriaResult, ProjectSuccessCriteria
from app.services.success_criteria_service import evaluate_criterion, _apply_operator

_HIS_PATCH = "app.services.success_criteria_service._his.run_intelligence_tool"


def _make_criterion(ctype, assertion):
    c = MagicMock(spec=ProjectSuccessCriteria)
    c.id = uuid.uuid4()
    c.type = ctype
    c.assertion = assertion
    c.description = "test"
    return c


@pytest.mark.asyncio
async def test_cr_completed_missing_cr_id():
    db = AsyncMock()
    c = _make_criterion(CriteriaType.cr_completed, {})
    result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.fail
    assert "cr_id" in detail


@pytest.mark.asyncio
async def test_cr_completed_invalid_uuid():
    db = AsyncMock()
    c = _make_criterion(CriteriaType.cr_completed, {"cr_id": "not-a-uuid"})
    result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.fail
    assert "UUID" in detail


@pytest.mark.asyncio
async def test_cr_completed_not_found():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    c = _make_criterion(CriteriaType.cr_completed, {"cr_id": str(uuid.uuid4())})
    result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.fail
    assert "not found" in detail


@pytest.mark.asyncio
async def test_cr_completed_pass():
    from app.models.change_request import ChangeRequestStatus
    db = AsyncMock()
    mock_cr = MagicMock()
    mock_cr.status = ChangeRequestStatus.completed
    mock_cr.title = "Test CR"
    db.get = AsyncMock(return_value=mock_cr)
    c = _make_criterion(CriteriaType.cr_completed, {"cr_id": str(uuid.uuid4())})
    result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.pass_
    assert "completed" in detail


@pytest.mark.asyncio
async def test_cr_completed_fail_not_done():
    from app.models.change_request import ChangeRequestStatus
    db = AsyncMock()
    mock_cr = MagicMock()
    mock_cr.status = ChangeRequestStatus.draft
    mock_cr.title = "Pending CR"
    db.get = AsyncMock(return_value=mock_cr)
    c = _make_criterion(CriteriaType.cr_completed, {"cr_id": str(uuid.uuid4())})
    result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.fail


@pytest.mark.asyncio
async def test_manual_criterion():
    db = AsyncMock()
    c = _make_criterion(CriteriaType.manual, {"instructions": "Check the logs manually"})
    result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.pending_manual
    assert "Check the logs" in detail


@pytest.mark.asyncio
async def test_manual_criterion_default_instructions():
    db = AsyncMock()
    c = _make_criterion(CriteriaType.manual, {})
    result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.pending_manual
    assert "Manual verification" in detail


@pytest.mark.asyncio
async def test_host_state_check_missing_fields():
    db = AsyncMock()
    c = _make_criterion(CriteriaType.host_state_check, {"asset_id": "a1"})
    result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.fail
    assert "missing" in detail


@pytest.mark.asyncio
async def test_host_state_check_pass():
    db = AsyncMock()
    c = _make_criterion(CriteriaType.host_state_check, {
        "asset_id": "a1", "field": "version", "operator": "eq", "value": "1.2.3"
    })
    mock_intel = {"version": "1.2.3"}
    with patch(_HIS_PATCH, new=AsyncMock(return_value=mock_intel)):
        result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.pass_
    assert "1.2.3" in detail


@pytest.mark.asyncio
async def test_host_state_check_fail():
    db = AsyncMock()
    c = _make_criterion(CriteriaType.host_state_check, {
        "asset_id": "a1", "field": "version", "operator": "eq", "value": "2.0.0"
    })
    mock_intel = {"version": "1.2.3"}
    with patch(_HIS_PATCH, new=AsyncMock(return_value=mock_intel)):
        result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.fail


@pytest.mark.asyncio
async def test_host_state_check_no_intel():
    db = AsyncMock()
    c = _make_criterion(CriteriaType.host_state_check, {
        "asset_id": "a1", "field": "version", "operator": "eq", "value": "1.0"
    })
    with patch(_HIS_PATCH, new=AsyncMock(return_value=None)):
        result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.fail
    assert "no intelligence" in detail


@pytest.mark.asyncio
async def test_service_check_missing_fields():
    db = AsyncMock()
    c = _make_criterion(CriteriaType.service_check, {"asset_id": "a1"})
    result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.fail
    assert "missing" in detail


@pytest.mark.asyncio
async def test_service_check_pass():
    db = AsyncMock()
    c = _make_criterion(CriteriaType.service_check, {
        "asset_id": "a1", "service": "nginx", "state": "running"
    })
    mock_intel = {"running_services": [{"name": "nginx", "state": "running"}]}
    with patch(_HIS_PATCH, new=AsyncMock(return_value=mock_intel)):
        result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.pass_


@pytest.mark.asyncio
async def test_service_check_service_not_found():
    db = AsyncMock()
    c = _make_criterion(CriteriaType.service_check, {
        "asset_id": "a1", "service": "nginx", "state": "running"
    })
    mock_intel = {"running_services": []}
    with patch(_HIS_PATCH, new=AsyncMock(return_value=mock_intel)):
        result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.fail
    assert "not found" in detail


@pytest.mark.asyncio
async def test_port_check_missing_fields():
    db = AsyncMock()
    c = _make_criterion(CriteriaType.port_check, {"asset_id": "a1"})
    result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.fail
    assert "missing" in detail


@pytest.mark.asyncio
async def test_port_check_open_pass():
    db = AsyncMock()
    c = _make_criterion(CriteriaType.port_check, {
        "asset_id": "a1", "port": 443, "open": True
    })
    mock_intel = {"open_ports": [80, 443, 8080]}
    with patch(_HIS_PATCH, new=AsyncMock(return_value=mock_intel)):
        result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.pass_


@pytest.mark.asyncio
async def test_port_check_closed_fail():
    db = AsyncMock()
    c = _make_criterion(CriteriaType.port_check, {
        "asset_id": "a1", "port": 22, "open": False
    })
    mock_intel = {"open_ports": [22, 80]}
    with patch(_HIS_PATCH, new=AsyncMock(return_value=mock_intel)):
        result, detail = await evaluate_criterion(db, c, uuid.uuid4())
    assert result == CriteriaResult.fail
    assert "open" in detail
    assert "closed" in detail


def test_apply_operator_eq():
    assert _apply_operator("running", "eq", "running") is True
    assert _apply_operator("stopped", "eq", "running") is False


def test_apply_operator_neq():
    assert _apply_operator("stopped", "neq", "running") is True
    assert _apply_operator("running", "neq", "running") is False


def test_apply_operator_contains():
    assert _apply_operator("hello world", "contains", "world") is True
    assert _apply_operator("hello", "contains", "world") is False


def test_apply_operator_gt_lt():
    assert _apply_operator(10, "gt", 5) is True
    assert _apply_operator(3, "lt", 5) is True
    assert _apply_operator(10, "lt", 5) is False
    assert _apply_operator(5, "gt", 10) is False


def test_apply_operator_unknown():
    assert _apply_operator("x", "unknown_op", "x") is False
