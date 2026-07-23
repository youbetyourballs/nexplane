# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for the 9 new high-blast-radius CR type executors."""
import pytest
from unittest.mock import AsyncMock, patch


# dispatch_agent_job is imported lazily inside each executor function;
# patch it at the source so all callers see the mock
_DISPATCH_PATH = "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job"


# ---------------------------------------------------------------------------
# db_major_version_upgrade
# ---------------------------------------------------------------------------

class TestDbMajorVersionUpgrade:
    @pytest.fixture(autouse=True)
    def _mod(self):
        from app.connectors.executors.nexplane_agent import db_major_version_upgrade
        self.mod = db_major_version_upgrade

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "full"

    @pytest.mark.asyncio
    async def test_missing_asset_ids(self):
        with pytest.raises(ValueError, match="asset_ids required"):
            await self.mod.execute({}, [], None)

    @pytest.mark.asyncio
    async def test_missing_target_version(self):
        with pytest.raises(ValueError, match="target_version required"):
            await self.mod.execute({}, ["asset-1"], None)

    @pytest.mark.asyncio
    async def test_dry_run_returns_early(self):
        async def fake(command, parameters, asset_ids, timeout_seconds=60):
            if command == "preflight_db_upgrade":
                return {"status": "ok", "current_version": "14", "estimated_dump_size_gb": 2}
            raise AssertionError(f"Unexpected command: {command}")

        with patch(_DISPATCH_PATH, side_effect=fake):
            result = await self.mod.execute(
                {"db_engine": "postgresql", "target_version": "16", "dry_run": True},
                ["asset-1"], None,
            )
        assert result["status"] == "dry_run"
        assert result["current_version"] == "14"

    @pytest.mark.asyncio
    async def test_preflight_blocked(self):
        async def fake(command, parameters, asset_ids, timeout_seconds=60):
            return {"status": "blocked", "reason": "replication slots active"}

        with patch(_DISPATCH_PATH, side_effect=fake):
            result = await self.mod.execute(
                {"db_engine": "postgresql", "target_version": "16"},
                ["asset-1"], None,
            )
        assert result["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_rollback_no_dump_path(self):
        result = await self.mod.rollback({}, {}, None)
        assert result["rolled_back"] is False
        assert "no_dump_path" in result["reason"]


# ---------------------------------------------------------------------------
# k8s_cluster_upgrade
# ---------------------------------------------------------------------------

class TestK8sClusterUpgrade:
    @pytest.fixture(autouse=True)
    def _mod(self):
        from app.connectors.executors.nexplane_agent import k8s_cluster_upgrade
        self.mod = k8s_cluster_upgrade

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "partial"

    @pytest.mark.asyncio
    async def test_missing_asset_ids(self):
        with pytest.raises(ValueError, match="asset_ids required"):
            await self.mod.execute({}, [], None)

    @pytest.mark.asyncio
    async def test_missing_target_version(self):
        with pytest.raises(ValueError, match="target_version required"):
            await self.mod.execute({}, ["asset-1"], None)

    @pytest.mark.asyncio
    async def test_dry_run(self):
        async def fake(command, parameters, asset_ids, timeout_seconds=60):
            return {"cluster_type": "eks", "current_version": "1.28", "removed_api_violations": [], "node_pools": []}

        with patch(_DISPATCH_PATH, side_effect=fake):
            result = await self.mod.execute(
                {"target_version": "1.29", "dry_run": True},
                ["asset-1"], None,
            )
        assert result["status"] == "dry_run"
        assert result["cluster_type"] == "eks"

    @pytest.mark.asyncio
    async def test_control_plane_failure(self):
        call_n = [0]

        async def fake(command, parameters, asset_ids, timeout_seconds=60):
            call_n[0] += 1
            if command == "preflight_k8s_upgrade":
                return {"cluster_type": "eks", "current_version": "1.28", "node_pools": []}
            if command == "upgrade_k8s_control_plane":
                return {"success": False, "error": "API server unreachable"}
            raise AssertionError(f"Unexpected: {command}")

        with patch(_DISPATCH_PATH, side_effect=fake):
            result = await self.mod.execute({"target_version": "1.29"}, ["asset-1"], None)
        assert result["status"] == "failed"
        assert result["phase"] == "control_plane"
        assert "irreversible" in result["note"].lower()

    @pytest.mark.asyncio
    async def test_rollback_no_node_pools(self):
        result = await self.mod.rollback({}, {}, None)
        assert result["rolled_back"] is False

    @pytest.mark.asyncio
    async def test_rollback_control_plane_failed_state(self):
        result = await self.mod.rollback(
            {}, {"status": "failed", "phase": "control_plane"}, None,
        )
        assert result["rolled_back"] is False
        assert "irreversible" in result["reason"].lower()


# ---------------------------------------------------------------------------
# windows_os_upgrade
# ---------------------------------------------------------------------------

class TestWindowsOsUpgrade:
    @pytest.fixture(autouse=True)
    def _mod(self):
        from app.connectors.executors.nexplane_agent import windows_os_upgrade
        self.mod = windows_os_upgrade

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "full"

    @pytest.mark.asyncio
    async def test_missing_asset_ids(self):
        with pytest.raises(ValueError, match="asset_ids required"):
            await self.mod.execute({}, [], None)

    @pytest.mark.asyncio
    async def test_missing_target_version(self):
        with pytest.raises(ValueError, match="target_version required"):
            await self.mod.execute({}, ["asset-1"], None)

    @pytest.mark.asyncio
    async def test_dism_without_iso_path(self):
        with pytest.raises(ValueError, match="iso_path required"):
            await self.mod.execute(
                {"target_version": "2025", "upgrade_method": "dism_iso"},
                ["asset-1"], None,
            )

    @pytest.mark.asyncio
    async def test_dry_run(self):
        async def fake(command, parameters, asset_ids, timeout_seconds=60):
            return {"status": "ok", "current_version": "2019", "estimated_duration_minutes": 45}

        with patch(_DISPATCH_PATH, side_effect=fake):
            result = await self.mod.execute(
                {"target_version": "2025", "dry_run": True},
                ["asset-1"], None,
            )
        assert result["status"] == "dry_run"
        assert result["current_version"] == "2019"

    @pytest.mark.asyncio
    async def test_rollback_no_snapshot_meta(self):
        result = await self.mod.rollback({}, {}, None)
        assert result["rolled_back"] is False
        assert "no_snapshot_meta" in result["reason"]


# ---------------------------------------------------------------------------
# ad_dc_parallel_upgrade
# ---------------------------------------------------------------------------

class TestAdDcParallelUpgrade:
    @pytest.fixture(autouse=True)
    def _mod(self):
        from app.connectors.executors.active_directory import ad_dc_parallel_upgrade
        self.mod = ad_dc_parallel_upgrade

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "full"

    @pytest.mark.asyncio
    async def test_missing_asset_ids(self):
        with pytest.raises(ValueError):
            await self.mod.execute({}, [], None)

    @pytest.mark.asyncio
    async def test_missing_new_dc_asset_id(self):
        with pytest.raises(ValueError, match="new_dc_asset_id"):
            await self.mod.execute(
                {"domain_name": "corp.example.com"},
                ["old-dc-asset"], None,
            )

    @pytest.mark.asyncio
    async def test_missing_domain_name(self):
        with pytest.raises(ValueError, match="domain_name"):
            await self.mod.execute(
                {"new_dc_asset_id": "new-dc", "new_dc_hostname": "dc2.corp.example.com"},
                ["old-dc-asset"], None,
            )

    @pytest.mark.asyncio
    async def test_dry_run(self):
        async def fake(command, parameters, asset_ids, timeout_seconds=60):
            return {"status": "ok", "fsmo_holders": {}, "replication_status": "healthy"}

        with patch(_DISPATCH_PATH, side_effect=fake):
            result = await self.mod.execute(
                {
                    "new_dc_asset_id": "new-dc",
                    "new_dc_hostname": "dc2.corp.example.com",
                    "domain_name": "corp.example.com",
                    "dry_run": True,
                },
                ["old-dc-asset"], None,
            )
        assert result["status"] == "dry_run"

    @pytest.mark.asyncio
    async def test_rollback_missing_asset_ids(self):
        result = await self.mod.rollback({}, {}, None)
        assert result["rolled_back"] is False


# ---------------------------------------------------------------------------
# Tier-zero AD executors
# ---------------------------------------------------------------------------

class TestAdDomainFunctionalLevelUpgrade:
    @pytest.fixture(autouse=True)
    def _mod(self):
        from app.connectors.executors.active_directory import ad_domain_functional_level_upgrade
        self.mod = ad_domain_functional_level_upgrade

    def test_rollback_capability_is_irreversible(self):
        assert self.mod.ROLLBACK_CAPABILITY == "irreversible"

    @pytest.mark.asyncio
    async def test_rollback_always_returns_false(self):
        result = await self.mod.rollback({}, {"new_level": "Win2025"}, None)
        assert result["rolled_back"] is False
        assert "irreversible" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_missing_target_level(self):
        with pytest.raises(ValueError, match="target_level required"):
            await self.mod.execute({"domain_name": "corp.example.com"}, ["dc-asset"], None)


class TestAdTrustCreate:
    @pytest.fixture(autouse=True)
    def _mod(self):
        from app.connectors.executors.active_directory import ad_trust_create
        self.mod = ad_trust_create

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "full"

    @pytest.mark.asyncio
    async def test_missing_target_domain(self):
        with pytest.raises(ValueError, match="target_domain required"):
            await self.mod.execute(
                {"trust_password": "s3cr3t", "domain_name": "corp.example.com"},
                ["dc"], None,
            )

    @pytest.mark.asyncio
    async def test_rollback_missing_coordinates(self):
        result = await self.mod.rollback({}, {}, None)
        assert result["rolled_back"] is False


class TestAdGpoDeploy:
    @pytest.fixture(autouse=True)
    def _mod(self):
        from app.connectors.executors.active_directory import ad_gpo_deploy
        self.mod = ad_gpo_deploy

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "full"

    @pytest.mark.asyncio
    async def test_missing_gpo_name(self):
        with pytest.raises(ValueError, match="gpo_name required"):
            await self.mod.execute({"domain_name": "corp.example.com"}, ["dc"], None)

    @pytest.mark.asyncio
    async def test_rollback_missing_gpo_id(self):
        result = await self.mod.rollback({}, {}, None)
        assert result["rolled_back"] is False


class TestAdPsoManage:
    @pytest.fixture(autouse=True)
    def _mod(self):
        from app.connectors.executors.active_directory import ad_pso_manage
        self.mod = ad_pso_manage

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "full"

    @pytest.mark.asyncio
    async def test_missing_pso_name(self):
        with pytest.raises(ValueError, match="pso_name required"):
            await self.mod.execute({"domain_name": "corp.example.com"}, ["dc"], None)

    @pytest.mark.asyncio
    async def test_rollback_missing_pso_name(self):
        result = await self.mod.rollback({}, {}, None)
        assert result["rolled_back"] is False


class TestAdStaleComputerCleanup:
    @pytest.fixture(autouse=True)
    def _mod(self):
        from app.connectors.executors.active_directory import ad_stale_computer_cleanup
        self.mod = ad_stale_computer_cleanup

    def test_rollback_capability(self):
        assert self.mod.ROLLBACK_CAPABILITY == "full"

    @pytest.mark.asyncio
    async def test_delete_action_rollback_impossible(self):
        result = await self.mod.rollback(
            {},
            {"action": "delete", "_target_asset_ids": ["dc-asset"]},
            None,
        )
        assert result["rolled_back"] is False
        assert "Deleted" in result["reason"]

    @pytest.mark.asyncio
    async def test_dry_run_calls_discovery_only(self):
        async def fake(command, parameters, asset_ids, timeout_seconds=60):
            if command == "discover_stale_computers":
                return {"stale_accounts": [
                    {"sam_account_name": "OLDPC01$", "distinguished_name": "CN=OLDPC01,OU=Computers,DC=corp,DC=example,DC=com"}
                ]}
            raise AssertionError(f"Should not call {command} in dry_run")

        with patch(_DISPATCH_PATH, side_effect=fake):
            result = await self.mod.execute(
                {"domain_name": "corp.example.com", "dry_run": True},
                ["dc-asset"], None,
            )
        assert result["status"] == "report"
        assert result["stale_account_count"] == 1
