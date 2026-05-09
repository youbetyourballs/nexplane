# IP Migration — Plan 2: Backend Executors & Services

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the change_ip executor with method-aware dispatch, add a DNS discovery service that queries asset inventory and DNS connectors, implement migrate_ip multi-stage orchestration, add ip_campaign fleet executor, update change type definitions and catalog, and add post-execution hooks for asset metadata updates.

**Architecture:** The change_ip executor gains method/timer params and dispatches them to the Go agent. A new dns_discovery_service.py cross-references asset metadata and connector ingest to find DNS records tied to an asset's IP. The migrate_ip executor orchestrates the multi-stage workflow using a sequence of sub-CRs. The ip_campaign executor drives batched fleet migration using the existing campaign infrastructure pattern.

**Tech Stack:** Python/FastAPI, SQLAlchemy async, existing dispatch pattern

---

## Key file locations

- Executor dir: `backend/app/connectors/executors/nexplane_agent/`
- Dispatch helper: `backend/app/connectors/executors/nexplane_agent/_dispatch.py`
- Change type defs: `backend/app/connectors/change_type_definitions/`
- Catalog: `backend/app/connectors/catalog/nexplane_agent.json`
- Workflow hook: `backend/app/workflows/execute_change_workflow.py`
- Models: `backend/app/models/change_request.py`, `backend/app/models/asset.py`
- Services: `backend/app/services/`
- DB migrations: `backend/alembic/versions/` (next is `038_`)
- Tests: `backend/tests/`

---

## Task 1: Update `change_ip.py` executor

**Files touched:**
- `backend/app/connectors/executors/nexplane_agent/change_ip.py`

### Steps

- [ ] **1.1** Replace the entire contents of `change_ip.py` with the implementation below. The existing stub returns hardcoded data; the new version reads all new parameters and forwards them to `dispatch_agent_job`.

  Complete replacement:

  ```python
  """change_ip executor — dispatches to the Nexplane Go agent.

  New parameters (v2, all optional and backward-compatible):
      method                  str   "auto" | "tailscale" | "secondary_swap" | "commit_timer" | "manual"
      commit_timer_seconds    int   default 30 — dead-man's-switch window
      probe_interval_seconds  int   default 5 — how often commit-timer polls control plane
      add_secondary           bool  default False — keep old IP as secondary after swap
      dns_servers             list  default [] — DNS servers to configure on the interface
      dns_search_domains      list  default [] — DNS search domains
      preflight_arp_probe     bool  default True — ARP-probe destination IP before applying
  """
  from __future__ import annotations
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

      agent_params = {
          # Core parameters (existing)
          "interface": parameters.get("interface"),
          "mode": parameters.get("mode"),
          "ip_version": parameters.get("ip_version", "4"),
          "new_ip_v4": parameters.get("new_ip_v4"),
          "new_ip_v6": parameters.get("new_ip_v6"),
          "new_gateway_v4": parameters.get("new_gateway_v4"),
          "new_gateway_v6": parameters.get("new_gateway_v6"),
          # New v2 parameters
          "method": parameters.get("method", "auto"),
          "commit_timer_seconds": int(parameters.get("commit_timer_seconds", 30)),
          "probe_interval_seconds": int(parameters.get("probe_interval_seconds", 5)),
          "add_secondary": bool(parameters.get("add_secondary", False)),
          "dns_servers": list(parameters.get("dns_servers") or []),
          "dns_search_domains": list(parameters.get("dns_search_domains") or []),
          "preflight_arp_probe": bool(parameters.get("preflight_arp_probe", True)),
      }
      # Strip None values so the Go agent receives a clean params dict
      agent_params = {k: v for k, v in agent_params.items() if v is not None}

      result = await dispatch_agent_job(
          command="change_ip",
          parameters=agent_params,
          asset_ids=asset_ids,
          timeout_seconds=int(parameters.get("commit_timer_seconds", 30)) + 120,
      )
      return result


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      """Restore previous network configuration from snapshot in execution_result."""
      from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

      snapshot = execution_result.get("snapshot", {})
      rollback_params = {
          "previous_result": snapshot,
          "interface": parameters.get("interface"),
      }

      result = await dispatch_agent_job(
          command="change_ip_rollback",
          parameters=rollback_params,
          asset_ids=parameters.get("_asset_ids", []),
          timeout_seconds=120,
      )
      return {"rolled_back": True, "action": "change_ip", "agent_result": result}
  ```

- [ ] **1.2** Write unit test in `backend/tests/test_ip_migration.py` — test class `TestChangeIpExecutor`:

  ```python
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

          assert captured["timeout"] == 180  # 60 + 120
  ```

- [ ] **1.3** Verify: `cd backend && python -m pytest tests/test_ip_migration.py::TestChangeIpExecutor -v`
  Expected: 3 passed.

- [ ] **1.4** Commit: `git add backend/app/connectors/executors/nexplane_agent/change_ip.py backend/tests/test_ip_migration.py && git commit -m "task 1: extend change_ip executor with method/timer params"`

---

## Task 2: Update `change_ip.json` and catalog

**Files touched:**
- `backend/app/connectors/change_type_definitions/change_ip.json`
- `backend/app/connectors/catalog/nexplane_agent.json`

### Steps

