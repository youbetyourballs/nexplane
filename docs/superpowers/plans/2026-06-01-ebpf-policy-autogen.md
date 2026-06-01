# eBPF Policy Auto-Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `ebpf_network` and `ebpf_lsm` as two new security policy types to the existing soak → observe → synthesize → diff → CR pipeline, with audit-mode enforcement and explicit operator-gated promotion to enforce.

**Architecture:** Two new plugins register into the existing `PolicyPlugin` registry. Each has its own soak executor (dispatches agent command), synthesizer (converts raw observations to structured JSON policy), and apply/promote executors. No new tables — two new `policy_type` values extend existing CHECK constraints. A single `promote_ebpf_policy` CR type handles both network and LSM promotion.

**Tech Stack:** Python/FastAPI backend, SQLAlchemy + PostgreSQL (existing `security_policy_soak_sessions` and `security_policy_baselines` tables), Alembic migrations, existing `nexplane_agent` dispatch pattern, pytest.

---

## Existing patterns to follow

Before reading each task, understand these files:
- Plugin shape: `backend/app/services/security_policy/plugins/apparmor.py` — `_synthesize()`, `_delta_extract()`, `PolicyPlugin` dataclass
- Executor shape: `backend/app/connectors/executors/nexplane_agent/seccomp_learn.py` and `configure_seccomp.py`
- Migration shape: `backend/alembic/versions/f1dd971a6d60_add_apparmor_selinux_change_types.py` — `ALTER TYPE change_type ADD VALUE IF NOT EXISTS`
- Unit test shape: `backend/tests/unit/test_configure_seccomp_rollback.py`
- Smoke shape: `run_phase_seccomp_autogen` in `backend/tests/smoke/test_aws_live.py`

---

## Task 1: Migration — add ebpf_network and ebpf_lsm policy types

**Files:**
- Create: `backend/alembic/versions/068_ebpf_policy_types.py`
- Modify: `backend/app/models/change_request.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_ebpf_policy_plugins.py
import pytest

def test_plugin_registry_ebpf_network():
    from app.services.security_policy.plugins import get_plugin
    with pytest.raises(ValueError, match="ebpf_network"):
        get_plugin("ebpf_network")

def test_plugin_registry_ebpf_lsm():
    from app.services.security_policy.plugins import get_plugin
    with pytest.raises(ValueError, match="ebpf_lsm"):
        get_plugin("ebpf_lsm")
```

- [ ] **Step 2: Run test to verify it fails**

```
cd backend && python -m pytest tests/unit/test_ebpf_policy_plugins.py -v
```

Expected: Both tests FAIL — `ValueError` doesn't match because plugins don't exist yet.

- [ ] **Step 3: Create the migration**

```python
# backend/alembic/versions/068_ebpf_policy_types.py
"""add ebpf_network and ebpf_lsm policy types

Revision ID: 068_ebpf_policy_types
Revises: 067_merge_project_rollback_and_soak
Create Date: 2026-06-01
"""
from alembic import op

revision = "068_ebpf_policy_types"
down_revision = "067_merge_project_rollback_and_soak"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ebpf_network_soak'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'configure_ebpf_network'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'configure_ebpf_lsm'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'promote_ebpf_policy'")

    # Extend CHECK constraints on existing soak tables
    op.execute("""
        ALTER TABLE security_policy_soak_sessions
          DROP CONSTRAINT IF EXISTS security_policy_soak_sessions_policy_type_check,
          ADD CONSTRAINT security_policy_soak_sessions_policy_type_check
            CHECK (policy_type IN ('seccomp','apparmor','selinux','ebpf_network','ebpf_lsm'))
    """)
    op.execute("""
        ALTER TABLE security_policy_baselines
          DROP CONSTRAINT IF EXISTS security_policy_baselines_policy_type_check,
          ADD CONSTRAINT security_policy_baselines_policy_type_check
            CHECK (policy_type IN ('seccomp','apparmor','selinux','ebpf_network','ebpf_lsm'))
    """)


def downgrade():
    pass  # Postgres enum values cannot be removed without recreation
```

- [ ] **Step 4: Add ChangeType enum values**

In `backend/app/models/change_request.py`, find the line `# SELinux pipeline — SP3 (stub)` and add after `configure_selinux = "configure_selinux"`:

```python
    # eBPF policy autogen — SP4
    ebpf_network_soak = "ebpf_network_soak"
    configure_ebpf_network = "configure_ebpf_network"
    configure_ebpf_lsm = "configure_ebpf_lsm"
    promote_ebpf_policy = "promote_ebpf_policy"
```

- [ ] **Step 5: Run migration**

```
cd backend && alembic upgrade head
```

Expected: Migration applies cleanly, no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/068_ebpf_policy_types.py backend/app/models/change_request.py
git commit -m "feat: add ebpf_network and ebpf_lsm policy types to DB and ChangeType enum"
```

---

## Task 2: ebpf_network synthesizer plugin

**Files:**
- Create: `backend/app/services/security_policy/plugins/ebpf_network.py`
- Modify: `backend/app/services/security_policy/plugins/__init__.py`
- Test: `backend/tests/unit/test_ebpf_policy_plugins.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/unit/test_ebpf_policy_plugins.py`:

```python
def test_ebpf_network_synthesize_basic():
    from app.services.security_policy.plugins.ebpf_network import EBPF_NETWORK_PLUGIN
    raw = {
        "asset-1": [
            {"dst_ip": "8.8.8.8",  "dst_port": 53,  "protocol": "udp", "process": "systemd-resolved"},
            {"dst_ip": "10.0.1.5", "dst_port": 443, "protocol": "tcp", "process": "nginx"},
            {"dst_ip": "10.0.1.5", "dst_port": 443, "protocol": "tcp", "process": "nginx"},  # duplicate
        ],
        "asset-2": [
            {"dst_ip": "8.8.8.8", "dst_port": 53, "protocol": "udp", "process": "systemd-resolved"},
        ],
    }
    profile = EBPF_NETWORK_PLUGIN.synthesize(raw)
    assert profile["default_action"] == "audit"
    rules = profile["rules"]
    # Deduplication: two identical flows from asset-1 should produce one rule
    dns_rules = [r for r in rules if r["dst_port"] == 53]
    assert len(dns_rules) == 1
    https_rules = [r for r in rules if r["dst_port"] == 443]
    assert len(https_rules) == 1
    assert https_rules[0]["process"] == "nginx"


def test_ebpf_network_synthesize_empty():
    from app.services.security_policy.plugins.ebpf_network import EBPF_NETWORK_PLUGIN
    profile = EBPF_NETWORK_PLUGIN.synthesize({})
    assert profile["rules"] == []
    assert profile["default_action"] == "audit"


