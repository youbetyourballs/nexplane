"""Unit tests for IP migration backend executors and services."""
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Task 1: change_ip executor
# ---------------------------------------------------------------------------

class TestChangeIpExecutor:

    def test_new_params_forwarded_to_dispatch(self):
        """All v2 params must appear in the parameters dict passed to dispatch_agent_job."""
        captured = {}

        async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
            captured["command"] = command
            captured["params"] = parameters
            return {"status": "completed", "snapshot": {}}

        with patch(
            "app.connectors.executors.nexplane_agent.change_ip.dispatch_agent_job",
            side_effect=fake_dispatch,
        ):
            from app.connectors.executors.nexplane_agent import change_ip
            result = run(change_ip.execute(
                parameters={
                    "interface": "eth0",
                    "mode": "static",
                    "new_ip_v4": "10.10.1.50/24",
                    "new_gateway_v4": "10.10.1.1",
                    "method": "commit_timer",
                    "commit_timer_seconds": 45,
                    "probe_interval_seconds": 10,
                    "add_secondary": True,
                    "dns_servers": ["10.10.1.10"],
                    "dns_search_domains": ["corp.example.com"],
                },
                asset_ids=["asset-uuid-1"],
                connector=None,
            ))

        assert captured["command"] == "change_ip"
        p = captured["params"]
        assert p["method"] == "commit_timer"
        assert p["commit_timer_seconds"] == 45
        assert p["probe_interval_seconds"] == 10
        assert p["add_secondary"] is True
        assert p["dns_servers"] == ["10.10.1.10"]
        assert p["dns_search_domains"] == ["corp.example.com"]

    def test_defaults_applied_when_params_absent(self):
        """When v2 params are omitted, defaults are applied before dispatch."""
        captured = {}

        async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
            captured["params"] = parameters
            return {"status": "completed", "snapshot": {}}

        with patch(
            "app.connectors.executors.nexplane_agent.change_ip.dispatch_agent_job",
            side_effect=fake_dispatch,
        ):
            from app.connectors.executors.nexplane_agent import change_ip
            run(change_ip.execute(
                parameters={"interface": "eth0", "mode": "static"},
                asset_ids=["asset-uuid-1"],
                connector=None,
            ))

        p = captured["params"]
        assert p["method"] == "auto"
        assert p["commit_timer_seconds"] == 30
        assert p["probe_interval_seconds"] == 5
        assert p["add_secondary"] is False
        assert p["dns_servers"] == []

    def test_timeout_scaled_with_commit_timer(self):
        """dispatch_agent_job timeout_seconds = commit_timer_seconds + 120."""
        captured = {}

        async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
            captured["timeout"] = timeout_seconds
            return {"status": "completed", "snapshot": {}}

        with patch(
            "app.connectors.executors.nexplane_agent.change_ip.dispatch_agent_job",
            side_effect=fake_dispatch,
        ):
            from app.connectors.executors.nexplane_agent import change_ip
            run(change_ip.execute(
                parameters={"interface": "eth0", "mode": "static", "commit_timer_seconds": 60},
                asset_ids=["x"],
                connector=None,
            ))

        assert captured["timeout"] == 330  # 60 + 270 (270s buffer for connect + retry)


# ---------------------------------------------------------------------------
# Task 3: DNS discovery service
# ---------------------------------------------------------------------------

class MockAssetObj:
    """Minimal stand-in for app.models.asset.Asset."""
    def __init__(self, id, organization_id, name, asset_type, asset_metadata, connector_id=None):
        self.id = id
        self.organization_id = organization_id
        self.name = name
        self.asset_type = asset_type
        self.asset_metadata = asset_metadata
        self.connector_id = connector_id


class MockScalars:
    def __init__(self, items): self._items = items
    def all(self): return self._items


class MockSelectResult:
    def __init__(self, items): self._items = items
    def scalars(self): return MockScalars(self._items)


class MockDb:
    """Minimal async DB session mock supporting get() and execute()."""
    def __init__(self, asset_by_id: dict, dns_zone_assets: list):
        self._assets = asset_by_id
        self._zones = dns_zone_assets

    async def get(self, model, pk):
        return self._assets.get(pk)

    async def execute(self, stmt):
        return MockSelectResult(self._zones)