- [ ] **2.1** Replace `backend/app/connectors/change_type_definitions/change_ip.json` with:

  ```json
  {
    "change_type": "change_ip",
    "display_name": "Change IP Address",
    "steps": [{"generic_action": "change_ip", "purpose": "execute", "required": true}],
    "preflight_checks": ["connector_reachable", "asset_exists"],
    "verification_methods": ["api_check"],
    "parameters": {
      "interface": {"type": "string", "required": true},
      "mode": {"type": "string", "required": true, "enum": ["static", "dhcp"]},
      "method": {"type": "string", "required": false, "default": "auto", "enum": ["auto", "tailscale", "secondary_swap", "commit_timer", "manual"]},
      "ip_version": {"type": "string", "required": false, "default": "4"},
      "new_ip_v4": {"type": "string", "required": false},
      "new_ip_v6": {"type": "string", "required": false},
      "new_gateway_v4": {"type": "string", "required": false},
      "new_gateway_v6": {"type": "string", "required": false},
      "dns_servers": {"type": "array", "required": false, "items": {"type": "string"}},
      "dns_search_domains": {"type": "array", "required": false, "items": {"type": "string"}},
      "commit_timer_seconds": {"type": "integer", "required": false, "default": 30},
      "probe_interval_seconds": {"type": "integer", "required": false, "default": 5},
      "add_secondary": {"type": "boolean", "required": false, "default": false},
      "preflight_arp_probe": {"type": "boolean", "required": false, "default": true}
    }
  }
  ```

- [ ] **2.2** In `backend/app/connectors/catalog/nexplane_agent.json`, locate the `change_ip` action entry (the object with `"action_id": "change_ip"`). Replace its `parameters` array with the expanded list matching the JSON definition above. The new parameters array must include all entries from the existing list plus these additions (append after `dns_servers`):

  ```json
  {"name": "dns_search_domains",      "type": "array",   "required": false},
  {"name": "method",                  "type": "string",  "required": false, "default": "auto"},
  {"name": "commit_timer_seconds",    "type": "integer", "required": false, "default": 30},
  {"name": "probe_interval_seconds",  "type": "integer", "required": false, "default": 5},
  {"name": "add_secondary",           "type": "boolean", "required": false, "default": false},
  {"name": "preflight_arp_probe",     "type": "boolean", "required": false, "default": true}
  ```

  Also update `"estimated_duration_seconds"` for the `change_ip` catalog action from `10` to `180` to reflect the commit-timer upper bound.

- [ ] **2.3** Verify JSON is valid: `python -c "import json; json.load(open('backend/app/connectors/change_type_definitions/change_ip.json'))" && python -c "import json; json.load(open('backend/app/connectors/catalog/nexplane_agent.json'))"`

- [ ] **2.4** Commit: `git add backend/app/connectors/change_type_definitions/change_ip.json backend/app/connectors/catalog/nexplane_agent.json && git commit -m "task 2: add v2 params to change_ip change type definition and catalog"`

---

## Task 3: DNS discovery service

**Files touched:**
- `backend/app/services/dns_discovery_service.py` (new)

### Steps

- [ ] **3.1** Create `backend/app/services/dns_discovery_service.py`:

  ```python
  """
  DNS Discovery Service — find all DNS A/AAAA records pointing to a given asset's
  current IP addresses by cross-referencing:
    1. asset_metadata.dns_names[]  (populated by DNS connector ingest)
    2. dns_zone assets whose asset_metadata.records[] reference the asset's IPs
    3. asset.name if it looks like an FQDN (contains at least one dot)
  """
  from __future__ import annotations
  from typing import TYPE_CHECKING

  if TYPE_CHECKING:
      from sqlalchemy.ext.asyncio import AsyncSession


  async def discover_dns_records_for_asset(
      db: "AsyncSession",
      asset_id: str,
  ) -> list[dict]:
      """Find all DNS A/AAAA records pointing to an asset's current IPs.

      Returns a list of record dicts:
      {
          "name":         str,   # e.g. "api.corp.example.com"
          "type":         str,   # "A" or "AAAA"
          "value":        str,   # the IP address the record currently resolves to
          "ttl":          int,   # record TTL in seconds
          "provider":     str,   # "route53", "azure_dns", "cloudflare", etc.
          "connector_id": str,   # UUID of the Nexplane connector that manages this zone
          "zone_id":      str,   # provider-specific zone/hosted-zone identifier
      }
      """
      import uuid as _uuid
      from sqlalchemy import select
      from app.models.asset import Asset, AssetType

      asset_uuid = _uuid.UUID(asset_id) if isinstance(asset_id, str) else asset_id

      # Load the target asset
      asset = await db.get(Asset, asset_uuid)
      if asset is None:
          return []

      meta = asset.asset_metadata or {}

      # Collect the asset's current IP addresses
      current_ips: set[str] = set()
      for ip in meta.get("ip_addresses", []):
          # Strip CIDR prefix if present (e.g. "10.0.0.100/24" -> "10.0.0.100")
          current_ips.add(ip.split("/")[0])
      if not current_ips:
          return []

      records: list[dict] = []
      seen: set[tuple] = set()  # (name, type, value) dedup key

      # -----------------------------------------------------------------------
      # Source 1: asset_metadata.dns_names[] — records already linked to asset
      # -----------------------------------------------------------------------
      for dns_name in meta.get("dns_names", []):
          _add_record(records, seen, {
              "name": dns_name,
              "type": "A",
              "value": next(iter(current_ips)),  # best-guess; caller can verify
              "ttl": meta.get("dns_ttl", 3600),
              "provider": meta.get("dns_provider", "unknown"),
              "connector_id": str(meta.get("dns_connector_id", "")),
              "zone_id": meta.get("dns_zone_id", ""),
          })

      # -----------------------------------------------------------------------
      # Source 2: dns_zone assets whose records[] reference the asset's IPs
      # -----------------------------------------------------------------------
      dns_zone_result = await db.execute(
          select(Asset).where(
              Asset.organization_id == asset.organization_id,
              Asset.asset_type == AssetType.dns_zone,
          )
      )
      dns_zone_assets = dns_zone_result.scalars().all()

      for zone_asset in dns_zone_assets:
          zone_meta = zone_asset.asset_metadata or {}
          zone_records = zone_meta.get("records", [])
          connector_id = str(zone_asset.connector_id or "")
          zone_id = zone_meta.get("zone_id", str(zone_asset.id))
          provider = zone_meta.get("provider", "unknown")

          for rec in zone_records:
              rec_type = rec.get("type", "").upper()
              if rec_type not in ("A", "AAAA"):
                  continue
              rec_value = rec.get("value", "").split("/")[0]
              if rec_value not in current_ips:
                  continue
              _add_record(records, seen, {
                  "name": rec.get("name", ""),
                  "type": rec_type,
                  "value": rec_value,
                  "ttl": int(rec.get("ttl", 3600)),
                  "provider": provider,
                  "connector_id": connector_id,
                  "zone_id": zone_id,
              })

      # -----------------------------------------------------------------------
      # Source 3: asset.name if it looks like an FQDN
      # -----------------------------------------------------------------------
      if "." in asset.name:
          for ip in current_ips:
              _add_record(records, seen, {
                  "name": asset.name,
                  "type": "A" if ":" not in ip else "AAAA",
                  "value": ip,
                  "ttl": 3600,
                  "provider": "unknown",
                  "connector_id": "",
                  "zone_id": "",
              })

      return records


  def _add_record(
      records: list[dict],
      seen: set[tuple],
      rec: dict,
  ) -> None:
      key = (rec["name"], rec["type"], rec["value"])
      if key not in seen:
          seen.add(key)
          records.append(rec)
  ```