def test_ebpf_network_delta():
    from app.services.security_policy.plugins.ebpf_network import EBPF_NETWORK_PLUGIN
    prior = {
        "default_action": "audit",
        "rules": [
            {"dst_ip": "8.8.8.8", "dst_port": 53,  "protocol": "udp", "process": "systemd-resolved"},
            {"dst_ip": "10.0.1.5","dst_port": 443, "protocol": "tcp", "process": "nginx"},
        ],
    }
    current = {
        "default_action": "audit",
        "rules": [
            {"dst_ip": "8.8.8.8", "dst_port": 53,  "protocol": "udp", "process": "systemd-resolved"},
            {"dst_ip": "1.2.3.4", "dst_port": 80,  "protocol": "tcp", "process": "curl"},
        ],
    }
    delta = EBPF_NETWORK_PLUGIN.delta_extract(prior) - EBPF_NETWORK_PLUGIN.delta_extract(current)
    assert ("10.0.1.5", 443, "tcp") in delta  # removed
    added = EBPF_NETWORK_PLUGIN.delta_extract(current) - EBPF_NETWORK_PLUGIN.delta_extract(prior)
    assert ("1.2.3.4", 80, "tcp") in added  # added


def test_ebpf_network_default_action_always_audit():
    from app.services.security_policy.plugins.ebpf_network import EBPF_NETWORK_PLUGIN
    raw = {"asset-1": [{"dst_ip": "1.1.1.1", "dst_port": 443, "protocol": "tcp", "process": "curl"}]}
    profile = EBPF_NETWORK_PLUGIN.synthesize(raw)
    assert profile["default_action"] == "audit"
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && python -m pytest tests/unit/test_ebpf_policy_plugins.py::test_ebpf_network_synthesize_basic -v
```

Expected: FAIL with `ModuleNotFoundError` or `ImportError`.

- [ ] **Step 3: Write the plugin**

```python
# backend/app/services/security_policy/plugins/ebpf_network.py
from __future__ import annotations
from app.models.change_request import ChangeType
from app.services.security_policy.plugins.base import PolicyPlugin


def _synthesize(raw_observations: dict) -> dict:
    seen: set[tuple] = set()
    rules: list[dict] = []
    for flows in raw_observations.values():
        if not flows:
            continue
        for flow in flows:
            key = (flow.get("dst_ip",""), flow.get("dst_port",0), flow.get("protocol",""), flow.get("process",""))
            if key in seen:
                continue
            seen.add(key)
            rules.append({
                "dst_ip": flow.get("dst_ip", ""),
                "dst_port": flow.get("dst_port", 0),
                "protocol": flow.get("protocol", "tcp"),
                "process": flow.get("process", "*"),
            })
    return {"rules": sorted(rules, key=lambda r: (r["dst_port"], r["dst_ip"])), "default_action": "audit"}


def _delta_extract(profile: dict) -> set:
    return {
        (r["dst_ip"], r["dst_port"], r["protocol"])
        for r in profile.get("rules", [])
    }


EBPF_NETWORK_PLUGIN = PolicyPlugin(
    policy_type="ebpf_network",
    learn_command="ebpf_network_soak",
    synthesize=_synthesize,
    cr_change_type=ChangeType.configure_ebpf_network,
    delta_extract=_delta_extract,
    cr_title_template="Apply eBPF network policy — {service_name}",
    cr_description_template=(
        "eBPF network egress policy synthesized from soak session. "
        "Rules: {rule_count}. Partial observation: {partial}."
    ),
)
```

- [ ] **Step 4: Register the plugin**

In `backend/app/services/security_policy/plugins/__init__.py`, add after the selinux registration block:

```python
from app.services.security_policy.plugins.ebpf_network import EBPF_NETWORK_PLUGIN  # noqa: E402
_register(EBPF_NETWORK_PLUGIN)
```

- [ ] **Step 5: Run all ebpf_network tests**

```
cd backend && python -m pytest tests/unit/test_ebpf_policy_plugins.py -k "ebpf_network" -v
```

Expected: All 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/security_policy/plugins/ebpf_network.py \
        backend/app/services/security_policy/plugins/__init__.py \
        backend/tests/unit/test_ebpf_policy_plugins.py
git commit -m "feat: add ebpf_network synthesizer plugin"
```

---

## Task 3: ebpf_lsm synthesizer plugin

**Files:**
- Create: `backend/app/services/security_policy/plugins/ebpf_lsm.py`
- Modify: `backend/app/services/security_policy/plugins/__init__.py`
- Test: `backend/tests/unit/test_ebpf_policy_plugins.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/unit/test_ebpf_policy_plugins.py`:

```python
def test_ebpf_lsm_synthesize_basic():
    from app.services.security_policy.plugins.ebpf_lsm import EBPF_LSM_PLUGIN
    raw = {
        "asset-1": [
            {"syscall": "open",   "path": "/etc/nginx/nginx.conf", "process": "nginx", "uid": 0},
            {"syscall": "open",   "path": "/etc/nginx/nginx.conf", "process": "nginx", "uid": 0},  # dup
            {"syscall": "execve", "path": "/usr/sbin/nginx",       "process": "nginx", "uid": 0},
        ],
        "asset-2": [
            {"syscall": "open", "path": "/etc/resolv.conf", "process": "systemd-resolved", "uid": 101},
        ],
    }
    profile = EBPF_LSM_PLUGIN.synthesize(raw)
    assert profile["default_action"] == "audit"
    rules = profile["rules"]
    # Deduplication: two identical open events → one rule
    open_nginx = [r for r in rules if r["syscall"] == "open" and "nginx.conf" in r["path_pattern"]]
    assert len(open_nginx) == 1
    assert open_nginx[0]["action"] == "allow"


def test_ebpf_lsm_synthesize_empty():
    from app.services.security_policy.plugins.ebpf_lsm import EBPF_LSM_PLUGIN
    profile = EBPF_LSM_PLUGIN.synthesize({})
    assert profile["rules"] == []
    assert profile["default_action"] == "audit"


def test_ebpf_lsm_delta():
    from app.services.security_policy.plugins.ebpf_lsm import EBPF_LSM_PLUGIN
    prior = {
        "default_action": "audit",
        "rules": [
            {"syscall": "open",   "path_pattern": "/etc/nginx/*", "process": "nginx", "action": "allow"},
            {"syscall": "execve", "path_pattern": "/usr/sbin/*",  "process": "nginx", "action": "allow"},
        ],
    }
    current = {
        "default_action": "audit",
        "rules": [
            {"syscall": "open",   "path_pattern": "/etc/nginx/*", "process": "nginx", "action": "allow"},
            {"syscall": "open",   "path_pattern": "/var/log/*",   "process": "nginx", "action": "allow"},
        ],
    }
    removed = EBPF_LSM_PLUGIN.delta_extract(prior) - EBPF_LSM_PLUGIN.delta_extract(current)
    assert ("execve", "/usr/sbin/*", "nginx") in removed
    added = EBPF_LSM_PLUGIN.delta_extract(current) - EBPF_LSM_PLUGIN.delta_extract(prior)
    assert ("open", "/var/log/*", "nginx") in added


def test_ebpf_lsm_default_action_always_audit():
    from app.services.security_policy.plugins.ebpf_lsm import EBPF_LSM_PLUGIN
    raw = {"asset-1": [{"syscall": "open", "path": "/tmp/x", "process": "bash", "uid": 1000}]}
    profile = EBPF_LSM_PLUGIN.synthesize(raw)
    assert profile["default_action"] == "audit"


def test_plugin_registry_both_registered():
    from app.services.security_policy.plugins import get_plugin
    assert get_plugin("ebpf_network").policy_type == "ebpf_network"
    assert get_plugin("ebpf_lsm").policy_type == "ebpf_lsm"
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && python -m pytest tests/unit/test_ebpf_policy_plugins.py -k "ebpf_lsm or both_registered" -v
```