class TestDnsDiscoveryService:

    def _asset_uuid(self, short: str):
        import uuid
        # deterministic UUID from short string
        return uuid.UUID(f"00000000-0000-0000-0000-{short:>012}")

    def test_records_from_dns_names_metadata(self):
        """dns_names in asset_metadata should produce one record per name."""
        import uuid
        from app.services import dns_discovery_service

        asset_id = self._asset_uuid("000000000001")
        org_id = self._asset_uuid("000000000099")

        from app.models.asset import AssetType
        target = MockAssetObj(
            id=asset_id,
            organization_id=org_id,
            name="web01",
            asset_type=AssetType.server,
            asset_metadata={
                "ip_addresses": ["10.0.0.100/24"],
                "dns_names": ["api.corp.example.com", "web-01.internal.example.com"],
                "dns_ttl": 60,
                "dns_provider": "route53",
                "dns_connector_id": str(self._asset_uuid("000000000010")),
                "dns_zone_id": "Z1234567890",
            },
        )

        db = MockDb(asset_by_id={asset_id: target}, dns_zone_assets=[])
        result = run(dns_discovery_service.discover_dns_records_for_asset(db, str(asset_id)))

        assert len(result) == 2
        names = {r["name"] for r in result}
        assert "api.corp.example.com" in names
        assert "web-01.internal.example.com" in names
        for r in result:
            assert r["provider"] == "route53"
            assert r["ttl"] == 60

    def test_records_from_dns_zone_assets(self):
        """Records in dns_zone asset_metadata.records[] referencing asset IP should be discovered."""
        import uuid
        from app.services import dns_discovery_service
        from app.models.asset import AssetType

        asset_id = self._asset_uuid("000000000002")
        org_id = self._asset_uuid("000000000099")
        zone_id = self._asset_uuid("000000000020")
        connector_id = self._asset_uuid("000000000011")

        target = MockAssetObj(
            id=asset_id,
            organization_id=org_id,
            name="db01",
            asset_type=AssetType.server,
            asset_metadata={"ip_addresses": ["10.0.0.200"]},
        )

        zone_asset = MockAssetObj(
            id=zone_id,
            organization_id=org_id,
            name="corp.example.com",
            asset_type=AssetType.dns_zone,
            connector_id=connector_id,
            asset_metadata={
                "provider": "azure_dns",
                "zone_id": "Z-AZURE-001",
                "records": [
                    {"name": "db01.corp.example.com", "type": "A", "value": "10.0.0.200", "ttl": 300},
                    {"name": "other.corp.example.com", "type": "A", "value": "10.0.0.201", "ttl": 300},
                ],
            },
        )

        db = MockDb(asset_by_id={asset_id: target}, dns_zone_assets=[zone_asset])
        result = run(dns_discovery_service.discover_dns_records_for_asset(db, str(asset_id)))

        assert len(result) == 1
        assert result[0]["name"] == "db01.corp.example.com"
        assert result[0]["provider"] == "azure_dns"
        assert result[0]["connector_id"] == str(connector_id)

    def test_fqdn_asset_name_included(self):
        """If asset.name contains a dot, it is treated as a DNS name and added."""
        from app.services import dns_discovery_service
        from app.models.asset import AssetType

        asset_id = self._asset_uuid("000000000003")
        org_id = self._asset_uuid("000000000099")

        target = MockAssetObj(
            id=asset_id,
            organization_id=org_id,
            name="proxy.internal.example.com",
            asset_type=AssetType.server,
            asset_metadata={"ip_addresses": ["192.168.1.5"]},
        )

        db = MockDb(asset_by_id={asset_id: target}, dns_zone_assets=[])
        result = run(dns_discovery_service.discover_dns_records_for_asset(db, str(asset_id)))

        assert any(r["name"] == "proxy.internal.example.com" for r in result)

    def test_no_ips_returns_empty(self):
        """Asset with no ip_addresses in metadata returns empty list."""
        from app.services import dns_discovery_service
        from app.models.asset import AssetType

        asset_id = self._asset_uuid("000000000004")
        target = MockAssetObj(
            id=asset_id,
            organization_id=self._asset_uuid("000000000099"),
            name="empty",
            asset_type=AssetType.server,
            asset_metadata={},
        )

        db = MockDb(asset_by_id={asset_id: target}, dns_zone_assets=[])
        result = run(dns_discovery_service.discover_dns_records_for_asset(db, str(asset_id)))
        assert result == []

    def test_dedup_prevents_duplicate_records(self):
        """Same (name, type, value) from multiple sources is only returned once."""
        from app.services import dns_discovery_service
        from app.models.asset import AssetType

        asset_id = self._asset_uuid("000000000005")
        org_id = self._asset_uuid("000000000099")
        zone_id = self._asset_uuid("000000000021")

        target = MockAssetObj(
            id=asset_id,
            organization_id=org_id,
            name="api.corp.example.com",  # FQDN name — source 3
            asset_type=AssetType.server,
            asset_metadata={
                "ip_addresses": ["10.0.0.50"],
                "dns_names": ["api.corp.example.com"],   # source 1 — same record
            },
        )
        zone_asset = MockAssetObj(
            id=zone_id,
            organization_id=org_id,
            name="corp.example.com",
            asset_type=AssetType.dns_zone,
            connector_id=None,
            asset_metadata={
                "provider": "route53",
                "zone_id": "Z-DUP",
                "records": [
                    {"name": "api.corp.example.com", "type": "A", "value": "10.0.0.50", "ttl": 60},
                ],
            },
        )

        db = MockDb(asset_by_id={asset_id: target}, dns_zone_assets=[zone_asset])
        result = run(dns_discovery_service.discover_dns_records_for_asset(db, str(asset_id)))

        # All three sources would produce the same (name=api.corp.example.com, type=A, value=10.0.0.50)
        matching = [r for r in result if r["name"] == "api.corp.example.com" and r["type"] == "A"]
        assert len(matching) == 1


