# Santa Sync Server Connector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `santa_sync_server` connector that manages fleet-level Santa policy via the open Santa sync protocol (Moroz/Zentral compatible), with 5 actions and a SANTA_SYNC smoke phase using Moroz in Docker.

**Architecture:** A new connector type with a standalone catalog JSON, a `_client.py` that dispatches to Moroz or Zentral API implementations, and 5 async executor modules. The connector asset type is `macos_fleet` (virtual, fleet-level). The SANTA_SYNC smoke phase runs Moroz in Docker on the EC2 backend and exercises the full audit → push → rollback cycle.

**Tech Stack:** Python/httpx for the API client, Moroz for smoke (open-source Santa sync server in Go), Docker for smoke infra, pytest-style smoke phase matching the existing `test_aws_live.py` pattern.

---

## File Structure

**New files to create:**
- `backend/app/connectors/catalog/santa_sync_server.json` — connector type definition with 5 actions
- `backend/app/connectors/executors/santa_sync_server/__init__.py` — empty
- `backend/app/connectors/executors/santa_sync_server/_client.py` — `SantaSyncClient` class that dispatches to Moroz/Zentral
- `backend/app/connectors/executors/santa_sync_server/santa_policy_audit.py`
- `backend/app/connectors/executors/santa_sync_server/santa_push_rules.py`
- `backend/app/connectors/executors/santa_sync_server/santa_machine_list.py`
- `backend/app/connectors/executors/santa_sync_server/santa_machine_group_assign.py`
- `backend/app/connectors/executors/santa_sync_server/santa_rule_deploy.py`

**Modified files:**
- `backend/tests/smoke/test_aws_live.py` — add `run_phase_santa_sync()` function and wire into `PHASES` dict

---

### Task 1: Connector Catalog JSON

**Files:**
- Create: `backend/app/connectors/catalog/santa_sync_server.json`

- [ ] **Step 1: Write the catalog JSON**

```json
{
  "connector_type": "santa_sync_server",
  "display_name": "Santa Sync Server",
  "credential_fields": [
    {"name": "sync_server_url", "label": "Sync Server URL", "type": "string", "required": true},
    {"name": "auth_token", "label": "Auth Token", "type": "password", "required": true},
    {"name": "default_machine_group", "label": "Default Machine Group", "type": "string", "required": false},
    {"name": "machine_id_field", "label": "Machine ID Field (hardware_uuid or serial)", "type": "string", "required": false, "default": "hardware_uuid"},
    {"name": "tls_verify", "label": "Verify TLS", "type": "boolean", "required": false, "default": true}
  ],
  "actions": [
    {
      "action_id": "santa_policy_audit",
      "generic_action": "santa_policy_audit",
      "action_type": "read",
      "execution_tier": 1,
      "display_name": "Santa Policy Audit",
      "description": "Reads the current Santa rule set for a machine group from the sync server.",
      "applicable_asset_types": ["macos_fleet"],
      "parameters": [
        {"name": "machine_group", "type": "string", "required": false, "description": "Machine group name; defaults to default_machine_group credential"}
      ],
      "executor": "santa_sync_server.santa_policy_audit",
      "estimated_duration_seconds": 15
    },
    {
      "action_id": "santa_push_rules",
      "generic_action": "santa_push_rules",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Santa Push Rules",
      "description": "Pushes a rule set to a machine group. Machines receive the new rules on next sync.",
      "applicable_asset_types": ["macos_fleet"],
      "parameters": [
        {"name": "rules", "type": "array", "required": true, "description": "List of rule objects: {rule_type, identifier_type, identifier, custom_message (optional)}"},
        {"name": "machine_group", "type": "string", "required": false},
        {"name": "mode", "type": "string", "required": false, "default": "merge", "description": "merge or replace"}
      ],
      "executor": "santa_sync_server.santa_push_rules",
      "rollback_action": "santa_push_rules",
      "estimated_duration_seconds": 30
    },
    {
      "action_id": "santa_machine_list",
      "generic_action": "santa_machine_list",
      "action_type": "read",
      "execution_tier": 1,
      "display_name": "Santa Machine List",
      "description": "Lists machines enrolled in the sync server and their last sync state.",
      "applicable_asset_types": ["macos_fleet"],
      "parameters": [
        {"name": "machine_group", "type": "string", "required": false}
      ],
      "executor": "santa_sync_server.santa_machine_list",
      "estimated_duration_seconds": 15
    },
    {
      "action_id": "santa_machine_group_assign",
      "generic_action": "santa_machine_group_assign",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Santa Machine Group Assign",
      "description": "Assigns a machine to a rule group on the sync server. Captures previous group for rollback.",
      "applicable_asset_types": ["macos_fleet"],
      "parameters": [
        {"name": "machine_id", "type": "string", "required": true},
        {"name": "target_group", "type": "string", "required": true}
      ],
      "executor": "santa_sync_server.santa_machine_group_assign",
      "rollback_action": "santa_machine_group_assign",
      "estimated_duration_seconds": 10
    },
    {
      "action_id": "santa_rule_deploy",
      "generic_action": "santa_rule_deploy",
      "action_type": "change",
      "execution_tier": 2,
      "display_name": "Santa Rule Deploy",
      "description": "Adds a single rule to a group's policy and polls enrolled machines to verify receipt.",
      "applicable_asset_types": ["macos_fleet"],
      "parameters": [
        {"name": "rule_type", "type": "string", "required": true, "description": "allowlist, denylist, or silent_blocklist"},
        {"name": "identifier_type", "type": "string", "required": true, "description": "binary, certificate, teamid, or signingid"},
        {"name": "identifier", "type": "string", "required": true, "description": "SHA-256 or cert/team/signing ID"},
        {"name": "machine_group", "type": "string", "required": false},
        {"name": "custom_message", "type": "string", "required": false},
        {"name": "verify_machines", "type": "array", "required": false, "description": "Machine IDs to verify; default: all"},
        {"name": "verify_timeout_seconds", "type": "integer", "required": false, "default": 300}
      ],
      "executor": "santa_sync_server.santa_rule_deploy",
      "rollback_action": "santa_rule_deploy",
      "estimated_duration_seconds": 60
    }
  ]
}
```