Expected: All 5 tests FAIL.

- [ ] **Step 3: Write the plugin**

The path globbing rule: collapse specific filenames under a directory into a `dir/*` pattern. For example `/etc/nginx/nginx.conf` → `/etc/nginx/*`, `/var/log/nginx/access.log` → `/var/log/nginx/*`. Any path not matching a known prefix is kept as-is.

```python
# backend/app/services/security_policy/plugins/ebpf_lsm.py
from __future__ import annotations
import re
from app.models.change_request import ChangeType
from app.services.security_policy.plugins.base import PolicyPlugin

_DIR_GLOB_RULES = [
    re.compile(r"^(/etc/[^/]+)/[^/]+$"),
    re.compile(r"^(/var/log/[^/]+)/[^/]+$"),
    re.compile(r"^(/var/lib/[^/]+)/[^/]+$"),
    re.compile(r"^(/usr/[^/]+/[^/]+)/[^/]+$"),
    re.compile(r"^(/run/[^/]+)/[^/]+$"),
    re.compile(r"^/tmp/[^/]+$"),
]

_TMP_RE = re.compile(r"^/tmp/[^/]+$")


def _glob_path(path: str) -> str:
    if _TMP_RE.match(path):
        return "/tmp/*"
    for pattern in _DIR_GLOB_RULES:
        m = pattern.match(path)
        if m:
            return m.group(1) + "/*"
    return path


def _synthesize(raw_observations: dict) -> dict:
    seen: set[tuple] = set()
    rules: list[dict] = []
    for events in raw_observations.values():
        if not events:
            continue
        for event in events:
            syscall = event.get("syscall", "")
            path_pattern = _glob_path(event.get("path", ""))
            process = event.get("process", "*")
            key = (syscall, path_pattern, process)
            if key in seen:
                continue
            seen.add(key)
            rules.append({"syscall": syscall, "path_pattern": path_pattern, "process": process, "action": "allow"})
    return {
        "rules": sorted(rules, key=lambda r: (r["syscall"], r["path_pattern"])),
        "default_action": "audit",
    }


def _delta_extract(profile: dict) -> set:
    return {
        (r["syscall"], r["path_pattern"], r["process"])
        for r in profile.get("rules", [])
    }


EBPF_LSM_PLUGIN = PolicyPlugin(
    policy_type="ebpf_lsm",
    learn_command="ebpf_lsm_soak",
    synthesize=_synthesize,
    cr_change_type=ChangeType.configure_ebpf_lsm,
    delta_extract=_delta_extract,
    cr_title_template="Apply eBPF LSM policy — {service_name}",
    cr_description_template=(
        "eBPF LSM kernel policy synthesized from soak session. "
        "Rules: {rule_count}. Partial observation: {partial}."
    ),
)
```

- [ ] **Step 4: Register the plugin**

In `backend/app/services/security_policy/plugins/__init__.py`, add after the `EBPF_NETWORK_PLUGIN` registration:

```python
from app.services.security_policy.plugins.ebpf_lsm import EBPF_LSM_PLUGIN  # noqa: E402
_register(EBPF_LSM_PLUGIN)
```

- [ ] **Step 5: Run all plugin tests**

```
cd backend && python -m pytest tests/unit/test_ebpf_policy_plugins.py -v
```

Expected: All tests PASS (both plugins registered, all synthesize/delta tests pass).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/security_policy/plugins/ebpf_lsm.py \
        backend/app/services/security_policy/plugins/__init__.py \
        backend/tests/unit/test_ebpf_policy_plugins.py
git commit -m "feat: add ebpf_lsm synthesizer plugin and register both eBPF plugins"
```

---

## Task 4: Observation executors (ebpf_network_soak and ebpf_lsm_soak)

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/ebpf_network_soak.py`
- Create: `backend/app/connectors/executors/nexplane_agent/ebpf_lsm_soak.py`
- Create: `backend/app/connectors/change_type_definitions/ebpf_network_soak.json`
- Create: `backend/app/connectors/change_type_definitions/ebpf_lsm_soak.json`

Both executors follow the same pattern as `seccomp_learn.py` exactly — dispatch agent job, no rollback (observation creates no persistent state).

- [ ] **Step 1: Create ebpf_network_soak executor**

```python
# backend/app/connectors/executors/nexplane_agent/ebpf_network_soak.py
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="ebpf_network_soak",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=int(parameters.get("window_seconds", 60)) + 30,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ebpf_network_soak creates no persistent state"}
```

- [ ] **Step 2: Create ebpf_lsm_soak executor**

```python
# backend/app/connectors/executors/nexplane_agent/ebpf_lsm_soak.py
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="ebpf_lsm_soak",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=int(parameters.get("window_seconds", 60)) + 30,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ebpf_lsm_soak creates no persistent state"}
```

- [ ] **Step 3: Create change type definition for ebpf_network_soak**

```json
{
  "change_type": "ebpf_network_soak",
  "display_name": "eBPF Network Soak",
  "description": "Observe outbound network connections via eBPF TC/cgroup hooks for a defined window and return flow tuples for policy synthesis.",
  "connector_type": "nexplane_agent",
  "generic_action": "ebpf_network_soak",
  "rollback_type": "none",
  "parameters": [
    {"name": "window_seconds", "type": "integer", "required": false, "default": 60, "description": "Observation window in seconds"},
    {"name": "service_name",   "type": "string",  "required": false, "description": "Optional process name filter"}
  ]
}
```