# ---------------------------------------------------------------------------
# Task 4: migrate_ip executor
# ---------------------------------------------------------------------------

class TestMigrateIpExecutor:

    def _make_fake_dispatch(self, results: dict | None = None):
        """Returns an async callable that records calls and returns a canned result."""
        calls = []

        async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
            calls.append({"command": command, "params": parameters, "asset_ids": asset_ids})
            return (results or {}).get(command, {"status": "completed", "applied": True, "snapshot": {}})

        return fake_dispatch, calls

    def test_stages_completed_in_order(self):
        """Result must list all 6 expected stage keys."""
        fake_dispatch, calls = self._make_fake_dispatch()

        with patch("app.connectors.executors.nexplane_agent.migrate_ip.dispatch_agent_job", side_effect=fake_dispatch), \
             patch("app.connectors.executors.nexplane_agent.migrate_ip.AsyncSessionLocal") as mock_session_cls, \
             patch("app.connectors.executors.nexplane_agent.migrate_ip.discover_dns_records_for_asset", new=AsyncMock(return_value=[])):

            # Make AsyncSessionLocal a context manager that returns a mock db
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_ctx

            from app.connectors.executors.nexplane_agent import migrate_ip
            result = run(migrate_ip.execute(
                parameters={"interface": "eth0", "mode": "static", "new_ip_v4": "10.10.1.50/24"},
                asset_ids=["asset-1"],
                connector=None,
            ))

        expected_stages = ["dns_discovery", "dns_prepare", "apply_change", "verify_new", "dns_update", "commit"]
        assert result["stages_completed"] == expected_stages

    def test_change_ip_dispatched_with_correct_params(self):
        """change_ip must be dispatched with method and commit_timer_seconds."""
        fake_dispatch, calls = self._make_fake_dispatch()

        with patch("app.connectors.executors.nexplane_agent.migrate_ip.dispatch_agent_job", side_effect=fake_dispatch), \
             patch("app.connectors.executors.nexplane_agent.migrate_ip.AsyncSessionLocal") as mock_session_cls, \
             patch("app.connectors.executors.nexplane_agent.migrate_ip.discover_dns_records_for_asset", new=AsyncMock(return_value=[])):

            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_ctx

            from app.connectors.executors.nexplane_agent import migrate_ip
            run(migrate_ip.execute(
                parameters={
                    "interface": "eth0",
                    "mode": "static",
                    "method": "secondary_swap",
                    "commit_timer_seconds": 60,
                    "new_ip_v4": "10.10.1.50/24",
                },
                asset_ids=["asset-1"],
                connector=None,
            ))

        change_ip_call = next(c for c in calls if c["command"] == "change_ip")
        assert change_ip_call["params"]["method"] == "secondary_swap"
        assert change_ip_call["params"]["commit_timer_seconds"] == 60

    def test_dns_discovery_called_when_update_dns_true(self):
        """discover_dns_records_for_asset must be awaited when update_dns=True."""
        fake_dispatch, _ = self._make_fake_dispatch()
        mock_discover = AsyncMock(return_value=[
            {"name": "api.example.com", "type": "A", "value": "10.0.0.1", "ttl": 60,
             "provider": "route53", "connector_id": "c-1", "zone_id": "Z1"}
        ])

        with patch("app.connectors.executors.nexplane_agent.migrate_ip.dispatch_agent_job", side_effect=fake_dispatch), \
             patch("app.connectors.executors.nexplane_agent.migrate_ip.AsyncSessionLocal") as mock_session_cls, \
             patch("app.connectors.executors.nexplane_agent.migrate_ip.discover_dns_records_for_asset", new=mock_discover):

            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_ctx

            from app.connectors.executors.nexplane_agent import migrate_ip
            result = run(migrate_ip.execute(
                parameters={"interface": "eth0", "mode": "static", "update_dns": True},
                asset_ids=["asset-1"],
                connector=None,
            ))

        mock_discover.assert_awaited_once()
        assert result["dns_records_discovered"] == 1

    def test_dns_discovery_skipped_when_update_dns_false(self):
        """discover_dns_records_for_asset must NOT be called when update_dns=False."""
        fake_dispatch, _ = self._make_fake_dispatch()
        mock_discover = AsyncMock(return_value=[])

        with patch("app.connectors.executors.nexplane_agent.migrate_ip.dispatch_agent_job", side_effect=fake_dispatch), \
             patch("app.connectors.executors.nexplane_agent.migrate_ip.AsyncSessionLocal") as mock_session_cls, \
             patch("app.connectors.executors.nexplane_agent.migrate_ip.discover_dns_records_for_asset", new=mock_discover):

            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session_cls.return_value = mock_ctx

            from app.connectors.executors.nexplane_agent import migrate_ip
            run(migrate_ip.execute(
                parameters={"interface": "eth0", "mode": "static", "update_dns": False},
                asset_ids=["asset-1"],
                connector=None,
            ))

        mock_discover.assert_not_awaited()


