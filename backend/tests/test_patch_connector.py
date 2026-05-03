"""
Unit tests for the patch management connector executors.
Uses simple mock objects — no database or agent required.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any
import pytest

from app.connectors.executors.nexplane_agent import check_patch_compliance, run_patch_campaign


# ---------------------------------------------------------------------------
# Helpers / Mocks
# ---------------------------------------------------------------------------

class MockAsset:
    def __init__(self, id: str, hostname: str, metadata: dict | None = None):
        self.id = id
        self.hostname = hostname
        self.metadata = metadata or {}


class MockAssetRepository:
    def __init__(self, assets: list[MockAsset]):
        self._assets = assets

    async def list_assets(self, filter: dict | None = None) -> list[MockAsset]:
        return self._assets


class MockChangeRequest:
    def __init__(self, id: str):
        self.id = id


class MockChangeRequestService:
    def __init__(self):
        self.created: list[dict] = []

    async def create(self, **kwargs) -> MockChangeRequest:
        self.created.append(kwargs)
        return MockChangeRequest(id=f"cr-{len(self.created)}")


class MockAgentClient:
    def __init__(self, fail_asset_ids: list[str] | None = None):
        self._fail = set(fail_asset_ids or [])
        self.calls: list[dict] = []

    async def run_command(self, asset_id: str, command: str, params: dict, timeout_seconds: int) -> dict:
        self.calls.append({"asset_id": asset_id, "command": command, "params": params})
        if asset_id in self._fail:
            return {"status": "failed", "error": "agent unreachable"}
        return {"status": "completed", "data": {"packages_updated": [], "reboot_required": False}}


class MockConnector:
    def __init__(self, assets: list[MockAsset], fail_asset_ids: list[str] | None = None):
        self.asset_repository = MockAssetRepository(assets)
        self.change_request_service = MockChangeRequestService()
        self.agent_client = MockAgentClient(fail_asset_ids=fail_asset_ids)


def recent_patch() -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(days=5)).isoformat()


def stale_patch() -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(days=60)).isoformat()


# ---------------------------------------------------------------------------
# check_patch_compliance tests
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_compliance_all_compliant():
    assets = [
        MockAsset("a1", "host1", {"os_family": "linux", "last_security_patch_at": recent_patch()}),
        MockAsset("a2", "host2", {"os_family": "linux", "last_security_patch_at": recent_patch()}),
    ]
    connector = MockConnector(assets)
    result = run(check_patch_compliance.execute({"max_patch_age_days": 30}, [], connector))
    assert result["total_assets_checked"] == 2
    assert len(result["compliant_hosts"]) == 2
    assert len(result["non_compliant_hosts"]) == 0


def test_compliance_stale_host_detected():
    assets = [
        MockAsset("a1", "host1", {"os_family": "linux", "last_security_patch_at": stale_patch()}),
    ]
    connector = MockConnector(assets)
    result = run(check_patch_compliance.execute({"max_patch_age_days": 30}, [], connector))
    assert len(result["non_compliant_hosts"]) == 1
    assert result["non_compliant_hosts"][0]["asset_id"] == "a1"
    assert result["non_compliant_hosts"][0]["days_since_patch"] >= 60


def test_compliance_no_patch_metadata_is_non_compliant():
    assets = [MockAsset("a1", "host1", {"os_family": "linux"})]
    connector = MockConnector(assets)
    result = run(check_patch_compliance.execute({}, [], connector))
    assert len(result["non_compliant_hosts"]) == 1
    assert result["non_compliant_hosts"][0]["days_since_patch"] is None


def test_compliance_os_family_filter():
    assets = [
        MockAsset("a1", "linux-host", {"os_family": "linux", "last_security_patch_at": stale_patch()}),
        MockAsset("a2", "win-host",   {"os_family": "windows", "last_security_patch_at": stale_patch()}),
    ]
    connector = MockConnector(assets)
    result = run(check_patch_compliance.execute({"os_family": "linux"}, [], connector))
    assert result["total_assets_checked"] == 1
    assert result["non_compliant_hosts"][0]["asset_id"] == "a1"


def test_compliance_auto_create_change_request():
    assets = [MockAsset("a1", "host1", {"os_family": "linux", "last_security_patch_at": stale_patch()})]
    connector = MockConnector(assets)
    result = run(check_patch_compliance.execute(
        {"max_patch_age_days": 30, "create_change_request": True}, [], connector
    ))
    assert len(result["non_compliant_hosts"]) == 1
    assert "change_request_id" in result["non_compliant_hosts"][0]
    assert len(connector.change_request_service.created) == 1
    assert connector.change_request_service.created[0]["change_type"] == "patch_packages"


# ---------------------------------------------------------------------------
# run_patch_campaign tests
# ---------------------------------------------------------------------------

def make_asset_with_inventory(id: str, hostname: str, os_family: str, pkg: str, version: str) -> MockAsset:
    return MockAsset(id, hostname, {
        "os_family": os_family,
        "software_inventory": [{"name": pkg, "version": version, "arch": "amd64"}],
    })


def test_campaign_requires_cve_or_package():
    connector = MockConnector([])
    with pytest.raises(ValueError, match="cve_id or package_name"):
        run(run_patch_campaign.execute({}, [], connector))


def test_campaign_no_affected_assets():
    assets = [make_asset_with_inventory("a1", "host1", "linux", "curl", "7.81.0")]
    connector = MockConnector(assets)
    result = run(run_patch_campaign.execute({"package_name": "openssl"}, [], connector))
    assert result["affected_assets"] == 0
    assert result["hosts_patched"] == []
    assert not result["aborted"]


def test_campaign_patches_affected_assets():
    assets = [
        make_asset_with_inventory("a1", "host1", "linux", "xz-utils", "5.4.1"),
        make_asset_with_inventory("a2", "host2", "linux", "xz-utils", "5.4.1"),
        make_asset_with_inventory("a3", "host3", "linux", "curl", "7.81.0"),  # different pkg
    ]
    connector = MockConnector(assets)
    result = run(run_patch_campaign.execute(
        {"package_name": "xz-utils", "affected_version_lt": "5.6.0"}, [], connector
    ))
    assert result["affected_assets"] == 2
    assert set(result["hosts_patched"]) == {"a1", "a2"}
    assert result["hosts_failed"] == []
    assert not result["aborted"]


def test_campaign_version_filter_excludes_patched_hosts():
    assets = [
        make_asset_with_inventory("a1", "host1", "linux", "xz-utils", "5.4.1"),  # vulnerable
        make_asset_with_inventory("a2", "host2", "linux", "xz-utils", "5.6.2"),  # already patched
    ]
    connector = MockConnector(assets)
    result = run(run_patch_campaign.execute(
        {"package_name": "xz-utils", "affected_version_lt": "5.6.0"}, [], connector
    ))
    assert result["affected_assets"] == 1
    assert result["hosts_patched"] == ["a1"]


def test_campaign_aborts_on_high_error_rate():
    assets = [
        make_asset_with_inventory("a1", "host1", "linux", "xz-utils", "5.4.1"),
        make_asset_with_inventory("a2", "host2", "linux", "xz-utils", "5.4.1"),
        make_asset_with_inventory("a3", "host3", "linux", "xz-utils", "5.4.1"),
    ]
    # All hosts fail
    connector = MockConnector(assets, fail_asset_ids=["a1", "a2", "a3"])
    result = run(run_patch_campaign.execute(
        {"package_name": "xz-utils", "batch_size": 3, "abort_error_threshold": 0.2},
        [],
        connector,
    ))
    assert result["aborted"] is True
    assert result["abort_reason"] is not None
    assert "Error rate" in result["abort_reason"]


def test_campaign_dry_run_does_not_skip_dispatch():
    """dry_run=True is forwarded to each agent command; campaign itself still runs."""
    assets = [make_asset_with_inventory("a1", "host1", "linux", "openssl", "3.0.2")]
    connector = MockConnector(assets)
    result = run(run_patch_campaign.execute(
        {"package_name": "openssl", "dry_run": True}, [], connector
    ))
    assert result["hosts_patched"] == ["a1"]
    assert connector.agent_client.calls[0]["params"]["dry_run"] is True


def test_campaign_windows_host_uses_apply_windows_patches():
    assets = [make_asset_with_inventory("a1", "win1", "windows", "OpenSSL", "3.0.2")]
    connector = MockConnector(assets)
    run(run_patch_campaign.execute({"package_name": "OpenSSL"}, [], connector))
    assert connector.agent_client.calls[0]["command"] == "apply_windows_patches"


def test_campaign_cve_mode_uses_cve_param_for_linux():
    assets = [MockAsset("a1", "host1", {
        "os_family": "linux",
        "software_inventory": [{"name": "xz-utils", "version": "5.4.1"}],
        "security_advisories": [{"cve_id": "CVE-2024-3094", "package": "xz-utils"}],
    })]
    connector = MockConnector(assets)
    run(run_patch_campaign.execute({"cve_id": "CVE-2024-3094"}, [], connector))
    call = connector.agent_client.calls[0]
    assert call["command"] == "apply_linux_patches"
    assert call["params"]["mode"] == "cve"
    assert call["params"]["cve_id"] == "CVE-2024-3094"