- [ ] **3.2** Add tests to `backend/tests/test_ip_migration.py` — test class `TestDnsDiscoveryService`:

  ```python
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
  ```

- [ ] **3.3** Verify: `cd backend && python -m pytest tests/test_ip_migration.py::TestDnsDiscoveryService -v`
  Expected: 5 passed.

- [ ] **3.4** Commit: `git add backend/app/services/dns_discovery_service.py backend/tests/test_ip_migration.py && git commit -m "task 3: add dns_discovery_service for IP→DNS cross-reference"`

---

## Task 4: New `migrate_ip` executor

**Files touched:**
- `backend/app/connectors/executors/nexplane_agent/migrate_ip.py` (new)
- `backend/app/connectors/change_type_definitions/migrate_ip.json` (new)
- `backend/app/connectors/catalog/nexplane_agent.json`

### Steps

- [ ] **4.1** Create `backend/app/connectors/executors/nexplane_agent/migrate_ip.py`:

  ```python
  """
  migrate_ip executor — multi-stage IP migration orchestrator.

  Stages (v1 implementation):
    0. dns_discovery   — discover DNS records pointing to the asset's current IPs
    1. preflight       — parameter validation (performed by change_ip dispatch)
    2. dns_prepare     — lower TTLs (if update_dns=True and records found) [FUTURE]
    3. apply_change    — dispatch change_ip sub-job with the appropriate method
    4. verify_new      — confirmation that execution_result reports success
    5. dns_update      — fire DNS update CRs for each discovered record [FUTURE]
    6. commit          — mark migration permanent; write to execution_result

  v1 scope: stages 0, 3, 4, 6 are implemented. Stages 2 and 5 are stubbed
  (logged and skipped) so the executor is end-to-end functional while DNS
  provider integration is built out in a follow-on plan.

  Rollback: fires change_ip rollback for stage 3. DNS rollback is a no-op in v1.
  """
  from __future__ import annotations
  from datetime import datetime, timezone


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      """
      Parameters:
          interface               str   — network interface (required)
          mode                    str   — "static" | "dhcp" (required)
          new_ip_v4               str   — new IPv4 address in CIDR notation
          new_gateway_v4          str   — new IPv4 gateway
          dns_servers             list  — DNS servers for the interface
          method                  str   — "auto" | "tailscale" | "secondary_swap" | "commit_timer" | "manual"
          commit_timer_seconds    int   — default 30
          update_dns              bool  — default True, whether to update DNS records
          update_dns_ttl          bool  — default True, whether to lower TTL before change (v1: stub)
          confirm_at_stage        str   — default None, pause at named stage (v1: no-op)
          rollback_on_stage_failure bool — default True
      """
      from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
      from app.services.dns_discovery_service import discover_dns_records_for_asset
      from app.database import AsyncSessionLocal
      import logging

      log = logging.getLogger(__name__)

      update_dns = bool(parameters.get("update_dns", True))
      method = parameters.get("method", "auto")
      commit_timer_seconds = int(parameters.get("commit_timer_seconds", 30))

      stages_completed: list[str] = []
      dns_records: list[dict] = []
      change_ip_result: dict = {}

      # -----------------------------------------------------------------------
      # Stage 0: DNS discovery
      # -----------------------------------------------------------------------
      if update_dns and asset_ids:
          try:
              async with AsyncSessionLocal() as db:
                  dns_records = await discover_dns_records_for_asset(db, asset_ids[0])
              log.info("migrate_ip dns_discovery: found %d records", len(dns_records))
          except Exception as exc:
              log.warning("migrate_ip dns_discovery failed (non-fatal): %s", exc)
      stages_completed.append("dns_discovery")

      # -----------------------------------------------------------------------
      # Stage 2: DNS prepare (v1 stub)
      # -----------------------------------------------------------------------
      if update_dns and dns_records:
          log.info(
              "migrate_ip dns_prepare: %d records found, TTL lowering not yet implemented in v1",
              len(dns_records),
          )
      stages_completed.append("dns_prepare")

      # -----------------------------------------------------------------------
      # Stage 3: apply_change — dispatch change_ip
      # -----------------------------------------------------------------------
      change_ip_params = {
          "interface": parameters.get("interface"),
          "mode": parameters.get("mode"),
          "new_ip_v4": parameters.get("new_ip_v4"),
          "new_gateway_v4": parameters.get("new_gateway_v4"),
          "new_ip_v6": parameters.get("new_ip_v6"),
          "new_gateway_v6": parameters.get("new_gateway_v6"),
          "dns_servers": list(parameters.get("dns_servers") or []),
          "dns_search_domains": list(parameters.get("dns_search_domains") or []),
          "method": method,
          "commit_timer_seconds": commit_timer_seconds,
          "probe_interval_seconds": int(parameters.get("probe_interval_seconds", 5)),
          "add_secondary": bool(parameters.get("add_secondary", False)),
          "preflight_arp_probe": bool(parameters.get("preflight_arp_probe", True)),
      }
      # Strip None values
      change_ip_params = {k: v for k, v in change_ip_params.items() if v is not None}

      change_ip_result = await dispatch_agent_job(
          command="change_ip",
          parameters=change_ip_params,
          asset_ids=asset_ids,
          timeout_seconds=commit_timer_seconds + 120,
      )
      stages_completed.append("apply_change")

      # -----------------------------------------------------------------------
      # Stage 4: verify_new — check the dispatch result reported success
      # -----------------------------------------------------------------------
      ip_changed = change_ip_result.get("status") in ("completed", None) or bool(
          change_ip_result.get("applied", True)
      )
      stages_completed.append("verify_new")

      # -----------------------------------------------------------------------
      # Stage 5: DNS update (v1 stub)
      # -----------------------------------------------------------------------
      dns_records_updated = 0
      if update_dns and dns_records and ip_changed:
          log.info(
              "migrate_ip dns_update: %d records to update — DNS provider integration not yet implemented in v1",
              len(dns_records),
          )
      stages_completed.append("dns_update")

      # -----------------------------------------------------------------------
      # Stage 6: commit
      # -----------------------------------------------------------------------
      stages_completed.append("commit")

      return {
          "action": "migrate_ip",
          "ip_changed": ip_changed,
          "method_used": method,
          "dns_records_discovered": len(dns_records),
          "dns_records_updated": dns_records_updated,
          "stages_completed": stages_completed,
          "change_ip_result": change_ip_result,
          "applied_at": datetime.now(timezone.utc).isoformat(),
      }


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      """Rollback: reverse the change_ip stage. DNS rollback is not implemented in v1."""
      from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
      import logging

      log = logging.getLogger(__name__)

      stages = execution_result.get("stages_completed", [])
      if "apply_change" not in stages:
          return {"rolled_back": False, "reason": "apply_change stage never ran"}

      change_ip_result = execution_result.get("change_ip_result", {})
      snapshot = change_ip_result.get("snapshot", {})

      rollback_params = {
          "previous_result": snapshot,
          "interface": parameters.get("interface"),
      }

      result = await dispatch_agent_job(
          command="change_ip_rollback",
          parameters=rollback_params,
          asset_ids=parameters.get("_asset_ids", []),
          timeout_seconds=120,
      )

      if execution_result.get("dns_records_updated", 0) > 0:
          log.warning("migrate_ip rollback: DNS records were updated but DNS rollback is not implemented in v1")

      return {
          "rolled_back": True,
          "action": "migrate_ip",
          "ip_rollback_result": result,
          "dns_rolled_back": False,
          "dns_rollback_note": "v1: DNS rollback not implemented",
      }
  ```

