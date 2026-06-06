import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock


def _make_policy(
    expires_at=None,
    allowed_change_types=None,
    max_risk_level="high",
    enabled=True,
):
    from app.models.recurring_job_policy import RecurringJobPolicy
    p = MagicMock(spec=RecurringJobPolicy)
    p.id = uuid.uuid4()
    p.enabled = enabled
    p.expires_at = expires_at
    p.allowed_change_types = allowed_change_types or ["ssm_command", "ec2_stop"]
    p.max_risk_level = max_risk_level
    return p


def test_policy_within_expiry_is_valid():
    from app.services.recurring_job_service import _policy_allows
    policy = _make_policy(expires_at=datetime.now(timezone.utc) + timedelta(days=30))
    ok, reason = _policy_allows(policy, change_type="ssm_command", risk_level="low")
    assert ok is True
    assert reason is None


def test_expired_policy_is_rejected():
    from app.services.recurring_job_service import _policy_allows
    policy = _make_policy(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    ok, reason = _policy_allows(policy, change_type="ssm_command", risk_level="low")
    assert ok is False
    assert "expired" in reason.lower()


def test_disallowed_change_type_is_rejected():
    from app.services.recurring_job_service import _policy_allows
    policy = _make_policy(allowed_change_types=["ssm_command"])
    ok, reason = _policy_allows(policy, change_type="ec2_terminate", risk_level="low")
    assert ok is False
    assert "change_type" in reason.lower()


def test_risk_level_exceeding_policy_is_rejected():
    from app.services.recurring_job_service import _policy_allows
    policy = _make_policy(max_risk_level="medium")
    ok, reason = _policy_allows(policy, change_type="ssm_command", risk_level="critical")
    assert ok is False
    assert "risk" in reason.lower()


def test_no_policy_requires_manual_approval():
    from app.services.recurring_job_service import _policy_allows
    ok, reason = _policy_allows(None, change_type="ssm_command", risk_level="low")
    assert ok is False
    assert "no policy" in reason.lower()
