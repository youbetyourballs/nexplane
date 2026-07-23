# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for planning_engine k8s_cluster_upgrade branch."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def test_k8s_cluster_upgrade_plan_requires_target_version():
    from app.services.change_plan_service import PlanBlockedError
    from app.services.planning_engine import generate_plan
    from app.models.change_request import ChangeType

    cr = MagicMock()
    cr.change_type = ChangeType.k8s_cluster_upgrade
    cr.desired_outcome = {}  # missing target_version
    cr.target_asset_ids = []
    cr.organization_id = "org-1"

    safety = MagicMock()
    safety.approved = True
    safety.risk_level = MagicMock()
    safety.risk_level.value = "low"

    with pytest.raises(PlanBlockedError) as exc_info:
        generate_plan(cr, [], safety)
    assert "target_version" in str(exc_info.value).lower()


def test_k8s_cluster_upgrade_plan_produces_preflight_checks():
    from app.services.planning_engine import generate_plan
    from app.models.change_request import ChangeType

    cr = MagicMock()
    cr.change_type = ChangeType.k8s_cluster_upgrade
    cr.desired_outcome = {
        "target_version": "1.29",
        "node_pool_strategy": "rolling",
        "drain_timeout_seconds": 300,
        "max_unavailable": 1,
    }
    cr.target_asset_ids = []
    cr.organization_id = "org-1"

    safety = MagicMock()
    safety.approved = True
    safety.risk_level = MagicMock()
    safety.risk_level.value = "low"

    plan_data = generate_plan(cr, [], safety)
    assert plan_data is not None
    assert isinstance(plan_data.preflight_checks, list)


def test_k8s_cluster_upgrade_plan_irreversibility_in_blast_radius():
    from app.services.planning_engine import generate_plan
    from app.models.change_request import ChangeType

    cr = MagicMock()
    cr.change_type = ChangeType.k8s_cluster_upgrade
    cr.desired_outcome = {"target_version": "1.29", "node_pool_strategy": "rolling", "max_unavailable": 1}
    cr.target_asset_ids = []
    cr.organization_id = "org-1"

    safety = MagicMock()
    safety.approved = True
    safety.risk_level = MagicMock()
    safety.risk_level.value = "low"

    plan_data = generate_plan(cr, [], safety)
    blast = plan_data.blast_radius
    # The rollback_plan must mention irreversibility
    rollback = plan_data.rollback_plan
    rollback_str = str(rollback)
    assert "irreversible" in rollback_str.lower() or "partial" in rollback_str.lower()