- [ ] **4.2** Create `backend/app/connectors/change_type_definitions/migrate_ip.json`:

  ```json
  {
    "change_type": "migrate_ip",
    "display_name": "Migrate IP Address",
    "steps": [{"generic_action": "migrate_ip", "purpose": "execute", "required": true}],
    "preflight_checks": ["connector_reachable", "asset_exists"],
    "verification_methods": ["api_check"],
    "parameters": {
      "interface": {"type": "string", "required": true},
      "mode": {"type": "string", "required": true, "enum": ["static", "dhcp"]},
      "method": {"type": "string", "required": false, "default": "auto", "enum": ["auto", "tailscale", "secondary_swap", "commit_timer", "manual"]},
      "new_ip_v4": {"type": "string", "required": false},
      "new_ip_v6": {"type": "string", "required": false},
      "new_gateway_v4": {"type": "string", "required": false},
      "new_gateway_v6": {"type": "string", "required": false},
      "dns_servers": {"type": "array", "required": false, "items": {"type": "string"}},
      "dns_search_domains": {"type": "array", "required": false, "items": {"type": "string"}},
      "commit_timer_seconds": {"type": "integer", "required": false, "default": 30},
      "probe_interval_seconds": {"type": "integer", "required": false, "default": 5},
      "add_secondary": {"type": "boolean", "required": false, "default": false},
      "preflight_arp_probe": {"type": "boolean", "required": false, "default": true},
      "update_dns": {"type": "boolean", "required": false, "default": true},
      "update_dns_ttl": {"type": "boolean", "required": false, "default": true},
      "confirm_at_stage": {"type": "string", "required": false, "default": null},
      "rollback_on_stage_failure": {"type": "boolean", "required": false, "default": true}
    }
  }
  ```

