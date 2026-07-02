# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Tests for ExecutorProtocol conformance check and planning engine rollback warnings."""

import pathlib
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# ExecutorProtocol conformance
# ---------------------------------------------------------------------------

def test_executor_protocol_is_importable():
    from app.connectors.executor_protocol import ExecutorProtocol
    assert ExecutorProtocol is not None


def test_module_with_execute_and_rollback_passes_hasattr_check():
    """A module exposing execute() and rollback() satisfies the contract check."""
    mod = types.ModuleType("fake_executor")

    async def execute(parameters, asset_ids, connector):
        return {}

    async def rollback(parameters, execution_result, connector):
        return {"rolled_back": True}

    mod.execute = execute
    mod.rollback = rollback

    assert hasattr(mod, "execute") and hasattr(mod, "rollback")


def test_module_missing_rollback_fails_hasattr_check():
    mod = types.ModuleType("incomplete_executor")

    async def execute(parameters, asset_ids, connector):
        return {}

    mod.execute = execute
    assert not (hasattr(mod, "execute") and hasattr(mod, "rollback"))


def test_module_missing_execute_fails_hasattr_check():
    mod = types.ModuleType("incomplete_executor_2")

    async def rollback(parameters, execution_result, connector):
        return {"rolled_back": False}

    mod.rollback = rollback
    assert not (hasattr(mod, "execute") and hasattr(mod, "rollback"))


# ---------------------------------------------------------------------------
# Planning engine rollback_warning injection
# ---------------------------------------------------------------------------

def _make_minimal_catalog(connector_type, action_id, executor_module):
    """Return a minimal catalog mock that resolve_step can call."""
    from app.connectors.catalog_service import ActionOption

    action_def = {
        "action_id": action_id,
        "display_name": "Test Action",
        "description": "test",
        "execution_tier": 1,
        "rollback_action": "test_rollback",
        "estimated_duration_seconds": 10,
        "blast_radius_hint": None,
        "applicable_asset_types": ["server"],
        "generic_action": f"{connector_type}_{action_id}",
        "executor": f"{connector_type}.{action_id}",
    }
    option = ActionOption(
        connector_type=connector_type,
        action_id=action_id,
        action_def=action_def,
        execution_tier=1,
    )
    catalog = MagicMock()
    catalog.get_options_for_action.return_value = [option]
    catalog.get_executor.return_value = executor_module
    return catalog


def test_planning_engine_warns_when_rollback_returns_false(tmp_path):
    """_resolve_step sets rollback_warning when executor source contains rolled_back=False."""
    import importlib.util as _ilu

    # Write a fake executor file with a no-op rollback
    executor_file = tmp_path / "fake_noop.py"
    executor_file.write_text(
        "async def execute(parameters, asset_ids, connector):\n"
        "    return {}\n\n"
        "async def rollback(parameters, execution_result, connector):\n"
        "    return {'rolled_back': False, 'reason': 'irreversible'}\n"
    )

    # Load as a module so inspect.getfile() can resolve it
    spec = _ilu.spec_from_file_location("fake_noop", executor_file)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)

    catalog = _make_minimal_catalog("test_conn", "fake_noop", mod)

    from app.models.asset import Asset, AssetType
    from app.services import planning_engine as pe

    asset = MagicMock(spec=Asset)
    asset.asset_type = MagicMock()
    asset.asset_type.value = "server"
    asset.connector_id = None
    asset.connector = None

    step_def = {
        "generic_action": "test_conn_fake_noop",
        "param_overrides": {},
    }
    desired = {}

    result = pe._resolve_step(step_def, 1, desired, [asset], catalog)

    assert "rollback_warning" in result, "Expected rollback_warning in step result"
    assert "rolled_back=False" in result["rollback_warning"] or "rolled_back" in result["rollback_warning"]


def test_planning_engine_no_warning_for_clean_rollback(tmp_path):
    """_resolve_step does NOT set rollback_warning when rollback returns rolled_back=True."""
    import importlib.util as _ilu

    executor_file = tmp_path / "fake_clean.py"
    executor_file.write_text(
        "async def execute(parameters, asset_ids, connector):\n"
        "    return {}\n\n"
        "async def rollback(parameters, execution_result, connector):\n"
        "    return {'rolled_back': True}\n"
    )

    spec = _ilu.spec_from_file_location("fake_clean", executor_file)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)

    catalog = _make_minimal_catalog("test_conn", "fake_clean", mod)

    from app.models.asset import Asset
    from app.services import planning_engine as pe

    asset = MagicMock(spec=Asset)
    asset.asset_type = MagicMock()
    asset.asset_type.value = "server"
    asset.connector_id = None
    asset.connector = None

    step_def = {
        "generic_action": "test_conn_fake_clean",
        "param_overrides": {},
    }

    result = pe._resolve_step(step_def, 1, {}, [asset], catalog)

    assert "rollback_warning" not in result


def test_planning_engine_raises_when_executor_missing_execute():
    """_resolve_step warns (does not crash) when executor module missing execute."""
    mod = types.ModuleType("broken_executor")
    # Only rollback, no execute
    async def rollback(parameters, execution_result, connector):
        return {"rolled_back": False}
    mod.rollback = rollback

    catalog = _make_minimal_catalog("test_conn", "broken_action", mod)

    from app.models.asset import Asset
    from app.services import planning_engine as pe

    asset = MagicMock(spec=Asset)
    asset.asset_type = MagicMock()
    asset.asset_type.value = "server"
    asset.connector_id = None
    asset.connector = None

    step_def = {
        "generic_action": "test_conn_broken_action",
        "param_overrides": {},
    }

    with pytest.raises(ValueError, match="ExecutorProtocol contract violated"):
        pe._resolve_step(step_def, 1, {}, [asset], catalog)


# ---------------------------------------------------------------------------
# TIER_DEFINITIONS present and correct
# ---------------------------------------------------------------------------

def test_tier_definitions_present():
    from app.connectors.catalog_service import TIER_DEFINITIONS
    assert 1 in TIER_DEFINITIONS
    assert 2 in TIER_DEFINITIONS
    assert 3 in TIER_DEFINITIONS
    assert 5 in TIER_DEFINITIONS
    # Tier 4 is reserved/omitted
    assert 4 not in TIER_DEFINITIONS


def test_tier_5_description_mentions_ssh():
    from app.connectors.catalog_service import TIER_DEFINITIONS
    assert "SSH" in TIER_DEFINITIONS[5]


def test_tier_3_description_mentions_sccm():
    from app.connectors.catalog_service import TIER_DEFINITIONS
    assert "SCCM" in TIER_DEFINITIONS[3]


# ---------------------------------------------------------------------------
# SCCM catalog tier values
# ---------------------------------------------------------------------------

def test_sccm_catalog_all_actions_tier_3():
    import json
    catalog_path = pathlib.Path(__file__).parents[3] / "backend" / "app" / "connectors" / "catalog" / "sccm.json"
    if not catalog_path.exists():
        # Alternate: running inside Docker (/app layout)
        catalog_path = pathlib.Path(__file__).parents[2] / "app" / "connectors" / "catalog" / "sccm.json"
    data = json.loads(catalog_path.read_text())
    for action in data["actions"]:
        assert action["execution_tier"] == 3, (
            f"SCCM action '{action['action_id']}' has execution_tier={action['execution_tier']}, expected 3"
        )
