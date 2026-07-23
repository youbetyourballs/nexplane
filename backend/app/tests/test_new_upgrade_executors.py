# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for the 9 new high-blast-radius CR type executors.

These tests verify: ROLLBACK_CAPABILITY values, execute() parameter validation,
rollback() behavior when execution_result is missing coordinates, and
dry_run short-circuit.

All tests mock dispatch_agent_job — live smoke tests cover real infrastructure.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def mock_dispatch(**kwargs):
    """Returns an AsyncMock that returns kwargs as the job result."""
    async def _dispatch(command, parameters, asset_ids, timeout_seconds=60):
        return kwargs
    return _dispatch


# ---------------------------------------------------------------------------
# db_major_version_upgrade
# ---------------------------------------------------------------------------

class TestDbMajorVersionUpgrade:
    def setup_method(self):
        from app.connectors.executors.nexplane_agent import db_major_version_upgrade as mod
        self.mod = mod

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "full"

    def test_missing_asset_ids(self):
        with pytest.raises(ValueError, match="asset_ids required"):
            run(self.mod.execute({}, [], None))

    def test_missing_target_version(self):
        with pytest.raises(ValueError, match="target_version required"):
            run(self.mod.execute({}, ["asset-1"], None))

    def test_dry_run_returns_early(self):
        async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=60):
            if command == "preflight_db_upgrade":
                return {"status": "ok", "current_version": "14", "estimated_dump_size_gb": 2}
            raise AssertionError(f"Unexpected command in dry_run: {command}")

        with patch(
            "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
            side_effect=fake_dispatch,
        ):
            result = run(self.mod.execute(
                {"db_engine": "postgresql", "target_version": "16", "dry_run": True},
                ["asset-1"],
                None,
            ))
        assert result["status"] == "dry_run"
        assert result["current_version"] == "14"
        assert result["target_version"] == "16"

    def test_preflight_blocked_returns_blocked(self):
        async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=60):
            return {"status": "blocked", "reason": "replication slots active"}

        with patch(
            "app.connectors.executors.nexplane_agent.db_major_version_upgrade.dispatch_agent_job",
            side_effect=fake_dispatch,
        ):
            result = run(self.mod.execute(
                {"db_engine": "postgresql", "target_version": "16"},
                ["asset-1"],
                None,
            ))
        assert result["status"] == "blocked"
        assert "replication slots" in result["reason"]

    def test_rollback_no_dump_path(self):
        result = run(self.mod.rollback({}, {}, None))
        assert result["rolled_back"] is False
        assert "no_dump_path" in result["reason"]


# ---------------------------------------------------------------------------
# k8s_cluster_upgrade
# ---------------------------------------------------------------------------

class TestK8sClusterUpgrade:
    def setup_method(self):
        from app.connectors.executors.nexplane_agent import k8s_cluster_upgrade as mod
        self.mod = mod

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "partial"

    def test_missing_asset_ids(self):
        with pytest.raises(ValueError, match="asset_ids required"):
            run(self.mod.execute({}, [], None))

    def test_missing_target_version(self):
        with pytest.raises(ValueError, match="target_version required"):
            run(self.mod.execute({}, ["asset-1"], None))

    def test_dry_run(self):
        async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=60):
            return {
                "status": "ok",
                "cluster_type": "eks",
                "current_version": "1.28",
                "removed_api_violations": [],
                "node_pools": [],
            }

        with patch(
            "app.connectors.executors.nexplane_agent.k8s_cluster_upgrade.dispatch_agent_job",
            side_effect=fake_dispatch,
        ):
            result = run(self.mod.execute(
                {"target_version": "1.29", "dry_run": True},
                ["asset-1"],
                None,
            ))
        assert result["status"] == "dry_run"
        assert result["cluster_type"] == "eks"

    def test_control_plane_failure_returns_irreversible_note(self):
        call_count = [0]

        async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=60):
            call_count[0] += 1
            if command == "preflight_k8s_upgrade":
                return {"status": "ok", "cluster_type": "eks", "current_version": "1.28", "node_pools": []}
            if command == "upgrade_k8s_control_plane":
                return {"success": False, "error": "API server unreachable"}
            raise AssertionError(f"Unexpected: {command}")

        with patch(
            "app.connectors.executors.nexplane_agent.k8s_cluster_upgrade.dispatch_agent_job",
            side_effect=fake_dispatch,
        ):
            result = run(self.mod.execute(
                {"target_version": "1.29"},
                ["asset-1"],
                None,
            ))
        assert result["status"] == "failed"
        assert result["phase"] == "control_plane"
        assert "irreversible" in result["note"].lower()

    def test_rollback_no_node_pools(self):
        result = run(self.mod.rollback({}, {}, None))
        assert result["rolled_back"] is False

    def test_rollback_control_plane_failed_state(self):
        result = run(self.mod.rollback(
            {},
            {"status": "failed", "phase": "control_plane"},
            None,
        ))
        assert result["rolled_back"] is False
        assert "irreversible" in result["reason"].lower()


