# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch


def test_rollback_partial_exists_in_enum():
    from app.models.change_request import ChangeRequestStatus
    assert hasattr(ChangeRequestStatus, "rollback_partial")


def test_rollback_failed_exists_in_enum():
    from app.models.change_request import ChangeRequestStatus
    assert hasattr(ChangeRequestStatus, "rollback_failed")


def test_manual_recovery_required_exists_in_enum():
    from app.models.change_request import ChangeRequestStatus
    assert hasattr(ChangeRequestStatus, "manual_recovery_required")


def test_all_steps_succeed_marks_rolled_back():
    from app.services.rollback_executor import _determine_rollback_status
    from app.models.change_request import ChangeRequestStatus
    step_results = [
        {"step_number": 1, "success": True},
        {"step_number": 2, "success": True},
    ]
    status = _determine_rollback_status(step_results, rollback_ran=True)
    assert status == ChangeRequestStatus.rolled_back


def test_partial_step_failure_marks_rollback_partial():
    from app.services.rollback_executor import _determine_rollback_status
    from app.models.change_request import ChangeRequestStatus
    step_results = [
        {"step_number": 1, "success": True},
        {"step_number": 2, "success": False},
    ]
    status = _determine_rollback_status(step_results, rollback_ran=True)
    assert status == ChangeRequestStatus.rollback_partial


def test_rollback_did_not_run_marks_rollback_failed():
    from app.services.rollback_executor import _determine_rollback_status
    from app.models.change_request import ChangeRequestStatus
    status = _determine_rollback_status([], rollback_ran=False)
    assert status == ChangeRequestStatus.rollback_failed


def test_all_steps_fail_marks_rollback_failed():
    from app.services.rollback_executor import _determine_rollback_status
    from app.models.change_request import ChangeRequestStatus
    step_results = [
        {"step_number": 1, "success": False},
        {"step_number": 2, "success": False},
    ]
    status = _determine_rollback_status(step_results, rollback_ran=True)
    assert status == ChangeRequestStatus.rollback_failed