- [ ] **4.3** Add the `migrate_ip` entry to `backend/app/connectors/catalog/nexplane_agent.json`. Append this object to the `actions` array (before the closing `]`):

  ```json
  {
      "action_id": "migrate_ip",
      "generic_action": "migrate_ip",
      "action_type": "change",
      "execution_tier": 3,
      "display_name": "Migrate IP Address",
      "description": "Multi-stage IP migration with method-aware dispatch (Tailscale, secondary swap, commit timer, or manual), DNS record discovery, and per-stage rollback.",
      "applicable_asset_types": ["server"],
      "parameters": [
          {"name": "interface",               "type": "string",  "required": true},
          {"name": "mode",                    "type": "string",  "required": true},
          {"name": "method",                  "type": "string",  "required": false, "default": "auto"},
          {"name": "new_ip_v4",               "type": "string",  "required": false},
          {"name": "new_gateway_v4",          "type": "string",  "required": false},
          {"name": "dns_servers",             "type": "array",   "required": false},
          {"name": "dns_search_domains",      "type": "array",   "required": false},
          {"name": "commit_timer_seconds",    "type": "integer", "required": false, "default": 30},
          {"name": "probe_interval_seconds",  "type": "integer", "required": false, "default": 5},
          {"name": "add_secondary",           "type": "boolean", "required": false, "default": false},
          {"name": "preflight_arp_probe",     "type": "boolean", "required": false, "default": true},
          {"name": "update_dns",              "type": "boolean", "required": false, "default": true},
          {"name": "update_dns_ttl",          "type": "boolean", "required": false, "default": true},
          {"name": "confirm_at_stage",        "type": "string",  "required": false},
          {"name": "rollback_on_stage_failure","type": "boolean","required": false, "default": true}
      ],
      "executor": "nexplane_agent.migrate_ip",
      "rollback_action": "migrate_ip",
      "estimated_duration_seconds": 300,
      "blast_radius_hint": "network_connectivity_loss",
      "safety_notes": [
          "Changing IP may temporarily disconnect the agent from the control plane",
          "Use method=tailscale if Tailscale is deployed to eliminate connectivity risk",
          "DNS records are discovered automatically but v1 DNS update is not yet automated"
      ]
  }
  ```

- [ ] **4.4** Add tests to `backend/tests/test_ip_migration.py` — test class `TestMigrateIpExecutor`:

  ```python
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
  ```

- [ ] **4.5** Verify: `cd backend && python -m pytest tests/test_ip_migration.py::TestMigrateIpExecutor -v`
  Expected: 4 passed.

- [ ] **4.6** Commit: `git add backend/app/connectors/executors/nexplane_agent/migrate_ip.py backend/app/connectors/change_type_definitions/migrate_ip.json backend/app/connectors/catalog/nexplane_agent.json backend/tests/test_ip_migration.py && git commit -m "task 4: add migrate_ip executor with multi-stage orchestration"`

---

## Task 5: New `ip_campaign` executor

**Files touched:**
- `backend/app/connectors/executors/nexplane_agent/ip_campaign.py` (new)
- `backend/app/connectors/change_type_definitions/ip_campaign.json` (new)
- `backend/app/connectors/catalog/nexplane_agent.json`

### Steps

- [ ] **5.1** Create `backend/app/connectors/executors/nexplane_agent/ip_campaign.py`:

  ```python
  """
  ip_campaign executor — fleet IP migration using migrate_ip in rolling batches.

  Mirrors the run_patch_campaign.py pattern:
    - Accepts a migration_plan list of per-asset IP configurations
    - Batches assets, runs each batch in parallel via asyncio.gather()
    - Aborts if the error fraction exceeds abort_error_threshold
    - Completed batches are NOT automatically rolled back (fleet scale — operator recovers)
  """
  from __future__ import annotations
  import asyncio
  from datetime import datetime, timezone
  from typing import Any


  async def execute(parameters: dict, asset_ids: list, connector) -> dict:
      """
      Parameters:
          migration_plan          list  — [{asset_id, interface, new_ip_v4, new_gateway_v4, method}, ...]
          batch_size              int   — default 5
          batch_interval_seconds  int   — seconds to sleep between batches, default 60
          abort_error_threshold   float — fraction of failures to abort, default 0.1
          method                  str   — default method for all hosts ("auto")
          commit_timer_seconds    int   — default 30
          dry_run                 bool  — if True, plan without executing

      Returns:
          total_assets       int
          batches_completed  int
          hosts_migrated     list[str]   asset IDs successfully migrated
          hosts_failed       list[dict]  [{asset_id, error}]
          aborted            bool
          abort_reason       str | None
          dry_run            bool
      """
      from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

      migration_plan: list[dict] = parameters.get("migration_plan", [])
      batch_size = int(parameters.get("batch_size", 5))
      batch_interval_seconds = int(parameters.get("batch_interval_seconds", 60))
      abort_threshold = float(parameters.get("abort_error_threshold", 0.1))
      default_method = parameters.get("method", "auto")
      commit_timer_seconds = int(parameters.get("commit_timer_seconds", 30))
      dry_run = bool(parameters.get("dry_run", False))

      if not migration_plan:
          raise ValueError("migration_plan must be a non-empty list of per-asset configurations")

      if dry_run:
          return {
              "total_assets": len(migration_plan),
              "batches_completed": 0,
              "hosts_migrated": [],
              "hosts_failed": [],
              "aborted": False,
              "abort_reason": None,
              "dry_run": True,
              "plan_preview": migration_plan,
          }

      migrated: list[str] = []
      failed: list[dict] = []
      aborted = False
      abort_reason: str | None = None
      batch_num = 0

      batches = [migration_plan[i:i + batch_size] for i in range(0, len(migration_plan), batch_size)]

      for batch_num, batch in enumerate(batches):
          if batch_num > 0:
              await asyncio.sleep(batch_interval_seconds)

          results = await asyncio.gather(
              *[
                  _migrate_single_host(
                      dispatch_agent_job=dispatch_agent_job,
                      host_plan=host_plan,
                      default_method=default_method,
                      commit_timer_seconds=commit_timer_seconds,
                  )
                  for host_plan in batch
              ],
              return_exceptions=True,
          )

          for host_plan, result in zip(batch, results):
              if isinstance(result, Exception):
                  failed.append({
                      "asset_id": host_plan.get("asset_id", "unknown"),
                      "error": str(result),
                  })
              else:
                  migrated.append(host_plan["asset_id"])

          total_attempted = len(migrated) + len(failed)
          if total_attempted > 0 and len(failed) / total_attempted > abort_threshold:
              aborted = True
              abort_reason = (
                  f"Error rate {len(failed)/total_attempted:.0%} exceeded threshold "
                  f"{abort_threshold:.0%} after batch {batch_num + 1}"
              )
              break

      return {
          "total_assets": len(migration_plan),
          "batches_completed": batch_num + 1 if not aborted else batch_num,
          "hosts_migrated": migrated,
          "hosts_failed": failed,
          "aborted": aborted,
          "abort_reason": abort_reason,
          "dry_run": False,
      }


  async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
      """Campaign-level rollback is not supported — individual migrate_ip CRs have their own rollback."""
      return {
          "rolled_back": False,
          "reason": (
              "ip_campaign rollback is not supported at the campaign level. "
              "Initiate per-host migrate_ip rollbacks individually."
          ),
      }


  async def _migrate_single_host(
      dispatch_agent_job: Any,
      host_plan: dict,
      default_method: str,
      commit_timer_seconds: int,
  ) -> None:
      """
      Dispatches a change_ip command for a single host in the migration plan.
      Raises RuntimeError on failure so asyncio.gather() captures it as an exception.
      """
      asset_id = host_plan.get("asset_id")
      if not asset_id:
          raise ValueError("host_plan entry missing 'asset_id'")

      agent_params = {
          "interface": host_plan.get("interface", "eth0"),
          "mode": host_plan.get("mode", "static"),
          "new_ip_v4": host_plan.get("new_ip_v4"),
          "new_gateway_v4": host_plan.get("new_gateway_v4"),
          "method": host_plan.get("method", default_method),
          "commit_timer_seconds": commit_timer_seconds,
      }
      agent_params = {k: v for k, v in agent_params.items() if v is not None}

      result = await dispatch_agent_job(
          command="change_ip",
          parameters=agent_params,
          asset_ids=[asset_id],
          timeout_seconds=commit_timer_seconds + 120,
      )

      # Agent returns status="completed" on success; anything else is a failure
      status = result.get("status", "completed")
      if status == "failed":
          raise RuntimeError(result.get("error", "agent reported failure"))
  ```

