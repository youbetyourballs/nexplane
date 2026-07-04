# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for catalog_action rollback wiring and catalog_workflow planning."""
import pytest
from unittest.mock import MagicMock, patch
from app.models.change_request import ChangeType
from app.services.planning_engine import generate_plan as generate_change_plan
from app.services.change_plan_service import PlanBlockedError


def _mock_cr(change_type, desired_outcome):
    cr = MagicMock()
    cr.change_type = change_type
    cr.desired_outcome = desired_outcome
    cr.asset_ids = []
    cr.target_connector_id = None
    cr.connector_type = None
    return cr


def _mock_safety():
    safety = MagicMock()
    safety.risk_level.value = "low"
    safety.risk_score = 0
    return safety


def _mock_catalog(action_def_map):
    catalog = MagicMock()
    def get_action_def(ct, aid):
        key = f"{ct}.{aid}"
        if key not in action_def_map:
            raise KeyError(key)
        return action_def_map[key]
    catalog.get_action_def.side_effect = get_action_def
    return catalog


# ─── catalog_action rollback wiring ───────────────────────────────────────────

def test_catalog_action_wires_rollback_fields():
    action_def = {
        "connector_type": "aws",
        "action_id": "create_key_pair",
        "rollback_action": "delete_key_pair",
        "rollback_connector_type": "aws",
    }
    catalog = _mock_catalog({"aws.create_key_pair": action_def})
    cr = _mock_cr(
        ChangeType.catalog_action,
        {"connector_type": "aws", "action_id": "create_key_pair", "params": {"key_name": "k"}},
    )
    result = generate_change_plan(cr, assets=[], safety_result=_mock_safety(), catalog=catalog)
    step = result.generated_steps[0]
    assert step["rollback_connector_type"] == "aws"
    assert step["rollback_action_id"] == "delete_key_pair"


def test_catalog_action_readonly_has_none_rollback():
    action_def = {"connector_type": "aws", "action_id": "discover_ec2_instances"}
    catalog = _mock_catalog({"aws.discover_ec2_instances": action_def})
    cr = _mock_cr(
        ChangeType.catalog_action,
        {"connector_type": "aws", "action_id": "discover_ec2_instances", "params": {}},
    )
    result = generate_change_plan(cr, assets=[], safety_result=_mock_safety(), catalog=catalog)
    step = result.generated_steps[0]
    assert step["rollback_connector_type"] is None
    assert step["rollback_action_id"] is None


def test_catalog_action_unknown_action_raises():
    catalog = _mock_catalog({})
    cr = _mock_cr(
        ChangeType.catalog_action,
        {"connector_type": "aws", "action_id": "nonexistent", "params": {}},
    )
    with pytest.raises(PlanBlockedError):
        generate_change_plan(cr, assets=[], safety_result={}, catalog=catalog)


# ─── catalog_workflow ─────────────────────────────────────────────────────────

def test_catalog_workflow_generates_n_steps():
    action_def = {
        "connector_type": "aws",
        "action_id": "create_key_pair",
        "rollback_action": "delete_key_pair",
        "rollback_connector_type": "aws",
    }
    catalog = _mock_catalog({"aws.create_key_pair": action_def})
    cr = _mock_cr(
        ChangeType.catalog_workflow,
        {"steps": [
            {"connector_type": "aws", "action_id": "create_key_pair", "params": {"key_name": "a"}},
            {"connector_type": "aws", "action_id": "create_key_pair", "params": {"key_name": "b"}},
        ]},
    )
    result = generate_change_plan(cr, assets=[], safety_result=_mock_safety(), catalog=catalog)
    assert len(result.generated_steps) == 2
    assert result.generated_steps[0]["step_number"] == 1
    assert result.generated_steps[1]["step_number"] == 2
    for step in result.generated_steps:
        assert step["rollback_connector_type"] == "aws"
        assert step["rollback_action_id"] == "delete_key_pair"


def test_catalog_workflow_empty_steps_raises():
    catalog = _mock_catalog({})
    cr = _mock_cr(ChangeType.catalog_workflow, {"steps": []})
    with pytest.raises(PlanBlockedError):
        generate_change_plan(cr, assets=[], safety_result={}, catalog=catalog)


def test_catalog_workflow_unknown_step_action_raises():
    catalog = _mock_catalog({})
    cr = _mock_cr(
        ChangeType.catalog_workflow,
        {"steps": [{"connector_type": "aws", "action_id": "does_not_exist", "params": {}}]},
    )
    with pytest.raises(PlanBlockedError):
        generate_change_plan(cr, assets=[], safety_result={}, catalog=catalog)
