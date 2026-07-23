# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for k8s_cluster_upgrade resume/skip/retry endpoints."""
import pytest
import inspect


def test_resume_node_upgrade_endpoint_exists():
    """POST /change-requests/{id}/resume-node-upgrade is registered."""
    from app.routers.change_requests import router
    paths = [r.path for r in router.routes]
    assert any("resume-node-upgrade" in p for p in paths), \
        f"resume-node-upgrade route not found in: {paths}"


def test_skip_node_endpoint_exists():
    """POST /change-requests/{id}/skip-node is registered."""
    from app.routers.change_requests import router
    paths = [r.path for r in router.routes]
    assert any("skip-node" in p for p in paths), \
        f"skip-node route not found in: {paths}"


@pytest.mark.asyncio
async def test_retry_verify_accepts_k8s_upgrade():
    """retry-verify must accept k8s_cluster_upgrade (not just certificate_rotation)."""
    from app.routers.change_requests import retry_verify
    src = inspect.getsource(retry_verify)
    assert "k8s_cluster_upgrade" in src