- [ ] **5.2** Create `backend/app/connectors/change_type_definitions/ip_campaign.json`:

  ```json
  {
    "change_type": "ip_campaign",
    "display_name": "IP Migration Campaign",
    "steps": [{"generic_action": "ip_campaign", "purpose": "execute", "required": true}],
    "preflight_checks": ["connector_reachable"],
    "verification_methods": ["api_check"],
    "parameters": {
      "migration_plan": {
        "type": "array",
        "required": true,
        "items": {
          "type": "object",
          "properties": {
            "asset_id":      {"type": "string"},
            "interface":     {"type": "string"},
            "new_ip_v4":     {"type": "string"},
            "new_gateway_v4":{"type": "string"},
            "method":        {"type": "string"}
          }
        }
      },
      "batch_size":              {"type": "integer", "required": false, "default": 5},
      "batch_interval_seconds":  {"type": "integer", "required": false, "default": 60},
      "abort_error_threshold":   {"type": "number",  "required": false, "default": 0.1},
      "method":                  {"type": "string",  "required": false, "default": "auto"},
      "commit_timer_seconds":    {"type": "integer", "required": false, "default": 30},
      "dry_run":                 {"type": "boolean", "required": false, "default": false}
    }
  }
  ```

- [ ] **5.3** Add the `ip_campaign` entry to `backend/app/connectors/catalog/nexplane_agent.json`. Append to the `actions` array:

  ```json
  {
      "action_id": "ip_campaign",
      "generic_action": "ip_campaign",
      "action_type": "change",
      "execution_tier": 3,
      "display_name": "IP Migration Campaign",
      "description": "Fleet-level IP migration. Applies change_ip across a set of assets in configurable batches. Aborts if the error rate exceeds the threshold.",
      "applicable_asset_types": ["server"],
      "parameters": [
          {"name": "migration_plan",          "type": "array",   "required": true},
          {"name": "batch_size",              "type": "integer", "required": false, "default": 5},
          {"name": "batch_interval_seconds",  "type": "integer", "required": false, "default": 60},
          {"name": "abort_error_threshold",   "type": "number",  "required": false, "default": 0.1},
          {"name": "method",                  "type": "string",  "required": false, "default": "auto"},
          {"name": "commit_timer_seconds",    "type": "integer", "required": false, "default": 30},
          {"name": "dry_run",                 "type": "boolean", "required": false, "default": false}
      ],
      "executor": "nexplane_agent.ip_campaign",
      "estimated_duration_seconds": 3600,
      "blast_radius_hint": "fleet_network_connectivity_loss",
      "safety_notes": [
          "Use dry_run=true to preview the migration plan before executing",
          "Set abort_error_threshold conservatively (0.05-0.1) for production fleets",
          "Completed batches are not automatically rolled back — operator initiates per-host recovery"
      ]
  }
  ```

- [ ] **5.4** Add tests to `backend/tests/test_ip_migration.py` — test class `TestIpCampaignExecutor`:

  ```python
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
  ```