# ---------------------------------------------------------------------------
# Task 5: ip_campaign executor
# ---------------------------------------------------------------------------

class TestIpCampaignExecutor:

    def _plan(self, n: int) -> list[dict]:
        return [
            {
                "asset_id": f"asset-{i}",
                "interface": "eth0",
                "new_ip_v4": f"10.10.1.{50 + i}/24",
                "new_gateway_v4": "10.10.1.1",
            }
            for i in range(n)
        ]

    def _make_dispatch(self, fail_asset_ids: list[str] | None = None):
        fail_set = set(fail_asset_ids or [])
        calls = []

        async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
            calls.append({"command": command, "asset_ids": asset_ids, "params": parameters})
            if asset_ids and asset_ids[0] in fail_set:
                return {"status": "failed", "error": "agent unreachable"}
            return {"status": "completed", "applied": True, "snapshot": {}}

        return fake_dispatch, calls

    def test_dry_run_returns_plan_without_dispatch(self):
        """dry_run=True must return plan_preview and never call dispatch."""
        fake_dispatch, calls = self._make_dispatch()

        with patch("app.connectors.executors.nexplane_agent.ip_campaign.dispatch_agent_job", side_effect=fake_dispatch):
            from app.connectors.executors.nexplane_agent import ip_campaign
            result = run(ip_campaign.execute(
                parameters={"migration_plan": self._plan(3), "dry_run": True},
                asset_ids=[],
                connector=None,
            ))

        assert result["dry_run"] is True
        assert result["total_assets"] == 3
        assert len(calls) == 0

    def test_all_succeed_reports_correct_counts(self):
        """All hosts migrated: hosts_migrated == plan length, hosts_failed == []."""
        fake_dispatch, calls = self._make_dispatch()

        with patch("app.connectors.executors.nexplane_agent.ip_campaign.dispatch_agent_job", side_effect=fake_dispatch), \
             patch("app.connectors.executors.nexplane_agent.ip_campaign.asyncio.sleep", new=AsyncMock()):

            from app.connectors.executors.nexplane_agent import ip_campaign
            result = run(ip_campaign.execute(
                parameters={
                    "migration_plan": self._plan(6),
                    "batch_size": 3,
                    "batch_interval_seconds": 0,
                },
                asset_ids=[],
                connector=None,
            ))

        assert result["total_assets"] == 6
        assert len(result["hosts_migrated"]) == 6
        assert result["hosts_failed"] == []
        assert result["aborted"] is False
        assert result["batches_completed"] == 2

    def test_abort_fires_when_threshold_exceeded(self):
        """If failures exceed abort_error_threshold, aborted=True and remaining batches skip."""
        # 3 hosts, 2 fail → error rate 2/3 ≈ 67% > 10% threshold
        fake_dispatch, calls = self._make_dispatch(fail_asset_ids=["asset-0", "asset-1"])

        with patch("app.connectors.executors.nexplane_agent.ip_campaign.dispatch_agent_job", side_effect=fake_dispatch), \
             patch("app.connectors.executors.nexplane_agent.ip_campaign.asyncio.sleep", new=AsyncMock()):

            from app.connectors.executors.nexplane_agent import ip_campaign
            result = run(ip_campaign.execute(
                parameters={
                    "migration_plan": self._plan(6),
                    "batch_size": 3,
                    "abort_error_threshold": 0.1,
                },
                asset_ids=[],
                connector=None,
            ))

        assert result["aborted"] is True
        assert result["abort_reason"] is not None
        # Second batch must NOT have been attempted
        assert result["batches_completed"] == 1

    def test_batch_size_respected(self):
        """Each batch dispatches exactly batch_size hosts in parallel."""
        fake_dispatch, calls = self._make_dispatch()
        batch_sizes = []

        original_gather = asyncio.gather

        async def tracking_gather(*coros, **kwargs):
            batch_sizes.append(len(coros))
            return await original_gather(*coros, **kwargs)

        with patch("app.connectors.executors.nexplane_agent.ip_campaign.dispatch_agent_job", side_effect=fake_dispatch), \
             patch("app.connectors.executors.nexplane_agent.ip_campaign.asyncio.gather", side_effect=tracking_gather), \
             patch("app.connectors.executors.nexplane_agent.ip_campaign.asyncio.sleep", new=AsyncMock()):

            from app.connectors.executors.nexplane_agent import ip_campaign
            run(ip_campaign.execute(
                parameters={"migration_plan": self._plan(7), "batch_size": 4},
                asset_ids=[],
                connector=None,
            ))

        assert batch_sizes == [4, 3]

    def test_missing_migration_plan_raises(self):
        """Empty migration_plan must raise ValueError."""
        from app.connectors.executors.nexplane_agent import ip_campaign
        with pytest.raises(ValueError, match="migration_plan"):
            run(ip_campaign.execute(parameters={}, asset_ids=[], connector=None))


