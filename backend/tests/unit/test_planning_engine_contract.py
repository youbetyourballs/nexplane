# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import types
from unittest.mock import MagicMock

import pytest

from app.services.planning_engine import _calculate_blast_radius, _validate_executor_contract


def _make_executor(capability=None, reason=None):
    """Build a minimal executor module for testing."""
    m = types.ModuleType("test_executor")
    m.execute = lambda *a, **kw: {}
    m.rollback = lambda *a, **kw: {}
    if capability is not None:
        m.ROLLBACK_CAPABILITY = capability
    if reason is not None:
        m.ROLLBACK_REASON = reason
    return m


def _make_catalog(executor_module, action_def=None):
    catalog = MagicMock()
    catalog.get_executor.return_value = executor_module
    catalog.get_action_def.return_value = action_def or {
        "display_name": "Test Action",
        "description": "test",
        "execution_tier": 1,
        "rollback_action": None,
        "rollback_connector_type": None,
    }
    return catalog


class TestValidateExecutorContract:
    def test_missing_rollback_capability_raises_plan_blocked(self):
        from app.services.change_plan_service import PlanBlockedError
        catalog = _make_catalog(_make_executor(capability=None))
        with pytest.raises(PlanBlockedError):
            _validate_executor_contract("gcp", "gcp_create_gke_cluster", catalog)

    def test_full_capability_returns_none(self):
        catalog = _make_catalog(_make_executor(capability="full"))
        result = _validate_executor_contract("gcp", "gcp_create_gke_cluster", catalog)
        assert result is None

    def test_irreversible_returns_warning_string(self):
        catalog = _make_catalog(_make_executor(capability="irreversible", reason="Cluster is gone forever"))
        result = _validate_executor_contract("gcp", "gcp_delete_gke_cluster", catalog)
        assert isinstance(result, str)
        assert "Cluster is gone forever" in result

    def test_irreversible_without_reason_raises_plan_blocked(self):
        from app.services.change_plan_service import PlanBlockedError
        catalog = _make_catalog(_make_executor(capability="irreversible", reason=None))
        with pytest.raises(PlanBlockedError):
            _validate_executor_contract("gcp", "gcp_delete_gke_cluster", catalog)

    def test_missing_execute_raises_plan_blocked(self):
        from app.services.change_plan_service import PlanBlockedError
        m = types.ModuleType("bad")
        m.ROLLBACK_CAPABILITY = "full"
        # No execute or rollback
        catalog = _make_catalog(m)
        with pytest.raises(PlanBlockedError):
            _validate_executor_contract("gcp", "bad_action", catalog)

    def test_import_error_raises_plan_blocked(self):
        from app.services.change_plan_service import PlanBlockedError
        catalog = MagicMock()
        catalog.get_executor.side_effect = ImportError("no module")
        with pytest.raises(PlanBlockedError):
            _validate_executor_contract("gcp", "missing_action", catalog)


class TestCalculateBlastRadiusRollbackAvailable:
    def _make_cr_assets_safety(self):
        cr = MagicMock()
        assets = []
        safety = MagicMock()
        safety.risk_level.value = "low"
        safety.risk_score = 10
        return cr, assets, safety

    def test_rollback_available_true_when_no_irreversible_steps(self):
        cr, assets, safety = self._make_cr_assets_safety()
        result = _calculate_blast_radius(cr, assets, safety, steps=[])
        assert result["rollback_available"] is True

    def test_rollback_available_false_when_step_has_rollback_warning(self):
        cr, assets, safety = self._make_cr_assets_safety()
        steps = [{"rollback_warning": "This step cannot be rolled back: data is gone"}]
        result = _calculate_blast_radius(cr, assets, safety, steps=steps)
        assert result["rollback_available"] is False

    def test_rollback_available_false_when_critical_risk(self):
        cr, assets, safety = self._make_cr_assets_safety()
        safety.risk_level.value = "critical"
        result = _calculate_blast_radius(cr, assets, safety, steps=[])
        assert result["rollback_available"] is False