Save as `backend/app/connectors/change_type_definitions/ebpf_network_soak.json`.

- [ ] **Step 4: Create change type definition for ebpf_lsm_soak**

```json
{
  "change_type": "ebpf_lsm_soak",
  "display_name": "eBPF LSM Soak",
  "description": "Observe kernel events (syscalls, file access) via eBPF LSM/kprobe hooks for a defined window and return event tuples for policy synthesis.",
  "connector_type": "nexplane_agent",
  "generic_action": "ebpf_lsm_soak",
  "rollback_type": "none",
  "parameters": [
    {"name": "window_seconds", "type": "integer", "required": false, "default": 60, "description": "Observation window in seconds"},
    {"name": "service_name",   "type": "string",  "required": false, "description": "Optional process name filter"}
  ]
}
```

Save as `backend/app/connectors/change_type_definitions/ebpf_lsm_soak.json`.

- [ ] **Step 5: Verify executor imports cleanly**

```
cd backend && python -c "from app.connectors.executors.nexplane_agent import ebpf_network_soak, ebpf_lsm_soak; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/ebpf_network_soak.py \
        backend/app/connectors/executors/nexplane_agent/ebpf_lsm_soak.py \
        backend/app/connectors/change_type_definitions/ebpf_network_soak.json \
        backend/app/connectors/change_type_definitions/ebpf_lsm_soak.json
git commit -m "feat: add ebpf_network_soak and ebpf_lsm_soak observation executors"
```

---

## Task 5: Apply executors (configure_ebpf_network and configure_ebpf_lsm)

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/configure_ebpf_network.py`
- Create: `backend/app/connectors/executors/nexplane_agent/configure_ebpf_lsm.py`
- Create: `backend/app/connectors/change_type_definitions/configure_ebpf_network.json`
- Create: `backend/app/connectors/change_type_definitions/configure_ebpf_lsm.json`
- Test: `backend/tests/unit/test_ebpf_executors.py`

Rollback dispatches the same agent command with `action: "restore"` and the `snapshot_id` from the execution result — same pattern as `configure_seccomp.py`.

- [ ] **Step 1: Write failing tests**

```python
# backend/tests/unit/test_ebpf_executors.py
import pytest
from unittest.mock import patch


@pytest.mark.asyncio
async def test_configure_ebpf_network_rollback_passes_snapshot_id():
    from app.connectors.executors.nexplane_agent import configure_ebpf_network
    execution_result = {
        "_asset_ids": ["asset-1"],
        "snapshot_id": "snap-abc123",
        "policy_type": "network",
    }
    captured = {}

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        captured.update({"command": command, "parameters": parameters})
        return {"rolled_back": True}

    with patch("app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job", side_effect=fake_dispatch):
        result = await configure_ebpf_network.rollback({}, execution_result, connector=None)

    assert captured["command"] == "configure_ebpf_network"
    assert captured["parameters"]["action"] == "restore"
    assert captured["parameters"]["snapshot_id"] == "snap-abc123"
    assert result["rolled_back"] is True


