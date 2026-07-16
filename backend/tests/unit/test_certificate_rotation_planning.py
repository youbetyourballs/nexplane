# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for certificate_rotation planning engine validation."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.models.change_request import ChangeType
from app.services.change_plan_service import PlanBlockedError
from app.services.planning_engine import generate_plan


def _make_cr(desired_outcome: dict):
    """Build a minimal ChangeRequest mock for certificate_rotation."""
    cr = MagicMock()
    cr.change_type = ChangeType.certificate_rotation
    cr.desired_outcome = desired_outcome
    cr.organization_id = "00000000-0000-0000-0000-000000000001"
    return cr


def _make_safety():
    s = MagicMock()
    s.risk_level = MagicMock()
    s.risk_level.value = "low"
    s.risk_score = 10
    return s


@pytest.mark.asyncio
async def test_missing_subject_raises():
    from app.services.planning_engine import _validate_certificate_rotation
    with pytest.raises(PlanBlockedError, match="subject"):
        await _validate_certificate_rotation(
            {"trigger_reason": "scheduled", "scan_scope": ["aws"]},
            org_id="test-org",
        )


@pytest.mark.asyncio
async def test_invalid_trigger_reason_raises():
    from app.services.planning_engine import _validate_certificate_rotation
    with pytest.raises(PlanBlockedError, match="trigger_reason"):
        await _validate_certificate_rotation(
            {"subject": "api.example.com", "trigger_reason": "expired", "scan_scope": ["aws"]},
            org_id="test-org",
        )


@pytest.mark.asyncio
async def test_empty_scan_scope_raises():
    from app.services.planning_engine import _validate_certificate_rotation
    with pytest.raises(PlanBlockedError, match="scan_scope"):
        await _validate_certificate_rotation(
            {"subject": "api.example.com", "trigger_reason": "scheduled", "scan_scope": []},
            org_id="test-org",
        )


@pytest.mark.asyncio
async def test_valid_desired_outcome_passes():
    from app.services.planning_engine import _validate_certificate_rotation
    with patch(
        "app.services.planning_engine._find_connector_for_type",
        new=AsyncMock(return_value=MagicMock()),
    ):
        result = await _validate_certificate_rotation(
            {"subject": "api.example.com", "trigger_reason": "scheduled", "scan_scope": ["aws"]},
            org_id="test-org",
        )
    assert result is None  # no error


def test_generate_plan_certificate_rotation_field_errors():
    """generate_plan must raise PlanBlockedError for invalid fields."""
    cr = _make_cr({"trigger_reason": "expired", "scan_scope": []})
    with pytest.raises(PlanBlockedError):
        generate_plan(cr, assets=[], safety_result=_make_safety())


def test_generate_plan_certificate_rotation_preflight_check():
    """generate_plan must include step_ca_connector_required preflight check."""
    cr = _make_cr({"subject": "api.example.com", "trigger_reason": "scheduled", "scan_scope": ["aws"]})
    plan = generate_plan(cr, assets=[], safety_result=_make_safety())
    names = [c["name"] for c in plan.preflight_checks]
    assert "step_ca_connector_required" in names, f"Expected preflight check not found; got: {names}"
    check = next(c for c in plan.preflight_checks if c["name"] == "step_ca_connector_required")
    assert check["connector_type"] == "step_ca"
