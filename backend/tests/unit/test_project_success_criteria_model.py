# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import uuid
import pytest
from app.models.project_success_criteria import (
    ProjectSuccessCriteria,
    CriteriaType,
    CriteriaResult,
)


def test_criteria_type_values():
    assert CriteriaType.cr_completed.value == "cr_completed"
    assert CriteriaType.host_state_check.value == "host_state_check"
    assert CriteriaType.service_check.value == "service_check"
    assert CriteriaType.port_check.value == "port_check"
    assert CriteriaType.manual.value == "manual"


def test_criteria_result_values():
    assert CriteriaResult.pass_.value == "pass"
    assert CriteriaResult.fail.value == "fail"
    assert CriteriaResult.pending_manual.value == "pending_manual"
    assert CriteriaResult.not_checked.value == "not_checked"


def test_model_instantiation():
    c = ProjectSuccessCriteria(
        project_id=uuid.uuid4(),
        type=CriteriaType.manual,
        description="Verify firewall rules",
        assertion={"instructions": "Check iptables -L"},
        last_result=CriteriaResult.not_checked,
    )
    assert c.last_result == CriteriaResult.not_checked
    assert c.last_checked_at is None
    assert c.last_result_detail is None