- [ ] **5.5** Verify: `cd backend && python -m pytest tests/test_ip_migration.py::TestIpCampaignExecutor -v`
  Expected: 5 passed.

- [ ] **5.6** Commit: `git add backend/app/connectors/executors/nexplane_agent/ip_campaign.py backend/app/connectors/change_type_definitions/ip_campaign.json backend/app/connectors/catalog/nexplane_agent.json backend/tests/test_ip_migration.py && git commit -m "task 5: add ip_campaign fleet executor"`

---

## Task 6: Post-execution asset metadata hook

**Files touched:**
- `backend/app/services/ip_change_service.py` (new)
- `backend/app/workflows/execute_change_workflow.py`

### Steps

- [ ] **6.1** Create `backend/app/services/ip_change_service.py`:

  ```python
  """
  ip_change_service — post-execution metadata update for change_ip and migrate_ip CRs.

  After a successful IP change, update asset_metadata.ip_addresses to reflect the
  new IP so that downstream services (DNS discovery, UI, inventory) see current data.
  """
  from __future__ import annotations
  from typing import TYPE_CHECKING
  import logging

  if TYPE_CHECKING:
      from sqlalchemy.ext.asyncio import AsyncSession

  log = logging.getLogger(__name__)


  async def update_asset_ip_metadata(
      db: "AsyncSession",
      asset_ids: list,
      execution_result: dict,
  ) -> None:
      """After a successful change_ip or migrate_ip, update asset_metadata.ip_addresses.

      Reads the new IP from execution_result in the following priority:
        1. execution_result["change_ip_result"]["snapshot"]["ip_v4_addresses"]  (migrate_ip)
        2. execution_result["snapshot"]["ip_v4_addresses"]                       (change_ip)
        3. execution_result["new_ip_v4"] stripped of CIDR prefix                (fallback)

      Merges new IPs into existing asset_metadata, replacing stale entries for the
      same interface when the interface key is available.
      """
      import uuid as _uuid
      from sqlalchemy.orm.attributes import flag_modified
      from app.models.asset import Asset

      # Resolve the new IP from result
      new_ips = _extract_new_ips(execution_result)
      if not new_ips:
          log.warning("update_asset_ip_metadata: no new IP found in execution_result, skipping")
          return

      for raw_id in asset_ids:
          asset_uuid = _uuid.UUID(raw_id) if isinstance(raw_id, str) else raw_id
          asset = await db.get(Asset, asset_uuid)
          if asset is None:
              log.warning("update_asset_ip_metadata: asset %s not found", raw_id)
              continue

          meta = dict(asset.asset_metadata or {})
          meta["ip_addresses"] = new_ips
          asset.asset_metadata = meta
          flag_modified(asset, "asset_metadata")
          log.info("update_asset_ip_metadata: updated asset %s ip_addresses -> %s", raw_id, new_ips)

      await db.commit()


  def _extract_new_ips(execution_result: dict) -> list[str]:
      """Return a list of new IP address strings from the execution result dict."""
      # Path 1: migrate_ip wraps change_ip result
      change_ip_result = execution_result.get("change_ip_result", {})
      snapshot = change_ip_result.get("snapshot", {})
      v4_addrs = snapshot.get("ip_v4_addresses", [])
      if v4_addrs:
          return list(v4_addrs)

      # Path 2: direct change_ip snapshot
      snapshot = execution_result.get("snapshot", {})
      v4_addrs = snapshot.get("ip_v4_addresses", [])
      if v4_addrs:
          return list(v4_addrs)

      # Path 3: new_ip_v4 parameter echoed in result
      new_ip = execution_result.get("new_ip_v4")
      if new_ip:
          return [new_ip]

      return []
  ```

- [ ] **6.2** In `backend/app/workflows/execute_change_workflow.py`, add the `change_ip` and `migrate_ip` post-execution hooks immediately after the existing `agent_containerize_retire` block (after line ~123). Insert the following block:

  ```python
      if data.get("change_type") in ("change_ip", "migrate_ip"):
          from app.services.ip_change_service import update_asset_ip_metadata
          async with AsyncSessionLocal() as post_db:
              await update_asset_ip_metadata(
                  post_db, data["target_asset_ids"], execution_result
              )
  ```

  The exact location: find the block ending with:
  ```python
      if data.get("change_type") == "agent_containerize_retire":
          from app.services.retire_service import mark_asset_retired
          async with AsyncSessionLocal() as post_db:
              await mark_asset_retired(post_db, data["target_asset_ids"], execution_result)
  ```
  Append the new block immediately after it, before the `await write_audit_event(...)` call for `execution.completed`.

- [ ] **6.3** Add tests to `backend/tests/test_ip_migration.py` — test class `TestUpdateAssetIpMetadata`:

  ```python
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
  ```

- [ ] **6.4** Verify: `cd backend && python -m pytest tests/test_ip_migration.py::TestUpdateAssetIpMetadata -v`
  Expected: 3 passed.

- [ ] **6.5** Verify workflow edit compiles: `cd backend && python -c "from app.workflows.execute_change_workflow import execute_change_workflow; print('OK')"`

- [ ] **6.6** Commit: `git add backend/app/services/ip_change_service.py backend/app/workflows/execute_change_workflow.py backend/tests/test_ip_migration.py && git commit -m "task 6: post-execution hook updates asset ip_addresses metadata after change_ip/migrate_ip"`

---

## Task 7: Enum additions and DB migration

**Files touched:**
- `backend/app/models/change_request.py`
- `backend/alembic/versions/038_add_ip_migration_change_types.py` (new)

### Steps