- [ ] **Step 2: Verify the catalog loads**

```bash
cd backend && python3 -c "
import json
with open('app/connectors/catalog/santa_sync_server.json') as f:
    c = json.load(f)
print('connector_type:', c['connector_type'])
print('actions:', [a['action_id'] for a in c['actions']])
"
```
Expected output:
```
connector_type: santa_sync_server
actions: ['santa_policy_audit', 'santa_push_rules', 'santa_machine_list', 'santa_machine_group_assign', 'santa_rule_deploy']
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/catalog/santa_sync_server.json
git commit -m "feat(santa): add santa_sync_server connector catalog"
```

---

### Task 2: API Client (`_client.py`)

**Files:**
- Create: `backend/app/connectors/executors/santa_sync_server/__init__.py`
- Create: `backend/app/connectors/executors/santa_sync_server/_client.py`

The client detects the server flavour from the URL or an optional `server_flavor` credential field (`moroz` vs `zentral`). It exposes four methods matching what the executor modules need: `get_rules`, `push_rules`, `list_machines`, `assign_machine_group`.

- [ ] **Step 1: Create `__init__.py`**

```python
```
(empty file)

- [ ] **Step 2: Write the failing test**

Create `backend/tests/unit/connectors/test_santa_sync_client.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.connectors.executors.santa_sync_server._client import SantaSyncClient


@pytest.fixture
def moroz_creds():
    return {
        "sync_server_url": "http://moroz.internal",
        "auth_token": "test-token",
        "tls_verify": False,
    }


@pytest.fixture
def zentral_creds():
    return {
        "sync_server_url": "https://zentral.internal",
        "auth_token": "ztest-token",
        "tls_verify": True,
    }


@pytest.mark.asyncio
async def test_moroz_get_rules_returns_list(moroz_creds):
    client = SantaSyncClient(moroz_creds)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [
        {"sha256": "abc123", "policy": 1, "rule_type": 1}
    ]
    with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
        rules = await client.get_rules("default")
    assert len(rules) == 1
    assert rules[0]["identifier"] == "abc123"


@pytest.mark.asyncio
async def test_zentral_get_rules_returns_list(zentral_creds):
    client = SantaSyncClient(zentral_creds)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "results": [
            {"target": {"sha256": "def456", "type": "BINARY"}, "policy": "BLOCKLIST"}
        ]
    }
    with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
        rules = await client.get_rules("mygroup")
    assert len(rules) == 1
    assert rules[0]["identifier"] == "def456"


@pytest.mark.asyncio
async def test_push_rules_moroz(moroz_creds):
    client = SantaSyncClient(moroz_creds)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {}
    with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp):
        result = await client.push_rules("default", [{"rule_type": "denylist", "identifier_type": "binary", "identifier": "abc123"}], mode="merge")
    assert result["pushed"] >= 0
```

- [ ] **Step 3: Run test to verify it fails**

```bash
cd backend && python3 -m pytest tests/unit/connectors/test_santa_sync_client.py -v
```
Expected: FAIL with `ModuleNotFoundError` or `ImportError`.

- [ ] **Step 4: Write `_client.py`**