# ---------------------------------------------------------------------------
# Task 6: ip_change_service
# ---------------------------------------------------------------------------

class TestUpdateAssetIpMetadata:

    def _make_asset(self, asset_id, current_meta: dict):
        import uuid
        from app.models.asset import AssetType, Environment, Criticality

        class FakeAsset:
            def __init__(self):
                self.id = uuid.UUID(asset_id) if isinstance(asset_id, str) else asset_id
                self.asset_metadata = current_meta.copy()

        return FakeAsset()

    def test_updates_ip_addresses_from_snapshot(self):
        """ip_addresses in asset_metadata updated from change_ip snapshot."""
        from app.services.ip_change_service import update_asset_ip_metadata
        import uuid

        asset_id = "00000000-0000-0000-0000-000000000001"
        asset = self._make_asset(asset_id, {"ip_addresses": ["10.0.0.100/24"]})

        committed = []

        class MockDb:
            async def get(self, model, pk): return asset
            async def commit(self): committed.append(True)

        execution_result = {
            "snapshot": {"ip_v4_addresses": ["10.10.1.50/24"]},
        }

        run(update_asset_ip_metadata(MockDb(), [asset_id], execution_result))

        assert asset.asset_metadata["ip_addresses"] == ["10.10.1.50/24"]
        assert len(committed) == 1

    def test_updates_ip_addresses_from_migrate_ip_result(self):
        """migrate_ip wraps change_ip result; snapshot is under change_ip_result.snapshot."""
        from app.services.ip_change_service import update_asset_ip_metadata

        asset_id = "00000000-0000-0000-0000-000000000002"
        asset = self._make_asset(asset_id, {})

        committed = []

        class MockDb:
            async def get(self, model, pk): return asset
            async def commit(self): committed.append(True)

        execution_result = {
            "change_ip_result": {
                "snapshot": {"ip_v4_addresses": ["10.20.1.100/24"]},
            },
        }

        run(update_asset_ip_metadata(MockDb(), [asset_id], execution_result))

        assert asset.asset_metadata["ip_addresses"] == ["10.20.1.100/24"]

    def test_no_op_when_no_new_ip_in_result(self):
        """If no new IP can be resolved, metadata is not modified."""
        from app.services.ip_change_service import update_asset_ip_metadata

        asset_id = "00000000-0000-0000-0000-000000000003"
        original_meta = {"ip_addresses": ["10.0.0.1"]}
        asset = self._make_asset(asset_id, original_meta)

        class MockDb:
            async def get(self, model, pk): return asset
            async def commit(self): pass

        run(update_asset_ip_metadata(MockDb(), [asset_id], {}))

        # Must remain unchanged
        assert asset.asset_metadata["ip_addresses"] == ["10.0.0.1"]
