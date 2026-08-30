# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_connector():
    c = MagicMock()
    c.credentials = {"access_key_id": "AKIAIOSFODNN7EXAMPLE", "secret_access_key": "s3cr3t", "region": "us-east-1"}
    return c


def _dispatch_side_effect(command, parameters, asset_ids, timeout_seconds):
    responses = {
        "preflight_kernel_upgrade": {"status": "ok", "current_kernel": "5.15.0-91-generic",
                                      "installed_kernels": ["5.15.0-91-generic"], "already_installed": False,
                                      "package_manager": "apt", "boot_disk_free_gb": 8.0, "warnings": [],
                                      "running_services": ["nginx.service", "postgresql.service"],
                                      "running_containers": ["app-1"]},
        "execute_kernel_upgrade":   {"kernel_installed": "6.1.0-21-generic",
                                      "previous_kernel": "5.15.0-91-generic",
                                      "grub_entry": "Advanced options>Ubuntu 6.1",
                                      "reboot_armed": True},
        "reboot":                   {"action": "graceful_reboot", "scheduled_at": "2026-08-30T00:00:00Z"},
        "verify_kernel_upgrade":    {"verified": True, "running_kernel": "6.1.0-21-generic"},
        "verify_services_post_kernel_upgrade": {"services_healthy": True, "failed_services": [],
                                                "failed_containers": []},
    }
    return responses.get(command, {"status": "ok"})


@pytest.mark.asyncio
async def test_execute_happy_path():
    with patch("app.connectors.executors.nexplane_agent.kernel_upgrade.dispatch_agent_job",
               new=AsyncMock(side_effect=_dispatch_side_effect)), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._take_snapshot",
               new=AsyncMock(return_value={"snapshot_id": "snap-abc", "instance_id": "i-001",
                                            "root_volume_id": "vol-001", "root_device_name": "/dev/xvda",
                                            "availability_zone": "us-east-1a", "region": "us-east-1"})), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._get_aws_creds",
               new=AsyncMock(return_value={"access_key_id": "AKIA...", "secret_access_key": "s3cr3t", "region": "us-east-1"})), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._make_ec2_client",
               return_value=MagicMock()), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._wait_for_agent",
               new=AsyncMock(return_value=True)), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._resolve_instance_id",
               new=AsyncMock(return_value=("i-001", None))):
        from app.connectors.executors.nexplane_agent.kernel_upgrade import execute
        result = await execute({"target_kernel": "6.1.0-21-generic"}, ["asset-1"], _make_connector())
    assert result["status"] == "completed"
    assert result["new_kernel"] == "6.1.0-21-generic"
    assert result["snapshot_id"] == "snap-abc"
    assert result["services_verified"] is True


@pytest.mark.asyncio
async def test_execute_preflight_blocked_returns_failed():
    def _blocked(command, parameters, asset_ids, timeout_seconds):
        if command == "preflight_kernel_upgrade":
            return {"status": "blocked", "reason": "insufficient disk space", "current_kernel": "5.15.0"}
        return {"status": "ok"}

    with patch("app.connectors.executors.nexplane_agent.kernel_upgrade.dispatch_agent_job",
               new=AsyncMock(side_effect=_blocked)), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._get_aws_creds",
               new=AsyncMock(return_value={})):
        from app.connectors.executors.nexplane_agent.kernel_upgrade import execute
        result = await execute({"target_kernel": "6.1.0-21-generic"}, ["asset-1"], _make_connector())
    assert result["status"] == "failed"
    assert result["phase"] == "preflight"


@pytest.mark.asyncio
async def test_execute_auto_rollback_when_verify_fails():
    def _verify_fails(command, parameters, asset_ids, timeout_seconds):
        if command == "verify_kernel_upgrade":
            return {"verified": False, "running_kernel": "5.15.0-91-generic"}
        if command == "preflight_kernel_upgrade":
            return {"status": "ok", "current_kernel": "5.15.0-91-generic",
                    "already_installed": False, "package_manager": "apt",
                    "boot_disk_free_gb": 8.0, "warnings": []}
        return {"status": "ok", "kernel_installed": "6.1.0", "previous_kernel": "5.15.0", "reboot_armed": True}

    with patch("app.connectors.executors.nexplane_agent.kernel_upgrade.dispatch_agent_job",
               new=AsyncMock(side_effect=_verify_fails)), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._take_snapshot",
               new=AsyncMock(return_value={"snapshot_id": "snap-abc", "instance_id": "i-001",
                                            "root_volume_id": "vol-001", "root_device_name": "/dev/xvda",
                                            "availability_zone": "us-east-1a", "region": "us-east-1"})), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._get_aws_creds",
               new=AsyncMock(return_value={"access_key_id": "AKIA..."})), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._make_ec2_client",
               return_value=MagicMock()), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._wait_for_agent",
               new=AsyncMock(return_value=True)), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._restore_snapshot",
               new=AsyncMock(return_value={"restored": True, "new_volume_id": "vol-999"})), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._resolve_instance_id",
               new=AsyncMock(return_value=("i-001", None))):
        from app.connectors.executors.nexplane_agent.kernel_upgrade import execute
        result = await execute({"target_kernel": "6.1.0-21-generic"}, ["asset-1"], _make_connector())
    assert result["status"] == "verify_failed"
    assert "rollback" in result


