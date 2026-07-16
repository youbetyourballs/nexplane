# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for certificate_rotation planning engine validation."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.models.change_request import ChangeType
from app.services.change_plan_service import PlanBlockedError


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