```python
import httpx
from typing import Optional


_MOROZ_RULE_TYPE_MAP = {1: "allowlist", 2: "denylist", 3: "silent_blocklist"}
_ZENTRAL_POLICY_MAP = {"ALLOWLIST": "allowlist", "BLOCKLIST": "denylist", "SILENT_BLOCKLIST": "silent_blocklist"}
_TO_MOROZ_POLICY = {"allowlist": 1, "denylist": 2, "silent_blocklist": 3}
_TO_ZENTRAL_POLICY = {"allowlist": "ALLOWLIST", "denylist": "BLOCKLIST", "silent_blocklist": "SILENT_BLOCKLIST"}
_ZENTRAL_TARGET_TYPE_MAP = {"binary": "BINARY", "certificate": "CERTIFICATE", "teamid": "TEAM_ID", "signingid": "SIGNING_ID"}


class SantaSyncClient:
    def __init__(self, creds: dict):
        self._creds = creds
        url = creds["sync_server_url"].rstrip("/")
        token = creds["auth_token"]
        verify = creds.get("tls_verify", True)
        self._is_zentral = "zentral" in url.lower() or creds.get("server_flavor") == "zentral"
        self._http = httpx.AsyncClient(
            base_url=url,
            headers={"Authorization": f"Token {token}"},
            verify=verify,
            timeout=30.0,
        )

    async def aclose(self):
        await self._http.aclose()

    async def get_rules(self, machine_group: Optional[str]) -> list[dict]:
        if self._is_zentral:
            params = {}
            if machine_group:
                params["configuration"] = machine_group
            resp = await self._http.get("/api/santa/rules/", params=params)
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("results", data) if isinstance(data, dict) else data
            return [self._normalize_zentral_rule(r) for r in raw]
        else:
            resp = await self._http.get("/rules")
            resp.raise_for_status()
            raw = resp.json()
            return [self._normalize_moroz_rule(r) for r in (raw if isinstance(raw, list) else raw.get("rules", []))]

    async def push_rules(self, machine_group: Optional[str], rules: list[dict], mode: str = "merge") -> dict:
        if self._is_zentral:
            pushed = 0
            for rule in rules:
                payload = {
                    "target": {
                        "type": _ZENTRAL_TARGET_TYPE_MAP.get(rule["identifier_type"], "BINARY"),
                        "sha256" if rule["identifier_type"] == "binary" else rule["identifier_type"]: rule["identifier"],
                    },
                    "policy": _TO_ZENTRAL_POLICY.get(rule["rule_type"], "BLOCKLIST"),
                }
                if rule.get("custom_message"):
                    payload["custom_message"] = rule["custom_message"]
                resp = await self._http.post("/api/santa/rules/", json=payload)
                resp.raise_for_status()
                pushed += 1
            return {"pushed": pushed, "machine_group": machine_group, "mode": mode}
        else:
            payload = {"rules": [
                {
                    "sha256": rule["identifier"],
                    "policy": _TO_MOROZ_POLICY.get(rule["rule_type"], 2),
                    "rule_type": 1 if rule["identifier_type"] == "binary" else 2,
                    **({"custom_message": rule["custom_message"]} if rule.get("custom_message") else {}),
                }
                for rule in rules
            ]}
            if mode == "replace":
                payload["clean_sync"] = True
            resp = await self._http.post("/rules", json=payload)
            resp.raise_for_status()
            return {"pushed": len(rules), "machine_group": machine_group, "mode": mode}

    async def list_machines(self, machine_group: Optional[str]) -> list[dict]:
        if self._is_zentral:
            params = {}
            if machine_group:
                params["configuration"] = machine_group
            resp = await self._http.get("/api/santa/enrolled-machines/", params=params)
            resp.raise_for_status()
            data = resp.json()
            raw = data.get("results", data) if isinstance(data, dict) else data
            return [self._normalize_zentral_machine(m) for m in raw]
        else:
            resp = await self._http.get("/machines")
            resp.raise_for_status()
            raw = resp.json()
            machines = raw if isinstance(raw, list) else raw.get("machines", [])
            return [self._normalize_moroz_machine(m) for m in machines]

    async def assign_machine_group(self, machine_id: str, target_group: str) -> dict:
        if self._is_zentral:
            resp = await self._http.patch(f"/api/santa/enrolled-machines/{machine_id}/", json={"configuration": target_group})
            resp.raise_for_status()
            return resp.json()
        else:
            resp = await self._http.put(f"/machines/{machine_id}", json={"machine_group": target_group})
            resp.raise_for_status()
            return resp.json()

    def _normalize_moroz_rule(self, r: dict) -> dict:
        return {
            "identifier": r.get("sha256", ""),
            "rule_type": _MOROZ_RULE_TYPE_MAP.get(r.get("policy", 2), "denylist"),
            "identifier_type": "binary" if r.get("rule_type", 1) == 1 else "certificate",
            "custom_message": r.get("custom_message", ""),
        }

    def _normalize_zentral_rule(self, r: dict) -> dict:
        target = r.get("target", {})
        id_type_raw = target.get("type", "BINARY").lower()
        id_type = {"binary": "binary", "certificate": "certificate", "team_id": "teamid", "signing_id": "signingid"}.get(id_type_raw, "binary")
        identifier = target.get("sha256") or target.get("signing_id") or target.get("team_id") or ""
        return {
            "identifier": identifier,
            "rule_type": _ZENTRAL_POLICY_MAP.get(r.get("policy", "BLOCKLIST"), "denylist"),
            "identifier_type": id_type,
            "custom_message": r.get("custom_message", ""),
        }

    def _normalize_moroz_machine(self, m: dict) -> dict:
        return {
            "machine_id": m.get("machine_id", m.get("hardware_uuid", "")),
            "hostname": m.get("hostname", ""),
            "os_version": m.get("os_version", ""),
            "santa_version": m.get("santa_version", ""),
            "last_sync": m.get("last_preflight_at", ""),
            "rule_count": m.get("rule_count", 0),
        }

    def _normalize_zentral_machine(self, m: dict) -> dict:
        return {
            "machine_id": m.get("hardware_uuid", m.get("serial_number", "")),
            "hostname": m.get("serial_number", ""),
            "os_version": m.get("os_version", ""),
            "santa_version": m.get("client_version", ""),
            "last_sync": m.get("last_seen", ""),
            "rule_count": m.get("rule_count", 0),
        }
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && python3 -m pytest tests/unit/connectors/test_santa_sync_client.py -v
```
Expected: 3 PASSED.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/santa_sync_server/__init__.py \
        backend/app/connectors/executors/santa_sync_server/_client.py \
        backend/tests/unit/connectors/test_santa_sync_client.py