@pytest.mark.asyncio
async def test_execute_auto_rollback_when_services_fail():
    def _services_fail(command, parameters, asset_ids, timeout_seconds):
        if command == "preflight_kernel_upgrade":
            return {"status": "ok", "current_kernel": "5.15.0-91-generic",
                    "already_installed": False, "package_manager": "apt",
                    "boot_disk_free_gb": 8.0, "warnings": [],
                    "running_services": ["nginx.service"], "running_containers": []}
        if command == "verify_kernel_upgrade":
            return {"verified": True, "running_kernel": "6.1.0-21-generic"}
        if command == "verify_services_post_kernel_upgrade":
            return {"services_healthy": False, "failed_services": ["nginx.service"],
                    "failed_containers": []}
        return {"status": "ok", "kernel_installed": "6.1.0", "previous_kernel": "5.15.0", "reboot_armed": True}

    with patch("app.connectors.executors.nexplane_agent.kernel_upgrade.dispatch_agent_job",
               new=AsyncMock(side_effect=_services_fail)), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._take_snapshot",
               new=AsyncMock(return_value={"snapshot_id": "snap-abc", "instance_id": "i-001",
                                            "root_volume_id": "vol-001", "root_device_name": "/dev/xvda",
                                            "availability_zone": "us-east-1a", "region": "us-east-1"})), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._get_aws_creds",
               new=AsyncMock(return_value={"access_key_id": "AKIA..."})), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._make_ec2_client",
               return_value=MagicMock()), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._wait_for_agent",
               new=AsyncMock(return_value=True)), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._restore_snapshot",
               new=AsyncMock(return_value={"restored": True, "new_volume_id": "vol-999"})), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._resolve_instance_id",
               new=AsyncMock(return_value=("i-001", None))):
        from app.connectors.executors.nexplane_agent.kernel_upgrade import execute
        result = await execute({"target_kernel": "6.1.0-21-generic"}, ["asset-1"], _make_connector())
    assert result["status"] == "service_health_failed"
    assert "rollback" in result
    assert "nginx.service" in result["failed_services"]


@pytest.mark.asyncio
async def test_rollback_dispatches_ebs_restore_when_snapshot_available():
    execution_result = {
        "snapshot_id": "snap-abc",
        "instance_id": "i-001",
        "root_volume_id": "vol-001",
        "root_device_name": "/dev/xvda",
        "availability_zone": "us-east-1a",
        "region": "us-east-1",
        "previous_kernel": "5.15.0-91-generic",
        "asset_id": "asset-1",
    }
    with patch("app.connectors.executors.nexplane_agent.kernel_upgrade._get_aws_creds",
               new=AsyncMock(return_value={"access_key_id": "AKIA..."})), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._make_ec2_client",
               return_value=MagicMock()), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._restore_snapshot",
               new=AsyncMock(return_value={"restored": True, "new_volume_id": "vol-999"})):
        from app.connectors.executors.nexplane_agent.kernel_upgrade import rollback
        result = await rollback({}, execution_result, _make_connector())
    assert result["rolled_back"] is True
    assert result["strategy"] == "ebs_restore"


@pytest.mark.asyncio
async def test_rollback_uses_grub_fallback_when_no_snapshot():
    execution_result = {
        "previous_kernel": "5.15.0-91-generic",
        "asset_id": "asset-1",
        # No snapshot_id
    }
    with patch("app.connectors.executors.nexplane_agent.kernel_upgrade.dispatch_agent_job",
               new=AsyncMock(return_value={"rolled_back": True})), \
         patch("app.connectors.executors.nexplane_agent.kernel_upgrade._wait_for_agent",
               new=AsyncMock(return_value=True)):
        from app.connectors.executors.nexplane_agent.kernel_upgrade import rollback
        result = await rollback({}, execution_result, _make_connector())
    assert result["rolled_back"] is True
    assert result["strategy"] == "grub_fallback"
