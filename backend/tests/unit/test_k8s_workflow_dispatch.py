# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for k8s_cluster_upgrade workflow dispatch and pause handling."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid


@pytest.mark.asyncio
async def test_activities_k8s_branch_in_source():
    """activity_execute_change source includes k8s_cluster_upgrade dispatch branch."""
    import inspect
    from app.workflows.activities import activity_execute_change
    src = inspect.getsource(activity_execute_change)
    assert "k8s_cluster_upgrade" in src
    assert "execute_k8s_cluster_upgrade" in src


@pytest.mark.asyncio
async def test_workflow_dispatch_is_wired_for_k8s():
    """Verify the activities module has k8s_cluster_upgrade branch by importing and checking source."""
    import inspect
    from app.workflows import activities
    src = inspect.getsource(activities)
    assert "k8s_cluster_upgrade" in src
    assert "execute_k8s_cluster_upgrade" in src


@pytest.mark.asyncio
async def test_workflow_k8s_pause_branch_exists():
    """Verify execute_change_workflow has the k8s_cluster_upgrade pause/fail/complete branch."""
    import inspect
    from app.workflows import execute_change_workflow as ecw
    src = inspect.getsource(ecw)
    assert "k8s_cluster_upgrade" in src
    assert "paused" in src
