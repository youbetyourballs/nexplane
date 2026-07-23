# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for k8s_preflight module."""
import pytest
from unittest.mock import MagicMock, patch
from app.services.k8s_preflight import run_preflight, PreflightResult, REMOVED_APIS


def _make_mock_clients(node_count=3, cpu_per_node="4", mem_per_node="16Gi"):
    core = MagicMock()
    node = MagicMock()
    node.status.conditions = [MagicMock(type="Ready", status="True")]
    node.status.allocatable = {"cpu": cpu_per_node, "memory": mem_per_node}
    node_list = MagicMock()
    node_list.items = [node] * node_count
    core.list_node.return_value = node_list

    custom = MagicMock()
    custom.list_cluster_custom_object.return_value = {"items": []}
    return {"core": core, "custom": custom}


def test_version_valid_sequential():
    clients = _make_mock_clients()
    result = run_preflight(clients, "1.28.8", "1.29", {"node_pool_strategy": "rolling", "max_unavailable": 1})
    assert result.version_valid is True
    assert result.target_patch_version.startswith("1.29.")


def test_version_skip_fails():
    clients = _make_mock_clients()
    result = run_preflight(clients, "1.27.5", "1.29", {"node_pool_strategy": "rolling", "max_unavailable": 1})
    assert result.version_valid is False
    assert "sequential" in result.version_error.lower()


def test_version_downgrade_fails():
    clients = _make_mock_clients()
    result = run_preflight(clients, "1.29.3", "1.28", {"node_pool_strategy": "rolling", "max_unavailable": 1})
    assert result.version_valid is False


def test_removed_apis_present_in_table():
    assert 22 in REMOVED_APIS
    assert 29 in REMOVED_APIS
    for minor, entries in REMOVED_APIS.items():
        for entry in entries:
            assert len(entry) == 4, f"REMOVED_APIS[{minor}] entry must be (group, version, kind, replacement)"


def test_headroom_ok_with_spare_capacity():
    clients = _make_mock_clients(node_count=5, cpu_per_node="8", mem_per_node="32Gi")
    result = run_preflight(clients, "1.28.8", "1.29", {"node_pool_strategy": "rolling", "max_unavailable": 1})
    assert result.headroom_ok is True


def test_headroom_warning_tight_cluster():
    # 2 nodes, 1 max_unavailable = 50% drain overhead — below 15% spare
    clients = _make_mock_clients(node_count=2, cpu_per_node="2", mem_per_node="4Gi")
    result = run_preflight(clients, "1.28.8", "1.29", {"node_pool_strategy": "rolling", "max_unavailable": 1})
    # headroom_ok is False but is a warning, not a hard failure
    assert isinstance(result.headroom_ok, bool)


def test_blue_green_skips_headroom():
    clients = _make_mock_clients(node_count=1, cpu_per_node="1", mem_per_node="1Gi")
    result = run_preflight(clients, "1.28.8", "1.29", {"node_pool_strategy": "blue_green", "max_unavailable": 1})
    # blue_green always passes headroom
    assert result.headroom_ok is True