# ---------------------------------------------------------------------------
# windows_os_upgrade
# ---------------------------------------------------------------------------

class TestWindowsOsUpgrade:
    def setup_method(self):
        from app.connectors.executors.nexplane_agent import windows_os_upgrade as mod
        self.mod = mod

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "full"

    def test_missing_asset_ids(self):
        with pytest.raises(ValueError, match="asset_ids required"):
            run(self.mod.execute({}, [], None))

    def test_missing_target_version(self):
        with pytest.raises(ValueError, match="target_version required"):
            run(self.mod.execute({}, ["asset-1"], None))

    def test_dism_without_iso_path(self):
        with pytest.raises(ValueError, match="iso_path required"):
            run(self.mod.execute(
                {"target_version": "2025", "upgrade_method": "dism_iso"},
                ["asset-1"],
                None,
            ))

    def test_dry_run(self):
        async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=60):
            return {"status": "ok", "current_version": "2019", "estimated_duration_minutes": 45}

        with patch(
            "app.connectors.executors.nexplane_agent.windows_os_upgrade.dispatch_agent_job",
            side_effect=fake_dispatch,
        ):
            result = run(self.mod.execute(
                {"target_version": "2025", "dry_run": True},
                ["asset-1"],
                None,
            ))
        assert result["status"] == "dry_run"
        assert result["current_version"] == "2019"

    def test_rollback_no_snapshot_meta(self):
        result = run(self.mod.rollback({}, {}, None))
        assert result["rolled_back"] is False
        assert "no_snapshot_meta" in result["reason"]


# ---------------------------------------------------------------------------
# ad_dc_parallel_upgrade
# ---------------------------------------------------------------------------

class TestAdDcParallelUpgrade:
    def setup_method(self):
        from app.connectors.executors.active_directory import ad_dc_parallel_upgrade as mod
        self.mod = mod

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "full"

    def test_missing_asset_ids(self):
        with pytest.raises(ValueError):
            run(self.mod.execute({}, [], None))

    def test_missing_new_dc_asset_id(self):
        with pytest.raises(ValueError, match="new_dc_asset_id"):
            run(self.mod.execute(
                {"domain_name": "corp.example.com"},
                ["old-dc-asset"],
                None,
            ))

    def test_missing_domain_name(self):
        with pytest.raises(ValueError, match="domain_name"):
            run(self.mod.execute(
                {"new_dc_asset_id": "new-dc", "new_dc_hostname": "dc2.corp.example.com"},
                ["old-dc-asset"],
                None,
            ))

    def test_dry_run(self):
        async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=60):
            return {"status": "ok", "fsmo_holders": {}, "replication_status": "healthy"}

        with patch(
            "app.connectors.executors.active_directory.ad_dc_parallel_upgrade.dispatch_agent_job",
            side_effect=fake_dispatch,
        ):
            result = run(self.mod.execute(
                {
                    "new_dc_asset_id": "new-dc",
                    "new_dc_hostname": "dc2.corp.example.com",
                    "domain_name": "corp.example.com",
                    "dry_run": True,
                },
                ["old-dc-asset"],
                None,
            ))
        assert result["status"] == "dry_run"

    def test_rollback_missing_asset_ids(self):
        result = run(self.mod.rollback({}, {}, None))
        assert result["rolled_back"] is False


