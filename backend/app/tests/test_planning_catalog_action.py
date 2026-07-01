# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Run with --noconftest."""
import types
from app.services import planning_engine as pe
from app.models.change_request import ChangeType


class _FakeSafety:  # matches SafetyReviewResult usage in _calculate_blast_radius
    risk_level = types.SimpleNamespace(value="low")
    risk_score = 10


class _FakeCatalog:
    def get_action_def(self, connector_type, action_id):
        return {}  # validation passes for any action in tests


def _cr(desired):
    return types.SimpleNamespace(
        change_type=ChangeType.catalog_action,
        desired_outcome=desired,
        target_asset_ids=[],
        id="00000000-0000-0000-0000-000000000001",
        organization_id="00000000-0000-0000-0000-000000000002",
    )


def test_catalog_action_synthesizes_single_step():
    cr = _cr({"connector_type": "commercial", "action_id": "provision_instance", "params": {"client_id": "acme", "mode": "managed_single_ec2"}})
    plan = pe.generate_plan(cr, assets=[], safety_result=_FakeSafety(), catalog=_FakeCatalog())
    assert len(plan.generated_steps) == 1
    s = plan.generated_steps[0]
    assert s["connector_type"] == "commercial"
    assert s["action_id"] == "provision_instance"
    assert s["parameters"] == {"client_id": "acme", "mode": "managed_single_ec2"}
    assert s.get("purpose") == "execute"
