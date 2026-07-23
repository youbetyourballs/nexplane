# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for k8s_cluster_upgrade_executor."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid


@pytest.mark.asyncio
async def test_execute_imports_cleanly():
    from app.services.k8s_cluster_upgrade_executor import (
        execute_k8s_cluster_upgrade,
        execute_k8s_cluster_upgrade_rollback,
        ROLLBACK_CAPABILITY,
    )
    assert ROLLBACK_CAPABILITY == "partial"
    assert callable(execute_k8s_cluster_upgrade)
    assert callable(execute_k8s_cluster_upgrade_rollback)


@pytest.mark.asyncio
async def test_preflight_failure_returns_failed_result():
    """When preflight fails (version skip), executor returns paused=False, failed=True."""
    from app.services.k8s_cluster_upgrade_executor import _run_preflight_phase

    mock_clients = {
        "core": MagicMock(),
        "custom": MagicMock(),
    }
    mock_clients["core"].list_node.return_value = MagicMock(items=[])
    mock_clients["custom"].list_cluster_custom_object.return_value = {"items": []}

    result = await _run_preflight_phase(
        clients=mock_clients,
        current_version="1.27.5",
        target_version="1.29",
        desired_outcome={"node_pool_strategy": "rolling", "max_unavailable": 1},
    )
    assert result["preflight"]["version_valid"] is False
    assert result["failed"] is True


@pytest.mark.asyncio
async def test_preflight_deprecated_api_sets_failed():
    from app.services.k8s_cluster_upgrade_executor import _run_preflight_phase

    core = MagicMock()
    core.list_node.return_value = MagicMock(items=[])
    custom = MagicMock()
    # Simulate a hit on removed API
    custom.list_cluster_custom_object.return_value = {
        "items": [{"metadata": {"name": "old-ingress", "namespace": "default"}}]
    }
    mock_clients = {"core": core, "custom": custom}

    result = await _run_preflight_phase(
        clients=mock_clients,
        current_version="1.28.5",
        target_version="1.29",
        desired_outcome={"node_pool_strategy": "rolling", "max_unavailable": 1},
    )
    assert result["preflight"]["version_valid"] is True
    assert len(result["preflight"]["deprecated_apis"]) > 0
    assert result["failed"] is True


@pytest.mark.asyncio
async def test_rollback_capability_constant():
    from app.services.k8s_cluster_upgrade_executor import ROLLBACK_CAPABILITY
    assert ROLLBACK_CAPABILITY == "partial"


@pytest.mark.asyncio
async def test_rollback_note_in_execution_result_structure():
    """Ensure build_initial_result includes rollback_note."""
    from app.services.k8s_cluster_upgrade_executor import _build_initial_result
    r = _build_initial_result(
        current_version="1.28.8",
        target_version="1.29.4",
        provider="eks",
        cluster_name="prod",
        cluster_id="arn:...",
    )
    assert r["rollback_capability"] == "partial"
    assert "irreversible" in r["rollback_note"].lower()
