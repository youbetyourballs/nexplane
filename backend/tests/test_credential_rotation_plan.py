# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import pytest
from unittest.mock import MagicMock, patch
from app.models.change_request import ChangeType, ChangeRequest
from app.services.planning_engine import generate_plan


def _make_cr(steps):
    cr = MagicMock(spec=ChangeRequest)
    cr.change_type = ChangeType.credential_rotation
    cr.desired_outcome = {"steps": steps}
    cr.target_asset_ids = []
    cr.risk_level = MagicMock(value="medium")
    return cr


def test_credential_rotation_plan_validates_steps():
    cr = _make_cr([
        {"connector_type": "aws", "action_id": "rotate_iam_key", "params": {"username": "test"}, "label": "Rotate key"},
    ])
    safety = MagicMock()
    safety.risk_level.value = "medium"
    safety.risk_score = 30
    with patch("app.connectors.catalog_service.get_catalog_service") as mock_catalog:
        mock_catalog.return_value.get_action_def.return_value = {"executor": "aws.rotate_iam_key"}
        result = generate_plan(cr, assets=[], safety_result=safety)
    assert result.generated_steps == []


def test_credential_rotation_plan_rejects_empty_steps():
    from app.services.change_plan_service import PlanBlockedError
    cr = _make_cr([])
    with pytest.raises(PlanBlockedError):
        generate_plan(cr, assets=[], safety_result=None)


def test_credential_rotation_plan_rejects_unknown_action():
    from app.services.change_plan_service import PlanBlockedError
    cr = _make_cr([{"connector_type": "aws", "action_id": "nonexistent_action", "params": {}}])
    with patch("app.connectors.catalog_service.get_catalog_service") as mock_catalog:
        mock_catalog.return_value.get_action_def.side_effect = KeyError("nonexistent_action")
        with pytest.raises(PlanBlockedError):
            generate_plan(cr, assets=[], safety_result=None)