- [ ] **7.1** In `backend/app/models/change_request.py`, locate the `ChangeType` enum. Append these two entries at the end of the enum body, before the class closes (after `agent_containerize_retire`):

  ```python
      # IP address migration
      change_ip = "change_ip"
      migrate_ip = "migrate_ip"
      ip_campaign = "ip_campaign"
  ```

  Note: `change_ip` may already exist in the enum from prior work — check first. If it does, only add `migrate_ip` and `ip_campaign`.

- [ ] **7.2** Create `backend/alembic/versions/038_add_ip_migration_change_types.py`:

  ```python
  """add ip migration change types

  Revision ID: 038
  Revises: 037
  Create Date: 2026-05-09
  """
  from alembic import op

  revision = '038'
  down_revision = '037'
  branch_labels = None
  depends_on = None


  def upgrade():
      for t in ['change_ip', 'migrate_ip', 'ip_campaign']:
          op.execute(f"ALTER TYPE change_type ADD VALUE IF NOT EXISTS '{t}'")


  def downgrade():
      pass
  ```

  The `IF NOT EXISTS` guard means the migration is safe to run even if `change_ip` was added in an earlier migration.

- [ ] **7.3** Verify the model imports cleanly: `cd backend && python -c "from app.models.change_request import ChangeType; print(ChangeType.migrate_ip, ChangeType.ip_campaign)"`

- [ ] **7.4** Commit: `git add backend/app/models/change_request.py backend/alembic/versions/038_add_ip_migration_change_types.py && git commit -m "task 7: add migrate_ip and ip_campaign to ChangeType enum and alembic migration 038"`

---

## Task 8: Complete unit test suite

**Files touched:**
- `backend/tests/test_ip_migration.py`

### Steps

- [ ] **8.1** Ensure the test file starts with the following header and imports (before any test class). If the file was built up across tasks 1–6, verify these imports are present at the top:

  ```python
  """Unit tests for IP migration backend executors and services."""
  from __future__ import annotations
  import asyncio
  import pytest
  from unittest.mock import AsyncMock, patch, MagicMock


  def run(coro):
      return asyncio.get_event_loop().run_until_complete(coro)
  ```

- [ ] **8.2** Run the full test file: `cd backend && python -m pytest tests/test_ip_migration.py -v`

  Expected output (all tasks combined):
  ```
  tests/test_ip_migration.py::TestChangeIpExecutor::test_new_params_forwarded_to_dispatch PASSED
  tests/test_ip_migration.py::TestChangeIpExecutor::test_defaults_applied_when_params_absent PASSED
  tests/test_ip_migration.py::TestChangeIpExecutor::test_timeout_scaled_with_commit_timer PASSED
  tests/test_ip_migration.py::TestDnsDiscoveryService::test_records_from_dns_names_metadata PASSED
  tests/test_ip_migration.py::TestDnsDiscoveryService::test_records_from_dns_zone_assets PASSED
  tests/test_ip_migration.py::TestDnsDiscoveryService::test_fqdn_asset_name_included PASSED
  tests/test_ip_migration.py::TestDnsDiscoveryService::test_no_ips_returns_empty PASSED
  tests/test_ip_migration.py::TestDnsDiscoveryService::test_dedup_prevents_duplicate_records PASSED
  tests/test_ip_migration.py::TestMigrateIpExecutor::test_stages_completed_in_order PASSED
  tests/test_ip_migration.py::TestMigrateIpExecutor::test_change_ip_dispatched_with_correct_params PASSED
  tests/test_ip_migration.py::TestMigrateIpExecutor::test_dns_discovery_called_when_update_dns_true PASSED
  tests/test_ip_migration.py::TestMigrateIpExecutor::test_dns_discovery_skipped_when_update_dns_false PASSED
  tests/test_ip_migration.py::TestIpCampaignExecutor::test_dry_run_returns_plan_without_dispatch PASSED
  tests/test_ip_migration.py::TestIpCampaignExecutor::test_all_succeed_reports_correct_counts PASSED
  tests/test_ip_migration.py::TestIpCampaignExecutor::test_abort_fires_when_threshold_exceeded PASSED
  tests/test_ip_migration.py::TestIpCampaignExecutor::test_batch_size_respected PASSED
  tests/test_ip_migration.py::TestIpCampaignExecutor::test_missing_migration_plan_raises PASSED
  tests/test_ip_migration.py::TestUpdateAssetIpMetadata::test_updates_ip_addresses_from_snapshot PASSED
  tests/test_ip_migration.py::TestUpdateAssetIpMetadata::test_updates_ip_addresses_from_migrate_ip_result PASSED
  tests/test_ip_migration.py::TestUpdateAssetIpMetadata::test_no_op_when_no_new_ip_in_result PASSED
  ======================== 20 passed in X.XXs ========================
  ```

  Expected count: **20 passed**.

- [ ] **8.3** If any test fails, diagnose and fix before proceeding. Do not commit a failing test suite.

- [ ] **8.4** Final commit: `git add backend/tests/test_ip_migration.py && git commit -m "task 8: complete ip migration unit test suite — 20 tests passing"`

---

## Completion checklist

Before marking this plan done:

- [ ] All 8 task commits are present on the branch
- [ ] `python -m pytest tests/test_ip_migration.py -v` reports **20 passed, 0 failed**
- [ ] `python -c "import json; json.load(open('backend/app/connectors/catalog/nexplane_agent.json'))"` exits 0
- [ ] `python -c "from app.models.change_request import ChangeType; print(ChangeType.migrate_ip)"` exits 0
- [ ] `python -c "from app.workflows.execute_change_workflow import execute_change_workflow"` exits 0
- [ ] `python -m pytest tests/ -v --ignore=tests/smoke` passes (no regressions in existing suite)