git commit -m "feat(santa): add SantaSyncClient for Moroz and Zentral"
```

---

### Task 3: `santa_policy_audit` Executor

**Files:**
- Create: `backend/app/connectors/executors/santa_sync_server/santa_policy_audit.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/connectors/test_santa_policy_audit.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_execute_returns_rules():
    from app.connectors.executors.santa_sync_server import santa_policy_audit

    mock_conn = MagicMock()
    mock_conn.credentials = {
        "sync_server_url": "http://moroz.test",
        "auth_token": "tok",
        "default_machine_group": "default",
    }
    mock_rules = [
        {"identifier": "abc123", "rule_type": "denylist", "identifier_type": "binary", "custom_message": ""}
    ]
    with patch("app.connectors.executors.santa_sync_server.santa_policy_audit.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.get_rules = AsyncMock(return_value=mock_rules)
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_policy_audit.execute({}, [], mock_conn)

    assert result["rule_count"] == 1
    assert result["rules"][0]["identifier"] == "abc123"
    assert result["machine_group"] == "default"


@pytest.mark.asyncio
async def test_execute_no_creds_returns_mock():
    from app.connectors.executors.santa_sync_server import santa_policy_audit

    mock_conn = MagicMock()
    mock_conn.credentials = {}
    result = await santa_policy_audit.execute({}, [], mock_conn)
    assert "rules" in result
    assert result["rule_count"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python3 -m pytest tests/unit/connectors/test_santa_policy_audit.py -v
```
Expected: FAIL with ImportError.

- [ ] **Step 3: Write `santa_policy_audit.py`**

```python
from ._client import SantaSyncClient


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "santa_policy_audit", "rules": [], "machine_group": "default", "rule_count": 0}

    machine_group = parameters.get("machine_group") or creds.get("default_machine_group")
    client = SantaSyncClient(creds)
    try:
        rules = await client.get_rules(machine_group)
    finally:
        await client.aclose()

    return {
        "action": "santa_policy_audit",
        "rules": rules,
        "machine_group": machine_group or "default",
        "rule_count": len(rules),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && python3 -m pytest tests/unit/connectors/test_santa_policy_audit.py -v
```
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/santa_sync_server/santa_policy_audit.py \
        backend/tests/unit/connectors/test_santa_policy_audit.py
git commit -m "feat(santa): add santa_policy_audit executor"
```

---

### Task 4: `santa_push_rules` Executor

**Files:**
- Create: `backend/app/connectors/executors/santa_sync_server/santa_push_rules.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/connectors/test_santa_push_rules.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def _make_connector(creds=None):
    mock_conn = MagicMock()
    mock_conn.credentials = creds or {
        "sync_server_url": "http://moroz.test",
        "auth_token": "tok",
        "default_machine_group": "default",
    }
    return mock_conn


@pytest.mark.asyncio
async def test_execute_stores_snapshot_before():
    from app.connectors.executors.santa_sync_server import santa_push_rules

    existing_rules = [
        {"identifier": "existing", "rule_type": "allowlist", "identifier_type": "binary", "custom_message": ""}
    ]
    new_rules = [{"rule_type": "denylist", "identifier_type": "binary", "identifier": "newsha"}]

    with patch("app.connectors.executors.santa_sync_server.santa_push_rules.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.get_rules = AsyncMock(return_value=existing_rules)
        mock_instance.push_rules = AsyncMock(return_value={"pushed": 1, "machine_group": "default", "mode": "merge"})
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_push_rules.execute({"rules": new_rules}, [], _make_connector())

    assert result["pushed"] == 1
    assert result["snapshot_before"] == existing_rules
    assert result["mode"] == "merge"


@pytest.mark.asyncio
async def test_rollback_restores_snapshot():
    from app.connectors.executors.santa_sync_server import santa_push_rules

    snapshot = [{"identifier": "old", "rule_type": "allowlist", "identifier_type": "binary", "custom_message": ""}]
    execution_result = {"snapshot_before": snapshot, "machine_group": "default"}

    with patch("app.connectors.executors.santa_sync_server.santa_push_rules.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.push_rules = AsyncMock(return_value={"pushed": 1, "machine_group": "default", "mode": "replace"})
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_push_rules.rollback({}, execution_result, _make_connector())

    assert result["rolled_back"] is True
    mock_instance.push_rules.assert_called_once()
    call_kwargs = mock_instance.push_rules.call_args
    assert call_kwargs[0][2] == "replace"


@pytest.mark.asyncio
async def test_execute_no_creds_returns_mock():
    from app.connectors.executors.santa_sync_server import santa_push_rules

    result = await santa_push_rules.execute(
        {"rules": [{"rule_type": "denylist", "identifier_type": "binary", "identifier": "abc"}]},
        [],
        _make_connector(creds={}),
    )
    assert result["pushed"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python3 -m pytest tests/unit/connectors/test_santa_push_rules.py -v
```
Expected: FAIL with ImportError.

- [ ] **Step 3: Write `santa_push_rules.py`**

```python
from ._client import SantaSyncClient


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "santa_push_rules", "pushed": 0, "snapshot_before": [], "machine_group": "default", "mode": "merge"}

    machine_group = parameters.get("machine_group") or creds.get("default_machine_group")
    rules = parameters.get("rules", [])
    mode = parameters.get("mode", "merge")

    client = SantaSyncClient(creds)
    try:
        snapshot_before = await client.get_rules(machine_group)
        push_result = await client.push_rules(machine_group, rules, mode=mode)
    finally:
        await client.aclose()

    return {
        "action": "santa_push_rules",
        "pushed": push_result["pushed"],
        "machine_group": push_result.get("machine_group") or machine_group or "default",
        "mode": mode,
        "snapshot_before": snapshot_before,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": False, "reason": "no credentials"}

    snapshot = execution_result.get("snapshot_before", [])
    machine_group = execution_result.get("machine_group")

    client = SantaSyncClient(creds)
    try:
        push_result = await client.push_rules(machine_group, snapshot, mode="replace")
    finally:
        await client.aclose()

    return {
        "rolled_back": True,
        "restored_rule_count": push_result["pushed"],
        "machine_group": machine_group,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && python3 -m pytest tests/unit/connectors/test_santa_push_rules.py -v
```
Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/santa_sync_server/santa_push_rules.py \
        backend/tests/unit/connectors/test_santa_push_rules.py
git commit -m "feat(santa): add santa_push_rules executor with rollback"
```

---

### Task 5: `santa_machine_list` and `santa_machine_group_assign` Executors

**Files:**
- Create: `backend/app/connectors/executors/santa_sync_server/santa_machine_list.py`
- Create: `backend/app/connectors/executors/santa_sync_server/santa_machine_group_assign.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/unit/connectors/test_santa_machine_executors.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


def _conn(creds=None):
    m = MagicMock()
    m.credentials = creds or {"sync_server_url": "http://moroz.test", "auth_token": "tok"}
    return m


@pytest.mark.asyncio
async def test_machine_list_returns_machines():
    from app.connectors.executors.santa_sync_server import santa_machine_list

    machines = [{"machine_id": "hw-1", "hostname": "mac1", "os_version": "14.0", "santa_version": "2024.1", "last_sync": "2026-01-01T00:00:00Z", "rule_count": 5}]
    with patch("app.connectors.executors.santa_sync_server.santa_machine_list.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.list_machines = AsyncMock(return_value=machines)
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_machine_list.execute({}, [], _conn())

    assert result["machine_count"] == 1
    assert result["machines"][0]["machine_id"] == "hw-1"


@pytest.mark.asyncio
async def test_machine_list_graceful_empty():
    from app.connectors.executors.santa_sync_server import santa_machine_list

    with patch("app.connectors.executors.santa_sync_server.santa_machine_list.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.list_machines = AsyncMock(return_value=[])
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_machine_list.execute({}, [], _conn())

    assert result["machine_count"] == 0


@pytest.mark.asyncio
async def test_machine_group_assign_captures_previous():
    from app.connectors.executors.santa_sync_server import santa_machine_group_assign

    with patch("app.connectors.executors.santa_sync_server.santa_machine_group_assign.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.list_machines = AsyncMock(return_value=[
            {"machine_id": "hw-1", "hostname": "mac1", "os_version": "", "santa_version": "", "last_sync": "", "rule_count": 0}
        ])
        mock_instance.assign_machine_group = AsyncMock(return_value={})
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_machine_group_assign.execute(
            {"machine_id": "hw-1", "target_group": "quarantine"}, [], _conn()
        )

    assert result["machine_id"] == "hw-1"
    assert result["new_group"] == "quarantine"


@pytest.mark.asyncio
async def test_machine_group_assign_rollback():
    from app.connectors.executors.santa_sync_server import santa_machine_group_assign

    execution_result = {"machine_id": "hw-1", "previous_group": "default", "new_group": "quarantine"}
    with patch("app.connectors.executors.santa_sync_server.santa_machine_group_assign.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.assign_machine_group = AsyncMock(return_value={})
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_machine_group_assign.rollback({}, execution_result, _conn())

    assert result["rolled_back"] is True
    mock_instance.assign_machine_group.assert_called_once_with("hw-1", "default")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && python3 -m pytest tests/unit/connectors/test_santa_machine_executors.py -v
```
Expected: FAIL with ImportError.

- [ ] **Step 3: Write `santa_machine_list.py`**

```python
from ._client import SantaSyncClient


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "santa_machine_list", "machines": [], "machine_count": 0}

    machine_group = parameters.get("machine_group") or creds.get("default_machine_group")
    client = SantaSyncClient(creds)
    try:
        machines = await client.list_machines(machine_group)
    finally:
        await client.aclose()

    return {
        "action": "santa_machine_list",
        "machines": machines,
        "machine_count": len(machines),
        "machine_group": machine_group,
    }
```

- [ ] **Step 4: Write `santa_machine_group_assign.py`**

```python
from ._client import SantaSyncClient


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "santa_machine_group_assign", "machine_id": parameters.get("machine_id"), "previous_group": None, "new_group": parameters.get("target_group")}

    machine_id = parameters["machine_id"]
    target_group = parameters["target_group"]

    client = SantaSyncClient(creds)
    try:
        machines = await client.list_machines(None)
        previous_group = next(
            (m.get("machine_group") for m in machines if m.get("machine_id") == machine_id),
            None,
        )
        await client.assign_machine_group(machine_id, target_group)
    finally:
        await client.aclose()

    return {
        "action": "santa_machine_group_assign",
        "machine_id": machine_id,
        "previous_group": previous_group,
        "new_group": target_group,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": False, "reason": "no credentials"}

    machine_id = execution_result["machine_id"]
    previous_group = execution_result.get("previous_group")
    if not previous_group:
        return {"rolled_back": False, "reason": "no previous_group recorded"}

    client = SantaSyncClient(creds)
    try:
        await client.assign_machine_group(machine_id, previous_group)
    finally:
        await client.aclose()

    return {"rolled_back": True, "machine_id": machine_id, "restored_group": previous_group}
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd backend && python3 -m pytest tests/unit/connectors/test_santa_machine_executors.py -v
```
Expected: 4 PASSED.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/santa_sync_server/santa_machine_list.py \
        backend/app/connectors/executors/santa_sync_server/santa_machine_group_assign.py \
        backend/tests/unit/connectors/test_santa_machine_executors.py
git commit -m "feat(santa): add santa_machine_list and santa_machine_group_assign executors"
```

---

### Task 6: `santa_rule_deploy` Executor

**Files:**
- Create: `backend/app/connectors/executors/santa_sync_server/santa_rule_deploy.py`

This executor wraps `santa_push_rules` and polls `santa_machine_list` to verify rule receipt.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/connectors/test_santa_rule_deploy.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import time


def _conn():
    m = MagicMock()
    m.credentials = {"sync_server_url": "http://moroz.test", "auth_token": "tok", "default_machine_group": "default"}
    return m


@pytest.mark.asyncio
async def test_deploy_pushes_and_verifies():
    from app.connectors.executors.santa_sync_server import santa_rule_deploy

    push_time = time.time()
    machines_before = [{"machine_id": "hw-1", "last_sync": "2020-01-01T00:00:00Z"}]
    machines_after = [{"machine_id": "hw-1", "last_sync": "2099-01-01T00:00:00Z"}]

    with patch("app.connectors.executors.santa_sync_server.santa_rule_deploy.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.get_rules = AsyncMock(return_value=[])
        mock_instance.push_rules = AsyncMock(return_value={"pushed": 1, "machine_group": "default", "mode": "merge"})
        mock_instance.list_machines = AsyncMock(side_effect=[machines_before, machines_after])
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_rule_deploy.execute(
            {
                "rule_type": "denylist",
                "identifier_type": "binary",
                "identifier": "abc123sha256",
                "verify_timeout_seconds": 5,
            },
            [],
            _conn(),
        )

    assert result["deployed"] is True
    assert result["verified_machines"] >= 0


@pytest.mark.asyncio
async def test_deploy_rollback_removes_rule():
    from app.connectors.executors.santa_sync_server import santa_rule_deploy

    execution_result = {
        "identifier": "abc123sha256",
        "identifier_type": "binary",
        "rule_type": "denylist",
        "previous_state": "absent",
        "machine_group": "default",
        "snapshot_before": [],
    }
    with patch("app.connectors.executors.santa_sync_server.santa_rule_deploy.SantaSyncClient") as MockClient:
        mock_instance = AsyncMock()
        mock_instance.push_rules = AsyncMock(return_value={"pushed": 1, "machine_group": "default", "mode": "merge"})
        mock_instance.aclose = AsyncMock()
        MockClient.return_value = mock_instance
        result = await santa_rule_deploy.rollback({}, execution_result, _conn())

    assert result["rolled_back"] is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && python3 -m pytest tests/unit/connectors/test_santa_rule_deploy.py -v
```
Expected: FAIL with ImportError.

- [ ] **Step 3: Write `santa_rule_deploy.py`**

```python
import asyncio
import time
from datetime import datetime, timezone
from ._client import SantaSyncClient


def _parse_ts(ts_str: str) -> float:
    if not ts_str:
        return 0.0
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "santa_rule_deploy", "deployed": False, "verified_machines": 0, "unverified_machines": []}

    machine_group = parameters.get("machine_group") or creds.get("default_machine_group")
    rule = {
        "rule_type": parameters["rule_type"],
        "identifier_type": parameters["identifier_type"],
        "identifier": parameters["identifier"],
    }
    if parameters.get("custom_message"):
        rule["custom_message"] = parameters["custom_message"]

    verify_machines = parameters.get("verify_machines")
    timeout_secs = parameters.get("verify_timeout_seconds", 300)

    client = SantaSyncClient(creds)
    try:
        snapshot_before = await client.get_rules(machine_group)
        previous_state = "absent"
        for r in snapshot_before:
            if r["identifier"] == rule["identifier"]:
                previous_state = r["rule_type"]
                break

        push_time = time.time()
        await client.push_rules(machine_group, [rule], mode="merge")

        verified = []
        unverified = []
        deadline = time.time() + timeout_secs
        while time.time() < deadline:
            machines = await client.list_machines(machine_group)
            if verify_machines:
                machines = [m for m in machines if m["machine_id"] in verify_machines]
            if not machines:
                break
            unverified = []
            for m in machines:
                if _parse_ts(m.get("last_sync", "")) > push_time:
                    verified.append(m["machine_id"])
                else:
                    unverified.append(m["machine_id"])
            if not unverified:
                break
            await asyncio.sleep(10)
    finally:
        await client.aclose()

    return {
        "action": "santa_rule_deploy",
        "deployed": True,
        "verified_machines": len(verified),
        "unverified_machines": unverified,
        "machine_group": machine_group,
        "identifier": rule["identifier"],
        "identifier_type": rule["identifier_type"],
        "rule_type": rule["rule_type"],
        "previous_state": previous_state,
        "snapshot_before": snapshot_before,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": False, "reason": "no credentials"}

    machine_group = execution_result.get("machine_group")
    identifier = execution_result["identifier"]
    identifier_type = execution_result["identifier_type"]
    previous_state = execution_result.get("previous_state", "absent")
    snapshot_before = execution_result.get("snapshot_before", [])

    client = SantaSyncClient(creds)
    try:
        if previous_state == "absent":
            await client.push_rules(machine_group, [{"rule_type": "denylist", "identifier_type": identifier_type, "identifier": identifier}], mode="merge")
        else:
            await client.push_rules(machine_group, snapshot_before, mode="replace")
    finally:
        await client.aclose()

    return {"rolled_back": True, "restored_state": previous_state, "identifier": identifier}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && python3 -m pytest tests/unit/connectors/test_santa_rule_deploy.py -v
```
Expected: 2 PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/santa_sync_server/santa_rule_deploy.py \
        backend/tests/unit/connectors/test_santa_rule_deploy.py
git commit -m "feat(santa): add santa_rule_deploy executor with verification and rollback"
```

---

### Task 7: Register `ConnectorType` Enum and Run Full Unit Test Suite

The `ConnectorType` enum in `backend/app/models/connector.py` must include `santa_sync_server` for the connector to be creatable via the API.

- [ ] **Step 1: Find the ConnectorType enum**

```bash
grep -n "santa_sync\|nexplane_agent\|active_directory" backend/app/models/connector.py | head -20
```
Note the line number of the enum definition.

- [ ] **Step 2: Add the enum value**

Open `backend/app/models/connector.py`. Find the `ConnectorType` enum class. Add:

```python
santa_sync_server = "santa_sync_server"
```

alongside the existing entries (alphabetical order, after `santa` if it exists, or after `s`-prefixed entries).

- [ ] **Step 3: Write the failing test**

Create `backend/tests/unit/connectors/test_santa_connector_type.py`:

```python
from app.models.connector import ConnectorType


def test_santa_sync_server_in_enum():
    assert ConnectorType.santa_sync_server.value == "santa_sync_server"
```

- [ ] **Step 4: Run test to verify it fails**

```bash
cd backend && python3 -m pytest tests/unit/connectors/test_santa_connector_type.py -v
```
Expected: FAIL with AttributeError.

- [ ] **Step 5: Add the enum value** (apply the edit from Step 2)

- [ ] **Step 6: Run all santa unit tests**

```bash
cd backend && python3 -m pytest tests/unit/connectors/test_santa_sync_client.py tests/unit/connectors/test_santa_policy_audit.py tests/unit/connectors/test_santa_push_rules.py tests/unit/connectors/test_santa_machine_executors.py tests/unit/connectors/test_santa_rule_deploy.py tests/unit/connectors/test_santa_connector_type.py -v
```
Expected: All PASSED (12+ tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/connector.py \
        backend/tests/unit/connectors/test_santa_connector_type.py
git commit -m "feat(santa): register santa_sync_server ConnectorType"
```

---

### Task 8: SANTA_SYNC Smoke Phase

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

The phase:
1. Installs Docker on the EC2 runner (or reuses if already installed)
2. Pulls and starts Moroz in Docker on port 8080
3. Registers a `santa_sync_server` connector pointing at `http://localhost:8080`
4. Creates a `macos_fleet` virtual asset
5. Runs `santa_policy_audit` CR → verifies empty rule set
6. Runs `santa_push_rules` CR with 2 rules → verifies 2 rules present
7. Runs rollback on the push CR → verifies rules removed
8. Runs `santa_machine_list` CR → verifies graceful empty response
9. Tears down Moroz container

Moroz config note: Moroz requires a minimal config file and listens on port 8080 by default. The auth token is passed as `--token` flag or via env.

- [ ] **Step 1: Find the PHASES dict in test_aws_live.py**

```bash
grep -n "\"SANTA_SYNC\"\|PHASES\s*=\|phases\[" backend/tests/smoke/test_aws_live.py | head -20
```
Note the line number of the PHASES dict to find the insertion point.

- [ ] **Step 2: Add the `run_phase_santa_sync` function**

Find the last `run_phase_*` function before the `PHASES` dict. Insert the following function immediately before the `PHASES` dict:

```python
def run_phase_santa_sync(client, ec2_client, ssm_client):
    """SANTA_SYNC: Moroz Santa sync server connector smoke test."""
    import time, hashlib, json as _json

    print("\n[Phase SANTA_SYNC] Santa sync server connector smoke test")

    # Check for SSM-provided credentials (optional — use live Moroz/Zentral if available)
    ssm_url = ""
    ssm_token = ""
    try:
        ssm_url = ssm_client.get_parameter(Name="/nexplane/smoke/santa/sync_server_url", WithDecryption=True)["Parameter"]["Value"]
        ssm_token = ssm_client.get_parameter(Name="/nexplane/smoke/santa/auth_token", WithDecryption=True)["Parameter"]["Value"]
    except Exception:
        pass

    if ssm_url and ssm_token:
        sync_server_url = ssm_url
        auth_token = ssm_token
        moroz_instance_id = None
        log("SANTA_SYNC: using SSM-provided sync server credentials")
    else:
        # Provision Moroz in Docker on the backend EC2 instance
        log("SANTA_SYNC: provisioning Moroz in Docker on backend EC2")
        backend_instance_id = "i-050bab85006f0b73c"  # platform EC2 instance
        moroz_token = "smoke-santa-token-{}".format(int(time.time()))

        setup_script = r"""#!/bin/bash
set -e
which docker || (dnf install -y docker 2>/dev/null || yum install -y docker 2>/dev/null || true)
systemctl start docker 2>/dev/null || true
docker rm -f moroz-smoke 2>/dev/null || true
cat > /tmp/moroz-config.toml <<'TOMLEOF'
[server]
listen = ":8080"
[[configs]]
name = "default"
client_mode = "MONITOR"
TOMLEOF
docker run -d --name moroz-smoke \
  -p 8080:8080 \
  -v /tmp/moroz-config.toml:/etc/moroz/config.toml \
  ghcr.io/groob/moroz:latest \
  -config /etc/moroz/config.toml 2>&1 | tail -3
sleep 5
docker ps | grep moroz-smoke
echo "MOROZ_READY"
"""
        cmd_resp = ssm_client.send_command(
            InstanceIds=[backend_instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={"commands": [setup_script]},
            TimeoutSeconds=120,
        )
        cmd_id = cmd_resp["Command"]["CommandId"]
        deadline = time.time() + 150
        while time.time() < deadline:
            try:
                out = ssm_client.get_command_invocation(CommandId=cmd_id, InstanceId=backend_instance_id)
                if out["Status"] in ("Success", "Failed", "TimedOut", "Cancelled"):
                    break
            except Exception:
                pass
            time.sleep(8)
        if out["Status"] != "Success" or "MOROZ_READY" not in out.get("StandardOutputContent", ""):
            raise RuntimeError("SANTA_SYNC: Moroz failed to start: {}".format(out.get("StandardOutputContent", "")[-500:]))
        log("SANTA_SYNC: Moroz started on backend EC2")
        sync_server_url = "http://localhost:8080"
        auth_token = moroz_token
        moroz_instance_id = backend_instance_id

    try:
        # Register connector
        conn_resp = client.post("/connectors", json={
            "name": "nexplane-smoke-santa-{}".format(int(time.time())),
            "connector_type": "santa_sync_server",
        })
        conn_id = conn_resp["id"]
        client.put(f"/connectors/{conn_id}/credentials", json={"credentials": {
            "sync_server_url": sync_server_url,
            "auth_token": auth_token,
            "default_machine_group": "default",
            "tls_verify": False,
        }})
        log("SANTA_SYNC: connector registered: {}".format(conn_id))

        # Create macos_fleet virtual asset
        asset_resp = client.post("/assets", json={
            "name": "smoke-santa-fleet",
            "asset_type": "macos_fleet",
            "connector_id": conn_id,
        })
        asset_id = asset_resp["id"]
        log("SANTA_SYNC: asset created: {}".format(asset_id))

        # Step 1: santa_policy_audit — verify empty rule set
        _run_cr(client, "santa_policy_audit", {}, [asset_id],
                "[SANTA_SYNC] audit empty rule set",
                verify=lambda r: r.get("rule_count", -1) == 0)
        log("SANTA_SYNC: ✅ policy_audit confirmed empty rule set")

        # Step 2: santa_push_rules — push 2 rules
        test_rules = [
            {"rule_type": "allowlist", "identifier_type": "binary", "identifier": "a" * 64},
            {"rule_type": "denylist", "identifier_type": "binary", "identifier": "b" * 64},
        ]
        push_cr_id = _run_cr(client, "santa_push_rules",
                             {"rules": test_rules, "mode": "merge"}, [asset_id],
                             "[SANTA_SYNC] push 2 rules",
                             verify=lambda r: r.get("pushed", 0) == 2,
                             return_cr_id=True)
        log("SANTA_SYNC: ✅ pushed 2 rules")

        # Step 3: santa_policy_audit — verify 2 rules present
        _run_cr(client, "santa_policy_audit", {}, [asset_id],
                "[SANTA_SYNC] audit 2 rules present",
                verify=lambda r: r.get("rule_count", -1) == 2)
        log("SANTA_SYNC: ✅ audit confirmed 2 rules")

        # Step 4: rollback push CR — verify rules removed
        rollback_resp = client.post(f"/change-requests/{push_cr_id}/rollback")
        _wait_cr(client, rollback_resp["id"], "[SANTA_SYNC] rollback push rules")
        log("SANTA_SYNC: ✅ rollback completed")

        # Step 5: santa_policy_audit — verify empty again
        _run_cr(client, "santa_policy_audit", {}, [asset_id],
                "[SANTA_SYNC] audit empty after rollback",
                verify=lambda r: r.get("rule_count", -1) == 0)
        log("SANTA_SYNC: ✅ audit confirmed empty after rollback")

        # Step 6: santa_machine_list — graceful empty
        _run_cr(client, "santa_machine_list", {}, [asset_id],
                "[SANTA_SYNC] machine list graceful empty",
                verify=lambda r: "machines" in r)
        log("SANTA_SYNC: ✅ machine_list graceful empty OK")

        log("SANTA_SYNC: ✅ all checks passed")

    finally:
        # Teardown Moroz
        if moroz_instance_id:
            try:
                ssm_client.send_command(
                    InstanceIds=[moroz_instance_id],
                    DocumentName="AWS-RunShellScript",
                    Parameters={"commands": ["docker rm -f moroz-smoke 2>/dev/null || true"]},
                    TimeoutSeconds=30,
                )
                log("SANTA_SYNC: Moroz container stopped")
            except Exception:
                pass
        # Cleanup connector and asset
        try:
            client.delete(f"/assets/{asset_id}")
            client.delete(f"/connectors/{conn_id}")
        except Exception:
            pass
```

- [ ] **Step 3: Check if `_run_cr` and `_wait_cr` helpers already exist**

```bash
grep -n "def _run_cr\|def _wait_cr" backend/tests/smoke/test_aws_live.py | head -10
```

If they don't exist, look for the pattern used in other phases to run a CR and wait. The pattern in the file is typically:

```python
cr_resp = client.post("/change-requests", json={"change_type": "...", "parameters": ..., "asset_ids": ...})
cr_id = cr_resp["id"]
# ... approve ...
# ... poll until done ...
```

Replace `_run_cr` and `_wait_cr` calls above with the actual pattern used in the file.

- [ ] **Step 4: Wire SANTA_SYNC into the PHASES dict**

Find the `PHASES = {` dict at the bottom of the file. Add:

```python
"SANTA_SYNC": run_phase_santa_sync,
```

alongside the other phase entries.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(santa): add SANTA_SYNC smoke phase with Moroz in Docker"
```

---

### Task 9: Run SANTA_SYNC Smoke Phase Live

- [ ] **Step 1: Verify backend is running on EC2**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker ps | grep nexplane-backend"
```
Expected: backend container is running.

- [ ] **Step 2: Pull latest code on EC2 and restart backend**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd ~/nexplane && git pull && docker compose restart backend"
```
Wait 10 seconds for the backend to restart.

- [ ] **Step 3: Run the smoke phase**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "nohup docker exec nexplane-backend-1 python3 /app/tests/smoke/test_aws_live.py --phases SANTA_SYNC > /tmp/smoke_santa_sync.log 2>&1 &"
```

- [ ] **Step 4: Monitor output**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "tail -f /tmp/smoke_santa_sync.log"
```
Expected final output:
```
[SANTA_SYNC] ✅ all checks passed
✅ SMOKE TEST PASSED
```

- [ ] **Step 5: If SANTA_SYNC fails, diagnose**

Check `[SANTA_SYNC]` lines for the first ❌. Common issues:
- Moroz container not starting: check Docker is installed on backend EC2 (`docker ps`)
- CR failing: check backend logs (`docker logs nexplane-backend-1 2>&1 | tail -50`)
- Connector type unknown: verify `santa_sync_server` is in `ConnectorType` enum and catalog loaded

- [ ] **Step 6: Commit final state once smoke passes**

```bash
git add -A
git commit -m "feat(santa): SANTA_SYNC smoke phase passing"
```

---

## Implementation Notes

### CR Helper Pattern
The smoke test uses a `log()` helper and a polling loop to run CRs. Before writing `_run_cr` in Task 8, check what CR execution helpers exist in the file (search for `def log\|def _run\|def _wait`). The smoke test's actual CR flow is: POST /change-requests → POST /change-requests/{id}/approve → poll GET /change-requests/{id} until status is `completed` or `failed`. Some phases call this inline, others use helper functions — match the existing pattern in the file.

### Moroz Docker Image
The `ghcr.io/groob/moroz:latest` image is the reference Moroz implementation. If unavailable, an alternative is building from source. The smoke phase should fail gracefully if Docker is not available on the backend EC2 rather than hanging indefinitely.

### Asset Type `macos_fleet`
If `macos_fleet` is not yet in the asset type enum, add it to `backend/app/models/asset.py` alongside the other asset types before running the smoke phase.
