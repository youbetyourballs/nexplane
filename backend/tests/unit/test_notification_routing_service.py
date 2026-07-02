# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for notification routing rule evaluation logic."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.notification_routing_rule import NotificationRoutingRule
from app.services.notification_routing_service import _rule_matches, evaluate_routing_rules


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_rule(**kwargs) -> NotificationRoutingRule:
    defaults = dict(
        id=str(uuid.uuid4()),
        name="test-rule",
        enabled=True,
        priority=100,
        match_cr_types=None,
        match_severities=None,
        match_asset_tags=None,
        match_connector_types=None,
        match_status=None,
        notify_user_ids=None,
        notify_role_ids=None,
        notify_channels=None,
        suppress_default=False,
    )
    defaults.update(kwargs)
    rule = MagicMock(spec=NotificationRoutingRule)
    for k, v in defaults.items():
        setattr(rule, k, v)
    return rule


def _make_cr(**kwargs):
    cr = MagicMock()
    defaults = dict(
        change_type="key_rotation",
        severity="high",
        asset_tags={"env": "production"},
        connector_type="aws",
        status="completed",
        organization_id=uuid.uuid4(),
    )
    defaults.update(kwargs)
    for k, v in defaults.items():
        setattr(cr, k, v)
    return cr


# ── _rule_matches tests ───────────────────────────────────────────────────────

def test_rule_matches_all_null_conditions_always_true():
    rule = _make_rule()
    cr = _make_cr()
    assert _rule_matches(rule, cr) is True


def test_rule_matches_cr_type_hit():
    rule = _make_rule(match_cr_types=["key_rotation", "ec2_stop"])
    cr = _make_cr(change_type="key_rotation")
    assert _rule_matches(rule, cr) is True


def test_rule_matches_cr_type_miss():
    rule = _make_rule(match_cr_types=["ec2_stop"])
    cr = _make_cr(change_type="key_rotation")
    assert _rule_matches(rule, cr) is False


def test_rule_matches_severity_hit():
    rule = _make_rule(match_severities=["critical", "high"])
    cr = _make_cr(severity="high")
    assert _rule_matches(rule, cr) is True


def test_rule_matches_severity_miss():
    rule = _make_rule(match_severities=["critical"])
    cr = _make_cr(severity="low")
    assert _rule_matches(rule, cr) is False


def test_rule_matches_asset_tags_all_must_match():
    rule = _make_rule(match_asset_tags={"env": "production", "team": "security"})
    cr = _make_cr(asset_tags={"env": "production", "team": "security", "extra": "x"})
    assert _rule_matches(rule, cr) is True


def test_rule_matches_asset_tags_partial_miss():
    rule = _make_rule(match_asset_tags={"env": "production", "team": "security"})
    cr = _make_cr(asset_tags={"env": "production"})  # missing "team"
    assert _rule_matches(rule, cr) is False


def test_rule_matches_connector_type_hit():
    rule = _make_rule(match_connector_types=["aws", "azure"])
    cr = _make_cr(connector_type="azure")
    assert _rule_matches(rule, cr) is True


def test_rule_matches_status_miss():
    rule = _make_rule(match_status=["failed", "rolled_back"])
    cr = _make_cr(status="completed")
    assert _rule_matches(rule, cr) is False


def test_rule_matches_and_logic_all_must_pass():
    """All non-null conditions must pass — if one fails, the rule doesn't fire."""
    rule = _make_rule(
        match_cr_types=["key_rotation"],
        match_severities=["critical"],  # CR has "high" — should fail
    )
    cr = _make_cr(change_type="key_rotation", severity="high")
    assert _rule_matches(rule, cr) is False


# ── evaluate_routing_rules tests ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_evaluate_no_rules_returns_empty():
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=execute_result)

    cr = _make_cr()
    result = await evaluate_routing_rules(db, cr, "cr_completed")

    assert result["user_ids"] == []
    assert result["channels"] == []
    assert result["suppress_default"] is False
    assert result["matched_rules"] == []


@pytest.mark.asyncio
async def test_evaluate_priority_ordering_lower_wins():
    """Rules are evaluated in ascending priority order; all matching rules fire."""
    rule_high_pri = _make_rule(name="high-pri", priority=10, notify_user_ids=["uid-a"])
    rule_low_pri = _make_rule(name="low-pri", priority=200, notify_user_ids=["uid-b"])

    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [rule_high_pri, rule_low_pri]
    db.execute = AsyncMock(return_value=execute_result)

    cr = _make_cr()
    result = await evaluate_routing_rules(db, cr, "cr_completed")

    assert set(result["user_ids"]) == {"uid-a", "uid-b"}
    assert result["matched_rules"] == ["high-pri", "low-pri"]


@pytest.mark.asyncio
async def test_evaluate_suppress_default_propagates():
    rule = _make_rule(name="suppress-rule", suppress_default=True, notify_user_ids=["uid-x"])

    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [rule]
    db.execute = AsyncMock(return_value=execute_result)

    cr = _make_cr()
    result = await evaluate_routing_rules(db, cr, "cr_completed")

    assert result["suppress_default"] is True
    assert "uid-x" in result["user_ids"]


@pytest.mark.asyncio
async def test_evaluate_deduplicates_user_ids():
    rule1 = _make_rule(name="r1", notify_user_ids=["uid-a", "uid-b"])
    rule2 = _make_rule(name="r2", notify_user_ids=["uid-b", "uid-c"])

    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [rule1, rule2]
    db.execute = AsyncMock(return_value=execute_result)

    cr = _make_cr()
    result = await evaluate_routing_rules(db, cr, "cr_completed")

    assert sorted(result["user_ids"]) == ["uid-a", "uid-b", "uid-c"]


@pytest.mark.asyncio
async def test_evaluate_non_matching_rule_skipped():
    rule = _make_rule(name="specific", match_cr_types=["ec2_stop"])

    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [rule]
    db.execute = AsyncMock(return_value=execute_result)

    cr = _make_cr(change_type="key_rotation")
    result = await evaluate_routing_rules(db, cr, "cr_completed")

    assert result["matched_rules"] == []
    assert result["user_ids"] == []


@pytest.mark.asyncio
async def test_evaluate_channels_merged_no_duplicates():
    rule1 = _make_rule(name="r1", notify_channels=["slack:#ops", "email:sec@co.com"])
    rule2 = _make_rule(name="r2", notify_channels=["slack:#ops", "pagerduty:team"])

    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [rule1, rule2]
    db.execute = AsyncMock(return_value=execute_result)

    cr = _make_cr()
    result = await evaluate_routing_rules(db, cr, "cr_completed")

    assert result["channels"] == ["slack:#ops", "email:sec@co.com", "pagerduty:team"]
