# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector(env="dev", instance_id="i-abc123"):
    c = MagicMock()
    c.credentials = {
        "access_key_id": "AKIATEST",
        "secret_access_key": "secret",
        "region": "us-east-1",
    }
    return c


def _make_asset(env="dev", instance_id="i-abc123"):
    asset = MagicMock()
    asset.organization_id = "org-1"
    asset.asset_metadata = {"environment": env, "instance_id": instance_id}
    return asset


SNAP_META = {
    "snapshot_id": "snap-abc123",
    "root_volume_id": "vol-old",
    "root_device_name": "/dev/sda1",
    "availability_zone": "us-east-1a",
    "region": "us-east-1",
    "instance_id": "i-abc123",
    "snapshot_type": "ebs",
}

PREFLIGHT_OK = {
    "disk_free_gb": 80.0,
    "disk_ok": True,
    "current_os": "Windows Server 2019 Datacenter",
    "dism_compat_passed": True,
    "dism_compat_issues": [],
    "dotnet_versions": ["4.8.1"],
    "critical_services": ["W32Time", "WinRM"],
    "incompatible_drivers": [],
    "incompatible_software": [],
}

PREFLIGHT_LOW_DISK = {**PREFLIGHT_OK, "disk_free_gb": 10.0, "disk_ok": False}
PREFLIGHT_DISM_FAIL = {
    **PREFLIGHT_OK,
    "dism_compat_passed": False,
    "dism_compat_issues": ["IncompatibleDriver: SomeOldDriver"],
    "dism_exit_code": "0xC1900101",
}


# ---------------------------------------------------------------------------
# Phase 1 — Pre-flight checks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_preflight_disk_check_fail():
    """Disk free < 32 GB must return preflight_failed without taking snapshot."""
    from app.connectors.executors.nexplane_agent.windows_os_upgrade import execute

    dispatch = AsyncMock(return_value=PREFLIGHT_LOW_DISK)
    with patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade.dispatch_agent_job",
        dispatch,
    ):
        result = await execute(
            {"target_version": "2022"},
            ["asset-1"],
            _make_connector(),
        )

    assert result["status"] == "preflight_failed"
    assert "disk" in result["reason"].lower()
    assert result.get("snapshot_id") is None


@pytest.mark.asyncio
async def test_preflight_unsupported_path_2016_to_2022():
    """2016→2022 direct upgrade must return preflight_failed with guidance."""
    from app.connectors.executors.nexplane_agent.windows_os_upgrade import execute

    preflight_2016 = {
        **PREFLIGHT_OK,
        "current_os": "Windows Server 2016 Datacenter",
    }
    dispatch = AsyncMock(return_value=preflight_2016)
    with patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade.dispatch_agent_job",
        dispatch,
    ):
        result = await execute(
            {"target_version": "2022"},
            ["asset-1"],
            _make_connector(),
        )

    assert result["status"] == "preflight_failed"
    assert "2016" in result["reason"] or "2019" in result["reason"]


@pytest.mark.asyncio
async def test_preflight_dism_compat_fail():
    """DISM hard compatibility failure must return preflight_failed with issues list."""
    from app.connectors.executors.nexplane_agent.windows_os_upgrade import execute

    dispatch = AsyncMock(return_value=PREFLIGHT_DISM_FAIL)
    with patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade.dispatch_agent_job",
        dispatch,
    ):
        result = await execute(
            {"target_version": "2022"},
            ["asset-1"],
            _make_connector(),
        )

    assert result["status"] == "preflight_failed"
    assert len(result.get("preflight", {}).get("dism_compat_issues", [])) > 0


@pytest.mark.asyncio
async def test_dry_run_returns_preflight_only():
    """`dry_run=True` must return status=dry_run with full preflight data, no snapshot."""
    from app.connectors.executors.nexplane_agent.windows_os_upgrade import execute

    dispatch = AsyncMock(return_value=PREFLIGHT_OK)
    snap = AsyncMock()
    with patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade.dispatch_agent_job",
        dispatch,
    ), patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade._take_snapshot",
        snap,
    ):
        result = await execute(
            {"target_version": "2022", "dry_run": True},
            ["asset-1"],
            _make_connector(),
        )

    assert result["status"] == "dry_run"
    assert "preflight" in result
    assert result.get("snapshot_id") is None
    snap.assert_not_called()