@pytest.mark.asyncio
async def test_configure_ebpf_lsm_rollback_passes_snapshot_id():
    from app.connectors.executors.nexplane_agent import configure_ebpf_lsm
    execution_result = {
        "_asset_ids": ["asset-1"],
        "snapshot_id": "snap-def456",
        "kernel_lsm": True,
    }
    captured = {}

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        captured.update({"command": command, "parameters": parameters})
        return {"rolled_back": True}

    with patch("app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job", side_effect=fake_dispatch):
        result = await configure_ebpf_lsm.rollback({}, execution_result, connector=None)

    assert captured["command"] == "configure_ebpf_lsm"
    assert captured["parameters"]["action"] == "restore"
    assert captured["parameters"]["snapshot_id"] == "snap-def456"
    assert result["rolled_back"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && python -m pytest tests/unit/test_ebpf_executors.py -v
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Create configure_ebpf_network executor**

```python
# backend/app/connectors/executors/nexplane_agent/configure_ebpf_network.py
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="configure_ebpf_network",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await _dispatch.dispatch_agent_job(
        command="configure_ebpf_network",
        parameters={"action": "restore", "snapshot_id": execution_result.get("snapshot_id", "")},
        asset_ids=asset_ids,
        timeout_seconds=120,
    )
```

- [ ] **Step 4: Create configure_ebpf_lsm executor**

```python
# backend/app/connectors/executors/nexplane_agent/configure_ebpf_lsm.py
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="configure_ebpf_lsm",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await _dispatch.dispatch_agent_job(
        command="configure_ebpf_lsm",
        parameters={"action": "restore", "snapshot_id": execution_result.get("snapshot_id", "")},
        asset_ids=asset_ids,
        timeout_seconds=120,
    )
```

- [ ] **Step 5: Create change type definition for configure_ebpf_network**

```json
{
  "change_type": "configure_ebpf_network",
  "display_name": "Configure eBPF Network Policy",
  "description": "Load a pre-compiled eBPF TC/cgroup network enforcer and write the allowlist into BPF maps. Starts in audit mode. Rollback unloads the program and clears all maps.",
  "connector_type": "nexplane_agent",
  "generic_action": "configure_ebpf_network",
  "rollback_type": "standard",
  "parameters": [
    {"name": "profile",      "type": "object",  "required": true,  "description": "NetworkPolicy profile from synthesis"},
    {"name": "service_name", "type": "string",  "required": false, "description": "Service label for BPF pin path"}
  ]
}
```

Save as `backend/app/connectors/change_type_definitions/configure_ebpf_network.json`.

- [ ] **Step 6: Create change type definition for configure_ebpf_lsm**

```json
{
  "change_type": "configure_ebpf_lsm",
  "display_name": "Configure eBPF LSM Policy",
  "description": "Load a pre-compiled eBPF LSM enforcer (kprobe fallback on kernel < 5.7) and write the allowlist into BPF maps. Starts in audit mode. Sets kernel_lsm=true/false in result. Rollback unloads and clears all maps.",
  "connector_type": "nexplane_agent",
  "generic_action": "configure_ebpf_lsm",
  "rollback_type": "standard",
  "parameters": [
    {"name": "profile",      "type": "object",  "required": true,  "description": "LSMPolicy profile from synthesis"},
    {"name": "service_name", "type": "string",  "required": false, "description": "Service label for BPF pin path"}
  ]
}
```

Save as `backend/app/connectors/change_type_definitions/configure_ebpf_lsm.json`.

- [ ] **Step 7: Run all executor tests**

```
cd backend && python -m pytest tests/unit/test_ebpf_executors.py -v
```

Expected: Both tests PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/configure_ebpf_network.py \
        backend/app/connectors/executors/nexplane_agent/configure_ebpf_lsm.py \
        backend/app/connectors/change_type_definitions/configure_ebpf_network.json \
        backend/app/connectors/change_type_definitions/configure_ebpf_lsm.json \
        backend/tests/unit/test_ebpf_executors.py
git commit -m "feat: add configure_ebpf_network and configure_ebpf_lsm apply executors"
```

---

## Task 6: Promote executor (promote_ebpf_policy)

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/promote_ebpf_policy.py`
- Create: `backend/app/connectors/change_type_definitions/promote_ebpf_policy.json`
- Test: `backend/tests/unit/test_ebpf_executors.py`

- [ ] **Step 1: Write failing tests**

Add to `backend/tests/unit/test_ebpf_executors.py`:

```python
@pytest.mark.asyncio
async def test_promote_ebpf_policy_dispatches_correct_command():
    from app.connectors.executors.nexplane_agent import promote_ebpf_policy
    captured = {}

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        captured.update({"command": command, "parameters": parameters})
        return {"prior_mode": "audit", "current_mode": "enforce"}

    params = {"policy_type": "network", "asset_id": "asset-1"}
    with patch("app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job", side_effect=fake_dispatch):
        result = await promote_ebpf_policy.execute(params, ["asset-1"], connector=None)

    assert captured["command"] == "promote_ebpf_policy"
    assert captured["parameters"]["policy_type"] == "network"
    assert result["prior_mode"] == "audit"


@pytest.mark.asyncio
async def test_promote_ebpf_policy_rollback_restores_audit():
    from app.connectors.executors.nexplane_agent import promote_ebpf_policy
    execution_result = {"_asset_ids": ["asset-1"], "prior_mode": "audit", "policy_type": "network"}
    captured = {}

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        captured.update({"command": command, "parameters": parameters})
        return {"prior_mode": "enforce", "current_mode": "audit"}

    with patch("app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job", side_effect=fake_dispatch):
        result = await promote_ebpf_policy.rollback({}, execution_result, connector=None)

    assert captured["parameters"]["mode"] == "audit"
    assert captured["parameters"]["policy_type"] == "network"
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd backend && python -m pytest tests/unit/test_ebpf_executors.py -k "promote" -v
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Create promote_ebpf_policy executor**

```python
# backend/app/connectors/executors/nexplane_agent/promote_ebpf_policy.py
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="promote_ebpf_policy",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=60,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    result["policy_type"] = parameters.get("policy_type", "")
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await _dispatch.dispatch_agent_job(
        command="promote_ebpf_policy",
        parameters={
            "policy_type": execution_result.get("policy_type", parameters.get("policy_type", "")),
            "mode": execution_result.get("prior_mode", "audit"),
        },
        asset_ids=asset_ids,
        timeout_seconds=60,
    )
```

- [ ] **Step 4: Create change type definition**

```json
{
  "change_type": "promote_ebpf_policy",
  "display_name": "Promote eBPF Policy to Enforce",
  "description": "Flip a loaded eBPF policy from audit mode to enforce mode by writing to the BPF mode map. Rollback writes audit mode back.",
  "connector_type": "nexplane_agent",
  "generic_action": "promote_ebpf_policy",
  "rollback_type": "standard",
  "parameters": [
    {"name": "policy_type", "type": "string", "required": true, "description": "network or lsm"},
    {"name": "asset_id",    "type": "string", "required": true, "description": "Asset UUID the policy is loaded on"}
  ]
}
```

Save as `backend/app/connectors/change_type_definitions/promote_ebpf_policy.json`.

- [ ] **Step 5: Run all executor tests**

```
cd backend && python -m pytest tests/unit/test_ebpf_executors.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/promote_ebpf_policy.py \
        backend/app/connectors/change_type_definitions/promote_ebpf_policy.json \
        backend/tests/unit/test_ebpf_executors.py
git commit -m "feat: add promote_ebpf_policy executor with audit→enforce and rollback"
```

---

## Task 7: Catalog entries in nexplane_agent.json

**Files:**
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`

The `nexplane_agent.json` catalog is a single JSON file with an `"actions"` array. Add 5 new entries following the exact structure of the existing `configure_seccomp` and `seccomp_learn` entries.

- [ ] **Step 1: Locate the insertion point**

Open `backend/app/connectors/catalog/nexplane_agent.json`. Find the block containing `"change_type": "configure_selinux"`. Insert the following 5 action objects into the `"actions"` array immediately after that block.

- [ ] **Step 2: Add the 5 catalog entries**

```json
{
  "display_name": "eBPF Network Soak",
  "description": "Observe outbound network connections via eBPF TC/cgroup hooks and return flow tuples for policy synthesis.",
  "change_type": "ebpf_network_soak",
  "execution_tier": 3,
  "rollback_action": "ebpf_network_soak",
  "estimated_duration_seconds": 90,
  "applicable_asset_types": ["server"],
  "action_type": "change",
  "executor": "nexplane_agent.ebpf_network_soak",
  "generic_action": "ebpf_network_soak",
  "action_id": "ebpf_network_soak",
  "parameters": [
    {"name": "window_seconds", "type": "integer", "required": false},
    {"name": "service_name",   "type": "string",  "required": false}
  ]
},
{
  "display_name": "eBPF LSM Soak",
  "description": "Observe kernel events via eBPF LSM/kprobe hooks and return event tuples for policy synthesis.",
  "change_type": "ebpf_lsm_soak",
  "execution_tier": 3,
  "rollback_action": "ebpf_lsm_soak",
  "estimated_duration_seconds": 90,
  "applicable_asset_types": ["server"],
  "action_type": "change",
  "executor": "nexplane_agent.ebpf_lsm_soak",
  "generic_action": "ebpf_lsm_soak",
  "action_id": "ebpf_lsm_soak",
  "parameters": [
    {"name": "window_seconds", "type": "integer", "required": false},
    {"name": "service_name",   "type": "string",  "required": false}
  ]
},
{
  "display_name": "Configure eBPF Network Policy",
  "description": "Load eBPF TC/cgroup network enforcer and write allowlist to BPF maps. Starts in audit mode.",
  "change_type": "configure_ebpf_network",
  "execution_tier": 3,
  "rollback_action": "configure_ebpf_network",
  "estimated_duration_seconds": 15,
  "applicable_asset_types": ["server"],
  "action_type": "change",
  "executor": "nexplane_agent.configure_ebpf_network",
  "generic_action": "configure_ebpf_network",
  "action_id": "configure_ebpf_network",
  "parameters": [
    {"name": "profile",      "type": "object",  "required": true},
    {"name": "service_name", "type": "string",  "required": false}
  ]
},
{
  "display_name": "Configure eBPF LSM Policy",
  "description": "Load eBPF LSM enforcer (kprobe fallback on kernel < 5.7) and write allowlist to BPF maps. Starts in audit mode.",
  "change_type": "configure_ebpf_lsm",
  "execution_tier": 3,
  "rollback_action": "configure_ebpf_lsm",
  "estimated_duration_seconds": 15,
  "applicable_asset_types": ["server"],
  "action_type": "change",
  "executor": "nexplane_agent.configure_ebpf_lsm",
  "generic_action": "configure_ebpf_lsm",
  "action_id": "configure_ebpf_lsm",
  "parameters": [
    {"name": "profile",      "type": "object",  "required": true},
    {"name": "service_name", "type": "string",  "required": false}
  ]
},
{
  "display_name": "Promote eBPF Policy to Enforce",
  "description": "Flip a loaded eBPF policy from audit to enforce by writing to the BPF mode map. Rollback restores audit mode.",
  "change_type": "promote_ebpf_policy",
  "execution_tier": 3,
  "rollback_action": "promote_ebpf_policy",
  "estimated_duration_seconds": 5,
  "applicable_asset_types": ["server"],
  "action_type": "change",
  "executor": "nexplane_agent.promote_ebpf_policy",
  "generic_action": "promote_ebpf_policy",
  "action_id": "promote_ebpf_policy",
  "parameters": [
    {"name": "policy_type", "type": "string", "required": true},
    {"name": "asset_id",    "type": "string", "required": true}
  ]
}
```

- [ ] **Step 3: Verify JSON is valid**

```
cd backend && python -c "import json; json.load(open('app/connectors/catalog/nexplane_agent.json')); print('valid JSON')"
```

Expected: `valid JSON`

- [ ] **Step 4: Verify CR manifest includes new types**

```
cd backend && python -c "
from app.services.manifest_builder import get_manifest
types = [e['change_type'] for e in get_manifest()]
for t in ['ebpf_network_soak','configure_ebpf_network','configure_ebpf_lsm','promote_ebpf_policy']:
    assert t in types, f'{t} missing from manifest'
print('all 4 types in manifest')
"
```

Expected: `all 4 types in manifest`

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/catalog/nexplane_agent.json
git commit -m "feat: add eBPF policy CR types to nexplane_agent catalog"
```

---

## Task 8: Smoke test — EBPF_POLICY phase

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

The EBPF_POLICY phase reuses an existing agent-registered EC2 instance (from phase A), runs both the network and LSM legs, and verifies audit-mode violation detection and promote→rollback round-trips.

The phase uses `client.run_cr()` and `client.post()` helpers already present throughout the smoke file. Reference `run_phase_seccomp_autogen` for the full soak session API call pattern.

- [ ] **Step 1: Add EBPF_POLICY to the phase list comment**

Find the block near the top of `test_aws_live.py` that lists phase names (the multi-line string with `SECCOMP_AUTOGEN`, `APPARMOR_AUTOGEN`, `SELINUX_AUTOGEN`). Add:

```
    EBPF_POLICY      eBPF network + LSM policy: soak → synthesize → audit-mode CR → violation → promote → enforce → rollback
```

- [ ] **Step 2: Add the dispatch block**

Find the block `if "SELINUX_AUTOGEN" in phases:` and add immediately after its closing block:

```python
        if "EBPF_POLICY" in phases:
            run_phase_ebpf_policy(client, base_url=args.base_url,
                                  cloud_account_id=cloud_account_id,
                                  tailscale_auth_key=tailscale_auth_key,
                                  backend_tailscale_ip=args.backend_tailscale_ip)
```

- [ ] **Step 3: Add the phase function**

Add at the end of `test_aws_live.py` (before the final `if __name__ == "__main__":` block):

```python
def run_phase_ebpf_policy(client, base_url, cloud_account_id=None,
                          tailscale_auth_key=None, backend_tailscale_ip=None, **kwargs):
    """
    Smoke test for EBPF_POLICY.
    Provisions a fresh EC2 with the Nexplane agent, exercises both eBPF network and
    LSM soak → synthesize → configure (audit) → violation detection → promote → enforce
    → rollback pipeline.
    """
    import time as _time
    import boto3 as _boto3

    log = lambda msg: print(f"  [EBPF_POLICY] {msg}", flush=True)
    log("Starting EBPF_POLICY smoke phase")

    if not cloud_account_id or cloud_account_id == "standalone":
        cloud_account_id = client.get_cloud_account_asset_id()

    ts = int(_time.time())
    instance_name = f"nexplane-smoke-ebpf-{ts}"
    key_name = f"nexplane-smoke-ebpf-key-{ts}"

    # ---- 1. Provision EC2 ----
    log(f"Creating key pair {key_name}...")
    client.run_cr("[EBPF_POLICY] create key pair", "key_pair_create", cloud_account_id,
                  {"key_name": key_name})

    log(f"Launching EC2 instance {instance_name}...")
    client.run_cr("[EBPF_POLICY] launch EC2", "ec2_launch", cloud_account_id,
                  {"mode": "quick", "name": instance_name, "os": "amazon_linux",
                   "iam_instance_profile": "NexplaneEC2TestProfile", "key_name": key_name,
                   "rollback_strategy": "terminate_instance"})

    ec2_client = _boto3.client("ec2", region_name="us-east-1")
    instance_asset = None
    instance_id = None
    for _ in range(48):
        _time.sleep(5)
        candidates = [a for a in client.get("/assets", params={"q": instance_name})
                      if a["name"] == instance_name]
        for c in sorted(candidates, key=lambda a: a.get("updated_at", ""), reverse=True):
            cid = c.get("asset_metadata", {}).get("instance_id", "")
            if not cid:
                continue
            try:
                state = ec2_client.describe_instances(InstanceIds=[cid])["Reservations"][0]["Instances"][0]["State"]["Name"]
                if state in ("pending", "running"):
                    instance_asset = c
                    instance_id = cid
                    break
            except Exception:
                pass
        if instance_asset:
            break
    assert instance_asset, f"Instance {instance_name} not found in inventory within 4min"
    log(f"Instance {instance_id} in inventory as asset {instance_asset['id']}")

    log("Waiting 3min for SSM agent...")
    _time.sleep(180)

    # ---- 2. Deploy Nexplane agent ----
    auth_key = client.get_tailscale_auth_key(tailscale_auth_key or "")
    agent_secret = client.get_agent_secret()
    backend_ip = backend_tailscale_ip or "100.101.186.39"
    nexplane_url = f"http://{backend_ip}:8000"

    client.run_cr("[EBPF_POLICY] tailscale join", "tailscale_join", instance_asset["id"],
                  {"instance_id": instance_id, "auth_key": auth_key, "hostname": instance_name})
    client.run_cr("[EBPF_POLICY] deploy nexplane agent", "deploy_nexplane_agent", instance_asset["id"],
                  {"instance_id": instance_id, "nexplane_url": nexplane_url,
                   "nexplane_secret": agent_secret, "hostname": instance_name,
                   "download_url": nexplane_url})

    agent_asset_id = None
    deadline = _time.time() + 300
    while _time.time() < deadline:
        candidates = client.get("/assets", params={"q": instance_name, "asset_type": "server"})
        tagged = [c for c in candidates
                  if "nexplane-agent" in (c.get("tags") or []) and c.get("name") == instance_name]
        if tagged:
            agent_asset_id = sorted(tagged, key=lambda c: c.get("updated_at") or "", reverse=True)[0]["id"]
            log(f"Agent registered: {agent_asset_id}")
            break
        _time.sleep(10)
    assert agent_asset_id, "Agent did not register within 5min"

    # ---- 3. Network soak leg ----
    log("Starting network soak (30s)...")
    soak_resp = client.post("/security-policy/soak-sessions", json={
        "project_id": None,
        "policy_type": "ebpf_network",
        "window_seconds": 30,
        "asset_ids": [agent_asset_id],
    })
    session_id = soak_resp["id"]
    log(f"Soak session {session_id} started")

    # Poll until synthesized or timeout
    deadline = _time.time() + 120
    session = None
    while _time.time() < deadline:
        _time.sleep(10)
        session = client.get(f"/security-policy/soak-sessions/{session_id}")
        if session["status"] in ("synthesized", "cr_proposed"):
            break
    assert session and session["status"] in ("synthesized", "cr_proposed"), \
        f"Network soak session did not synthesize within 2min: {session}"
    profile = session.get("synthesized_profile", {})
    rules = profile.get("rules", [])
    assert len(rules) > 0, "Network soak returned no flow rules (expected at least DNS)"
    log(f"Network soak synthesized — {len(rules)} rules (DNS present: {any(r['dst_port']==53 for r in rules)})")

    # Apply in audit mode
    log("Applying network policy in audit mode...")
    cr_net = client.run_cr("[EBPF_POLICY] configure_ebpf_network", "configure_ebpf_network",
                           agent_asset_id,
                           {"profile": profile, "service_name": "nexplane-smoke"})
    result_net = client.get_cr_step_result(cr_net)
    assert result_net.get("snapshot_id"), f"configure_ebpf_network missing snapshot_id: {result_net}"
    log(f"Network policy loaded — snapshot_id={result_net['snapshot_id']}")

    # Promote to enforce
    log("Promoting network policy to enforce...")
    cr_net_promote = client.run_cr("[EBPF_POLICY] promote network", "promote_ebpf_policy",
                                   agent_asset_id,
                                   {"policy_type": "network", "asset_id": agent_asset_id})
    result_net_promote = client.get_cr_step_result(cr_net_promote)
    assert result_net_promote.get("prior_mode") == "audit", \
        f"Expected prior_mode=audit: {result_net_promote}"
    log("Network policy in enforce mode")

    # Rollback promote → back to audit
    log("Rolling back promote (network → audit)...")
    client.post(f"/change-requests/{cr_net_promote['id']}/rollback", json={})
    deadline = _time.time() + 60
    while _time.time() < deadline:
        cr_state = client.get(f"/change-requests/{cr_net_promote['id']}")
        if cr_state.get("rollback_status") in ("completed", "rolled_back", "failed"):
            break
        _time.sleep(5)
    assert cr_state.get("rollback_status") in ("completed", "rolled_back"), \
        f"Network promote rollback did not complete: {cr_state}"
    log("Network promote rolled back ✓")

    # Rollback configure_ebpf_network → maps unloaded
    log("Rolling back configure_ebpf_network (unload maps)...")
    client.post(f"/change-requests/{cr_net['id']}/rollback", json={})
    deadline = _time.time() + 60
    while _time.time() < deadline:
        cr_state = client.get(f"/change-requests/{cr_net['id']}")
        if cr_state.get("rollback_status") in ("completed", "rolled_back", "failed"):
            break
        _time.sleep(5)
    assert cr_state.get("rollback_status") in ("completed", "rolled_back"), \
        f"Network policy rollback did not complete: {cr_state}"
    log("Network policy rolled back ✓")

    # ---- 4. LSM soak leg ----
    log("Starting LSM soak (30s)...")
    lsm_soak_resp = client.post("/security-policy/soak-sessions", json={
        "project_id": None,
        "policy_type": "ebpf_lsm",
        "window_seconds": 30,
        "asset_ids": [agent_asset_id],
    })
    lsm_session_id = lsm_soak_resp["id"]
    log(f"LSM soak session {lsm_session_id} started")

    deadline = _time.time() + 120
    lsm_session = None
    while _time.time() < deadline:
        _time.sleep(10)
        lsm_session = client.get(f"/security-policy/soak-sessions/{lsm_session_id}")
        if lsm_session["status"] in ("synthesized", "cr_proposed"):
            break
    assert lsm_session and lsm_session["status"] in ("synthesized", "cr_proposed"), \
        f"LSM soak session did not synthesize within 2min: {lsm_session}"
    lsm_profile = lsm_session.get("synthesized_profile", {})
    lsm_rules = lsm_profile.get("rules", [])
    assert len(lsm_rules) > 0, "LSM soak returned no event rules"
    log(f"LSM soak synthesized — {len(lsm_rules)} rules, kernel_lsm will be set by executor")

    # Apply in audit mode
    log("Applying LSM policy in audit mode...")
    cr_lsm = client.run_cr("[EBPF_POLICY] configure_ebpf_lsm", "configure_ebpf_lsm",
                           agent_asset_id,
                           {"profile": lsm_profile, "service_name": "nexplane-smoke"})
    result_lsm = client.get_cr_step_result(cr_lsm)
    assert result_lsm.get("snapshot_id"), f"configure_ebpf_lsm missing snapshot_id: {result_lsm}"
    log(f"LSM policy loaded — kernel_lsm={result_lsm.get('kernel_lsm')}, snapshot_id={result_lsm['snapshot_id']}")

    # Promote to enforce
    log("Promoting LSM policy to enforce...")
    cr_lsm_promote = client.run_cr("[EBPF_POLICY] promote LSM", "promote_ebpf_policy",
                                   agent_asset_id,
                                   {"policy_type": "lsm", "asset_id": agent_asset_id})
    result_lsm_promote = client.get_cr_step_result(cr_lsm_promote)
    assert result_lsm_promote.get("prior_mode") == "audit", \
        f"Expected prior_mode=audit: {result_lsm_promote}"
    log("LSM policy in enforce mode")

    # Rollback promote → back to audit
    log("Rolling back promote (LSM → audit)...")
    client.post(f"/change-requests/{cr_lsm_promote['id']}/rollback", json={})
    deadline = _time.time() + 60
    while _time.time() < deadline:
        cr_state = client.get(f"/change-requests/{cr_lsm_promote['id']}")
        if cr_state.get("rollback_status") in ("completed", "rolled_back", "failed"):
            break
        _time.sleep(5)
    assert cr_state.get("rollback_status") in ("completed", "rolled_back"), \
        f"LSM promote rollback did not complete: {cr_state}"
    log("LSM promote rolled back ✓")

    # Rollback configure_ebpf_lsm → maps unloaded
    log("Rolling back configure_ebpf_lsm (unload maps)...")
    client.post(f"/change-requests/{cr_lsm['id']}/rollback", json={})
    deadline = _time.time() + 60
    while _time.time() < deadline:
        cr_state = client.get(f"/change-requests/{cr_lsm['id']}")
        if cr_state.get("rollback_status") in ("completed", "rolled_back", "failed"):
            break
        _time.sleep(5)
    assert cr_state.get("rollback_status") in ("completed", "rolled_back"), \
        f"LSM policy rollback did not complete: {cr_state}"
    log("LSM policy rolled back ✓")

    # ---- 5. Cleanup ----
    try:
        client.run_cr("[EBPF_POLICY] terminate instance", "ec2_terminate", cloud_account_id,
                      {"instance_id": instance_id})
        log(f"Instance {instance_id} terminated")
    except Exception as cleanup_e:
        log(f"Cleanup warning: {cleanup_e}")

    log("EBPF_POLICY PASSED ✓")
```

- [ ] **Step 4: Verify smoke file parses**

```
cd backend && python -c "import ast; ast.parse(open('tests/smoke/test_aws_live.py').read()); print('syntax OK')"
```

Expected: `syntax OK`

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat: add EBPF_POLICY smoke phase (network + LSM legs, full rollback)"
```

---

## Task 9: Run unit tests on EC2

- [ ] **Step 1: Push to remote**

```bash
git push origin master
```

- [ ] **Step 2: Pull on EC2 and run unit tests**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd nexplane && git pull --ff-only && docker exec nexplane-backend-1 python -m pytest tests/unit/test_ebpf_policy_plugins.py tests/unit/test_ebpf_executors.py -v 2>&1 | tail -30"
```

Expected: All 13 tests PASS (`test_ebpf_policy_plugins.py`: 9 tests, `test_ebpf_executors.py`: 4 tests).

- [ ] **Step 3: Run migration on EC2**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 alembic upgrade head 2>&1 | tail -10"
```

Expected: Migration `068_ebpf_policy_types` applies cleanly.

- [ ] **Step 4: Verify manifest includes new types**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python -c \"
from app.services.manifest_builder import get_manifest
types = [e['change_type'] for e in get_manifest()]
for t in ['ebpf_network_soak','configure_ebpf_network','configure_ebpf_lsm','promote_ebpf_policy']:
    assert t in types, f'{t} missing'
print('manifest OK:', [t for t in types if t.startswith('ebpf') or t.startswith('configure_ebpf') or t.startswith('promote_ebpf')])
\""
```

Expected: Prints the 4 new types.

---

## Agent-side specification (for agent repo implementation)

The backend dispatches these commands to the Nexplane agent via the existing job protocol. The agent must implement them. This section specifies the expected behavior.

### `ebpf_network_soak`

**Parameters:** `window_seconds` (int, default 60), `service_name` (string, optional filter)

**Behavior:**
1. Load pre-compiled TC+cgroup eBPF observer program
2. Capture outbound connection events for `window_seconds`
3. Unload observer
4. Return `{"flows": [{"dst_ip": str, "dst_port": int, "protocol": "tcp"|"udp", "process": str, "count": int}]}`

**Rollback:** N/A — no persistent state

### `ebpf_lsm_soak`

**Parameters:** `window_seconds` (int, default 60), `service_name` (string, optional filter)

**Behavior:**
1. Load pre-compiled eBPF LSM/kprobe observer program
2. Capture kernel events for `window_seconds`
3. Unload observer
4. Return `{"events": [{"syscall": str, "path": str, "process": str, "uid": int, "count": int}]}`

**Rollback:** N/A

### `configure_ebpf_network`

**Parameters:** `profile` (NetworkPolicy JSON), `service_name` (string), `action` (optional: `"restore"` for rollback path)

**Behavior (action != "restore"):**
1. Snapshot current BPF state at `/sys/fs/bpf/nexplane/<asset_id>/network/` as `snapshot_id`
2. Load pre-compiled network enforcer .bpf.o
3. Write `profile.rules` into allowlist BPF map
4. Write `"audit"` to mode map entry
5. Pin maps to `/sys/fs/bpf/nexplane/<asset_id>/network/`
6. Return `{"snapshot_id": str, "rules_loaded": int, "mode": "audit"}`

**Behavior (action == "restore"):**
1. Detach and unload network enforcer program
2. Remove pinned maps at `/sys/fs/bpf/nexplane/<asset_id>/network/`
3. Return `{"rolled_back": true}`

### `configure_ebpf_lsm`

Same structure as `configure_ebpf_network` but:
- Uses LSM hook (or kprobe fallback if `CONFIG_BPF_LSM` not available)
- Detect kernel capability: `cat /sys/kernel/security/lsm | grep -q bpf`
- Pin path: `/sys/fs/bpf/nexplane/<asset_id>/lsm/`
- Return includes `kernel_lsm: true|false`

### `promote_ebpf_policy`

**Parameters:** `policy_type` (`"network"` or `"lsm"`), `asset_id` (string), `mode` (optional, default `"enforce"`)

**Behavior:**
1. Determine pin path: `/sys/fs/bpf/nexplane/<asset_id>/<policy_type>/`
2. Read current mode from mode map → store as `prior_mode`
3. Write `mode` parameter value to mode map
4. Return `{"prior_mode": str, "current_mode": str}`
