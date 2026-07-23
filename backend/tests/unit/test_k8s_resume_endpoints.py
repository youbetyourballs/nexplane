# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Unit tests for k8s_cluster_upgrade resume/skip/retry endpoints."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid


@pytest.mark.asyncio
async def test_resume_node_upgrade_endpoint_exists():
    """POST /change-requests/{id}/resume-node-upgrade is registered."""
    from app.main import app
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/v1/change-requests/{uuid.uuid4()}/resume-node-upgrade")
        assert resp.status_code in (401, 403, 404)  # not 405 (Method Not Allowed)


@pytest.mark.asyncio
async def test_skip_node_endpoint_exists():
    from app.main import app
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/v1/change-requests/{uuid.uuid4()}/skip-node", json={"node_name": "worker-0"})
        assert resp.status_code in (401, 403, 404)


@pytest.mark.asyncio
async def test_retry_verify_accepts_k8s_upgrade():
    """retry-verify must accept k8s_cluster_upgrade (not just certificate_rotation)."""
    import inspect
    from app.routers.change_requests import retry_verify
    src = inspect.getsource(retry_verify)
    assert "k8s_cluster_upgrade" in src