@pytest.mark.asyncio
async def test_skip_snapshot_blocked_on_prod():
    """`skip_snapshot=True` on a prod asset must raise RuntimeError before upgrade starts."""
    from app.connectors.executors.nexplane_agent.windows_os_upgrade import execute

    dispatch = AsyncMock(return_value=PREFLIGHT_OK)
    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.get = AsyncMock(return_value=_make_asset(env="prod"))

    with patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade.dispatch_agent_job",
        dispatch,
    ), patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade.AsyncSessionLocal",
        return_value=mock_db,
    ):
        with pytest.raises(RuntimeError, match="prod"):
            await execute(
                {"target_version": "2022", "skip_snapshot": True},
                ["asset-1"],
                _make_connector(),
            )


# ---------------------------------------------------------------------------
# Rollback — no snapshot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rollback_no_snapshot():
    """snapshot_id absent → rolled_back: False, reason: no_snapshot_available."""
    from app.connectors.executors.nexplane_agent.windows_os_upgrade import rollback

    result = await rollback({}, {}, None)
    assert result["rolled_back"] is False
    assert result["reason"] == "no_snapshot_available"


# ---------------------------------------------------------------------------
# Rollback — EBS path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rollback_ebs_calls_volume_swap():
    """rollback() with EC2 EBS snapshot_meta delegates to os_upgrade._restore_snapshot."""
    from app.connectors.executors.nexplane_agent.windows_os_upgrade import rollback

    mock_restore = AsyncMock(return_value={
        "restored": True,
        "new_volume_id": "vol-new",
        "old_volume_id": "vol-old",
        "agent_recovered": True,
    })
    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.get = AsyncMock(return_value=_make_asset())

    with patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade._restore_snapshot",
        mock_restore,
    ), patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade.AsyncSessionLocal",
        return_value=mock_db,
    ):
        result = await rollback(
            {"asset_ids": ["asset-1"]},
            {"snapshot_id": "snap-abc123", "snapshot_meta": SNAP_META, "asset_id": "asset-1"},
            _make_connector(),
        )

    assert result["rolled_back"] is True
    assert result["new_volume_id"] == "vol-new"
    assert result["agent_recovered"] is True
    mock_restore.assert_called_once()


@pytest.mark.asyncio
async def test_rollback_ebs_restore_exception():
    """boto3 error during EBS restore → rolled_back: False with reason."""
    from app.connectors.executors.nexplane_agent.windows_os_upgrade import rollback

    mock_restore = AsyncMock(side_effect=RuntimeError("EC2 volume swap failed"))
    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.get = AsyncMock(return_value=_make_asset())

    with patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade._restore_snapshot",
        mock_restore,
    ), patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade.AsyncSessionLocal",
        return_value=mock_db,
    ):
        result = await rollback(
            {},
            {"snapshot_id": "snap-abc123", "snapshot_meta": SNAP_META, "asset_id": "asset-1"},
            _make_connector(),
        )

    assert result["rolled_back"] is False
    assert "EC2 volume swap failed" in result["reason"]


# ---------------------------------------------------------------------------
# Rollback — VSS path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rollback_vss_returns_manual_steps():
    """VSS snapshot_meta → manual steps returned when automated restore fails."""
    from app.connectors.executors.nexplane_agent.windows_os_upgrade import rollback

    vss_meta = {
        "snapshot_type": "vss",
        "shadow_copy_id": "{GUID-1234}",
        "shadow_device": r"\\?\GLOBALROOT\Device\HarddiskVolumeShadowCopy1",
    }
    dispatch = AsyncMock(side_effect=RuntimeError("wbadmin not available"))

    with patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade.dispatch_agent_job",
        dispatch,
    ):
        result = await rollback(
            {},
            {"snapshot_id": "{GUID-1234}", "snapshot_meta": vss_meta, "asset_id": "asset-1"},
            _make_connector(),
        )

    assert result["rolled_back"] is False
    assert result["reason"] == "vss_system_volume_requires_offline_restore"
    assert len(result["manual_steps"]) >= 3
    assert "{GUID-1234}" in result["manual_steps"][2]


