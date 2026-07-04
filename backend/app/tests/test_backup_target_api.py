# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_create_backup_target_with_tier(auth_client: AsyncClient):
    resp = await auth_client.post(
        "/backup-targets",
        json={
            "target_description": "Test server backup",
            "expected_cadence_hours": 24,
            "backup_tier": "machine",
            "capture_strategy": "ebs_snapshot",
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["backup_tier"] == "machine"
    assert data["capture_strategy"] == "ebs_snapshot"
    assert data["storage_id"] is None


@pytest.mark.asyncio
async def test_patch_backup_target(auth_client: AsyncClient):
    create = await auth_client.post(
        "/backup-targets",
        json={"target_description": "Patchable target", "expected_cadence_hours": 24},
    )
    assert create.status_code == 201
    target_id = create.json()["id"]

    resp = await auth_client.patch(
        f"/backup-targets/{target_id}",
        json={"capture_strategy": "local_files", "backup_tier": "data"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["capture_strategy"] == "local_files"
    assert data["backup_tier"] == "data"
    assert data["target_description"] == "Patchable target"


@pytest.mark.asyncio
async def test_patch_backup_target_not_found(auth_client: AsyncClient):
    resp = await auth_client.patch(
        f"/backup-targets/{uuid.uuid4()}",
        json={"capture_strategy": "ebs_snapshot"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_recommend_strategy_aws_server(auth_client: AsyncClient):
    resp = await auth_client.get(
        "/backup-targets/recommend-strategy",
        params={"backup_type": "machine_image"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["capture_strategy"] == "ebs_snapshot"
    assert data["backup_tier"] == "machine"
    assert "reason" in data
    assert isinstance(data["alternatives"], list)


@pytest.mark.asyncio
async def test_recommend_strategy_data(auth_client: AsyncClient):
    resp = await auth_client.get(
        "/backup-targets/recommend-strategy",
        params={"backup_type": "file_archive"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["capture_strategy"] == "local_files"
    assert data["backup_tier"] == "data"


@pytest.mark.asyncio
async def test_recommend_strategy_unknown_type(auth_client: AsyncClient):
    resp = await auth_client.get(
        "/backup-targets/recommend-strategy",
        params={"backup_type": "invalid_type"},
    )
    assert resp.status_code == 422
