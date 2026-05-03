import pytest
from unittest.mock import AsyncMock, patch

from app.models.vulnerability import VulnerabilityFinding, RemediationPolicy
from app.services.vuln_remediation_engine import (
    match_policy,
    _default_action,
    generate_change_request_for_finding,
)


def make_finding(**kwargs) -> VulnerabilityFinding:
    defaults = dict(
        scanner="qualys",
        source="webhook",
        finding_type="cve",
        severity="critical",
        title="Test finding",
    )
    defaults.update(kwargs)
    f = VulnerabilityFinding.__new__(VulnerabilityFinding)
    for k, v in defaults.items():
        setattr(f, k, v)
    return f


def make_policy(**kwargs) -> RemediationPolicy:
    defaults = dict(
        match_scanner=None,
        match_finding_type=None,
        match_severity=None,
        match_resource_type=None,
        action_type="patch_packages",
        action_params=None,
        approval_level="require_approval",
        priority=0,
        enabled=True,
    )
    defaults.update(kwargs)
    p = RemediationPolicy.__new__(RemediationPolicy)
    for k, v in defaults.items():
        setattr(p, k, v)
    return p


class TestMatchPolicy:
    def test_no_policies_returns_none(self):
        f = make_finding()
        assert match_policy(f, []) is None

    def test_disabled_policy_not_matched(self):
        f = make_finding()
        p = make_policy(enabled=False)
        assert match_policy(f, [p]) is None

    def test_wildcard_policy_matches_anything(self):
        f = make_finding(scanner="tenable", finding_type="cve", severity="high")
        p = make_policy(priority=5)
        assert match_policy(f, [p]) is p

    def test_scanner_filter(self):
        f = make_finding(scanner="qualys")
        p_qualys = make_policy(match_scanner="qualys", priority=10)
        p_tenable = make_policy(match_scanner="tenable", priority=20)
        result = match_policy(f, [p_qualys, p_tenable])
        assert result is p_qualys

    def test_severity_filter(self):
        f = make_finding(severity="medium")
        p_critical = make_policy(match_severity=["critical", "high"], priority=10)
        p_all = make_policy(match_severity=None, priority=5)
        result = match_policy(f, [p_critical, p_all])
        assert result is p_all

    def test_highest_priority_wins(self):
        f = make_finding()
        p_low = make_policy(priority=1, action_type="notify_only")
        p_high = make_policy(priority=99, action_type="patch_packages")
        result = match_policy(f, [p_low, p_high])
        assert result is p_high


class TestDefaultAction:
    def test_cve_maps_to_patch_packages(self):
        f = make_finding(finding_type="cve")
        assert _default_action(f) == "patch_packages"

    def test_s3_public_access(self):
        f = make_finding(finding_type="misconfiguration", resource_type="s3_public_access")
        assert _default_action(f) == "s3_block_public_access"

    def test_security_group_open(self):
        f = make_finding(finding_type="misconfiguration", resource_type="security_group_open")
        assert _default_action(f) == "security_group_update"

    def test_iam_no_mfa(self):
        f = make_finding(finding_type="misconfiguration", resource_type="iam_no_mfa")
        assert _default_action(f) == "iam_enforce_mfa"

    def test_unknown_resource_type_falls_back(self):
        f = make_finding(finding_type="misconfiguration", resource_type="unknown_thing")
        assert _default_action(f) == "generic_remediation"


@pytest.mark.asyncio
async def test_generate_change_request_creates_draft(db_session, test_org, test_finding):
    """generate_change_request_for_finding must create a DRAFT CR and link it to the finding."""
    with patch(
        "app.services.vuln_remediation_engine.ai_generate_plan",
        new_callable=AsyncMock,
        return_value={"steps": []},
    ):
        cr = await generate_change_request_for_finding(test_finding, None, db_session)

    assert str(cr.status) == "draft"
    assert cr.source == "auto_remediation"
    assert test_finding.change_request_id == cr.id
    assert test_finding.status == "change_request_generated"


@pytest.mark.asyncio
async def test_generate_change_request_uses_policy_action(db_session, test_org, test_finding):
    policy = make_policy(action_type="security_group_update", action_params={"port": 22})
    with patch(
        "app.services.vuln_remediation_engine.ai_generate_plan",
        new_callable=AsyncMock,
        return_value={"steps": []},
    ):
        cr = await generate_change_request_for_finding(test_finding, policy, db_session)

    assert str(cr.change_type) == "security_group_update"