# ---------------------------------------------------------------------------
# Agent reconnect timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_reconnect_timeout():
    """_poll_agent_reconnect returns (False, elapsed) when deadline expires."""
    from app.connectors.executors.nexplane_agent.windows_os_upgrade import _poll_agent_reconnect

    dispatch = AsyncMock(side_effect=RuntimeError("agent offline"))

    with patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade.dispatch_agent_job",
        dispatch,
    ), patch("asyncio.sleep", new_callable=AsyncMock):
        reconnected, elapsed = await _poll_agent_reconnect(
            "asset-1", timeout_seconds=1, interval_seconds=1
        )

    assert reconnected is False


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_check_pass():
    """`health_check_command` with exit 0 sets health_check_passed: True."""
    from app.connectors.executors.nexplane_agent.windows_os_upgrade import execute

    dispatch_responses = [
        PREFLIGHT_OK,                             # Phase 1 preflight
        {"shadow_copy_id": "vss-1", "shadow_device": "dev"},  # VSS snapshot (no instance_id)
        None,                                      # upgrade launch
        {"post_upgrade_os": "Windows Server 2022 Datacenter",
         "health_check_passed": True,
         "health_check_output": "Running"},        # verify
    ]
    dispatch = AsyncMock(side_effect=dispatch_responses)
    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    asset = _make_asset(env="dev", instance_id=None)
    asset.asset_metadata = {"environment": "dev"}  # no instance_id → VSS path
    mock_db.get = AsyncMock(return_value=asset)

    poll = AsyncMock(return_value=(True, 120))

    with patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade.dispatch_agent_job",
        dispatch,
    ), patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade.AsyncSessionLocal",
        return_value=mock_db,
    ), patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade._poll_agent_reconnect",
        poll,
    ):
        result = await execute(
            {
                "target_version": "2022",
                "health_check_command": "Get-Service -Name W32Time | Select-Object -ExpandProperty Status",
            },
            ["asset-1"],
            _make_connector(),
        )

    assert result["status"] == "completed"
    assert result["health_check_passed"] is True
    assert result["health_check_output"] == "Running"


@pytest.mark.asyncio
async def test_health_check_fail():
    """`health_check_command` with non-zero exit transitions CR to failed."""
    from app.connectors.executors.nexplane_agent.windows_os_upgrade import execute

    dispatch_responses = [
        PREFLIGHT_OK,
        {"shadow_copy_id": "vss-1", "shadow_device": "dev"},
        None,
        {"post_upgrade_os": "Windows Server 2022 Datacenter",
         "health_check_passed": False,
         "health_check_output": "Service not found"},
    ]
    dispatch = AsyncMock(side_effect=dispatch_responses)
    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    asset = _make_asset(env="dev", instance_id=None)
    asset.asset_metadata = {"environment": "dev"}
    mock_db.get = AsyncMock(return_value=asset)

    poll = AsyncMock(return_value=(True, 90))

    with patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade.dispatch_agent_job",
        dispatch,
    ), patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade.AsyncSessionLocal",
        return_value=mock_db,
    ), patch(
        "app.connectors.executors.nexplane_agent.windows_os_upgrade._poll_agent_reconnect",
        poll,
    ):
        result = await execute(
            {"target_version": "2022", "health_check_command": "Get-Service -Name Missing"},
            ["asset-1"],
            _make_connector(),
        )

    assert result["status"] == "failed"
    assert result["health_check_passed"] is False
    assert result["snapshot_id"] is not None  # snapshot taken before upgrade