# ---------------------------------------------------------------------------
# Tier-zero AD executors
# ---------------------------------------------------------------------------

class TestAdDomainFunctionalLevelUpgrade:
    def setup_method(self):
        from app.connectors.executors.active_directory import ad_domain_functional_level_upgrade as mod
        self.mod = mod

    def test_rollback_capability_is_irreversible(self):
        assert self.mod.ROLLBACK_CAPABILITY == "irreversible"

    def test_rollback_always_returns_false(self):
        result = run(self.mod.rollback({}, {"new_level": "Win2025"}, None))
        assert result["rolled_back"] is False
        assert "irreversible" in result["reason"].lower()

    def test_missing_target_level(self):
        with pytest.raises(ValueError, match="target_level required"):
            run(self.mod.execute({"domain_name": "corp.example.com"}, ["dc-asset"], None))


class TestAdTrustCreate:
    def setup_method(self):
        from app.connectors.executors.active_directory import ad_trust_create as mod
        self.mod = mod

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "full"

    def test_missing_target_domain(self):
        with pytest.raises(ValueError, match="target_domain required"):
            run(self.mod.execute({"trust_password": "s3cr3t", "domain_name": "corp.example.com"}, ["dc"], None))

    def test_rollback_missing_coordinates(self):
        result = run(self.mod.rollback({}, {}, None))
        assert result["rolled_back"] is False


class TestAdGpoDeploy:
    def setup_method(self):
        from app.connectors.executors.active_directory import ad_gpo_deploy as mod
        self.mod = mod

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "full"

    def test_missing_gpo_name(self):
        with pytest.raises(ValueError, match="gpo_name required"):
            run(self.mod.execute({"domain_name": "corp.example.com"}, ["dc"], None))

    def test_rollback_missing_gpo_id(self):
        result = run(self.mod.rollback({}, {}, None))
        assert result["rolled_back"] is False


class TestAdPsoManage:
    def setup_method(self):
        from app.connectors.executors.active_directory import ad_pso_manage as mod
        self.mod = mod

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "full"

    def test_missing_pso_name(self):
        with pytest.raises(ValueError, match="pso_name required"):
            run(self.mod.execute({"domain_name": "corp.example.com"}, ["dc"], None))

    def test_rollback_missing_pso_name(self):
        result = run(self.mod.rollback({}, {}, None))
        assert result["rolled_back"] is False


class TestAdStaleComputerCleanup:
    def setup_method(self):
        from app.connectors.executors.active_directory import ad_stale_computer_cleanup as mod
        self.mod = mod

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "full"

    def test_delete_action_rollback_impossible(self):
        result = run(self.mod.rollback(
            {},
            {"action": "delete", "_target_asset_ids": ["dc-asset"]},
            None,
        ))
        assert result["rolled_back"] is False
        assert "Deleted" in result["reason"]

    def test_dry_run_calls_discovery_only(self):
        async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=60):
            if command == "discover_stale_computers":
                return {"stale_accounts": [{"sam_account_name": "OLDPC01$", "distinguished_name": "CN=OLDPC01,OU=Computers,DC=corp,DC=example,DC=com"}]}
            raise AssertionError(f"Should not call {command} in dry_run")

        with patch(
            "app.connectors.executors.active_directory.ad_tier_zero.dispatch_agent_job",
            side_effect=fake_dispatch,
        ):
            result = run(self.mod.execute(
                {"domain_name": "corp.example.com", "dry_run": True},
                ["dc-asset"],
                None,
            ))
        assert result["status"] == "report"
        assert result["stale_account_count"] == 1
