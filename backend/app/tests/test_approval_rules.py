# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from unittest.mock import MagicMock
from app.models.change_request import RiskLevel
from app.models.user import UserRole
from app.services.safety_engine import check_approval_requirements


def make_approval(role: str, decision: str = "approved"):
    approval = MagicMock()
    approval.decision = decision
    approval.approver = MagicMock()
    approval.approver.role = UserRole(role)
    return approval


def test_low_risk_security_operator_sufficient():
    approvals = [make_approval("security_operator")]
    result = check_approval_requirements(RiskLevel.low, approvals)
    assert result["satisfied"] is True


def test_low_risk_admin_sufficient():
    approvals = [make_approval("admin")]
    result = check_approval_requirements(RiskLevel.low, approvals)
    assert result["satisfied"] is True


def test_medium_risk_requires_approver_or_admin():
    approvals = [make_approval("approver")]
    result = check_approval_requirements(RiskLevel.medium, approvals)
    assert result["satisfied"] is True


def test_medium_risk_security_operator_not_sufficient():
    approvals = [make_approval("security_operator")]
    result = check_approval_requirements(RiskLevel.medium, approvals)
    assert result["satisfied"] is False


def test_high_risk_requires_both_approver_and_admin():
    approvals = [make_approval("approver"), make_approval("admin")]
    result = check_approval_requirements(RiskLevel.high, approvals)
    assert result["satisfied"] is True


def test_high_risk_only_approver_not_sufficient():
    approvals = [make_approval("approver")]
    result = check_approval_requirements(RiskLevel.high, approvals)
    assert result["satisfied"] is False


def test_high_risk_only_admin_not_sufficient():
    approvals = [make_approval("admin")]
    result = check_approval_requirements(RiskLevel.high, approvals)
    assert result["satisfied"] is False


def test_critical_risk_no_auto_execute():
    approvals = [make_approval("approver"), make_approval("admin")]
    result = check_approval_requirements(RiskLevel.critical, approvals)
    assert result["satisfied"] is True
    assert result["no_auto_execute"] is True


def test_no_approvals_never_satisfied():
    for level in RiskLevel:
        result = check_approval_requirements(level, [])
        assert result["satisfied"] is False


def test_rejected_approvals_not_counted():
    approvals = [make_approval("approver", "rejected"), make_approval("admin", "rejected")]
    result = check_approval_requirements(RiskLevel.high, approvals)
    assert result["satisfied"] is False
