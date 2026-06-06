# SP2 AppArmor Policy Auto-Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the soak/synthesize/CR pipeline to AppArmor by introducing a plugin registry (seccomp + apparmor plugins), refactoring `soak_service.py` to dispatch through the registry, adding the `apparmor_learn` agent command, fixing the existing `configure_apparmor` executor rollback bug, and running a passing APPARMOR_AUTOGEN smoke phase on Ubuntu 22.04.

**Architecture:** `PolicyPlugin` dataclass in `plugins/base.py` holds `learn_command`, `synthesize`, `cr_change_type`, `delta_extract`, and title/description templates. A registry dict in `plugins/__init__.py` provides `get_plugin(policy_type)`. `soak_service.py` does one `get_plugin` call per operation — no if/elif branches. The Go agent gains `apparmor_learn` in `linuxharden` (mirrors `seccomp_learn`). Two existing stubs (`configure_apparmor.py`, `configure_selinux.py`) are already in the backend but rollback is broken — fixed here. SELinux ChangeType and safety engine entry are also added as SP3 stubs.

**Tech Stack:** Python/FastAPI, SQLAlchemy async, Alembic, Go (agent), AppArmor userspace tools (`apparmor_parser`, `aa-status`, `aa-complain`, `aa-enforce`, auditd), Ubuntu 22.04 EC2

---

## File Map

| File | Status | Change |
|------|--------|--------|
| `backend/app/services/security_policy/plugins/__init__.py` | Create | Registry + `get_plugin()` |
| `backend/app/services/security_policy/plugins/base.py` | Create | `PolicyPlugin` dataclass |
| `backend/app/services/security_policy/plugins/seccomp.py` | Create | Seccomp plugin (migrated from synthesizer) |
| `backend/app/services/security_policy/plugins/apparmor.py` | Create | AppArmor plugin |
| `backend/app/services/security_policy/soak_service.py` | Modify | Dispatch through registry |
| `backend/app/services/security_policy/synthesizer.py` | Modify | `compute_delta` accepts `policy_type` |
| `backend/app/models/change_request.py` | Modify | Add `configure_apparmor`, `configure_selinux` to `ChangeType` |
| `backend/app/services/safety_engine.py` | Modify | Add both to `_IMPLICIT_ROLLBACK_TYPES` |
| `backend/app/connectors/executors/nexplane_agent/configure_apparmor.py` | Modify | Fix rollback bug (passes `snapshot_id` instead of `snapshot`) |
| `backend/app/connectors/executors/nexplane_agent/apparmor_learn.py` | Create | Backend executor for `apparmor_learn` agent command |
| `backend/alembic/versions/` | Create | Migration adding `configure_apparmor`, `configure_selinux` enum values |
| `agent/commands/linuxharden/apparmor_learn_linux.go` | Create | AppArmor AVC observation during soak window |
| `agent/commands/linuxharden/apparmor_learn_other.go` | Create | Non-linux stub |
| `agent/commands/linuxharden/linuxharden.go` | Modify | Export `ApparmorLearnExecute`, `ApparmorLearnRollback` |
| `agent/executor/executor.go` | Modify | Register `apparmor_learn` in commands and rollbacks maps |
| `backend/tests/smoke/test_aws_live.py` | Modify | Add `APPARMOR_AUTOGEN` phase |

---

## Task 1: Add `configure_apparmor` and `configure_selinux` to ChangeType + DB migration

**Files:**
- Modify: `backend/app/models/change_request.py:310-312`
- Modify: `backend/app/services/safety_engine.py:133-135`
- Create: `backend/alembic/versions/<hash>_add_apparmor_selinux_change_types.py`

- [ ] **Step 1: Add enum values to `change_request.py`**

Find the seccomp pipeline block (around line 310) and add after it:

```python
    # Seccomp pipeline — Phase SECCOMP_PIPELINE
    seccomp_learn = "seccomp_learn"
    configure_seccomp = "configure_seccomp"
    # AppArmor pipeline — SP2
    configure_apparmor = "configure_apparmor"
    # SELinux pipeline — SP3 (stub)
    configure_selinux = "configure_selinux"
```

- [ ] **Step 2: Add both to `_IMPLICIT_ROLLBACK_TYPES` in `safety_engine.py`**

Find the `configure_seccomp` line (around line 136) and extend it:

```python
    # Linux security policy — executors have built-in snapshot/restore rollback
    ChangeType.configure_seccomp,
    ChangeType.configure_apparmor,
    ChangeType.configure_selinux,
```

- [ ] **Step 3: Generate Alembic migration**

Run inside the backend container:
```bash
docker exec nexplane-backend-1 bash -c "cd /app && alembic revision --autogenerate -m 'add_apparmor_selinux_change_types'"
```

Open the generated file. If autogenerate produced an empty migration (enum changes often need manual SQL), replace the `upgrade()` body with:

```python
def upgrade() -> None:
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'configure_apparmor'")
    op.execute("ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'configure_selinux'")

def downgrade() -> None:
    pass  # Postgres enum values cannot be removed without recreation
```

- [ ] **Step 4: Run the migration**

```bash
docker exec nexplane-backend-1 bash -c "cd /app && alembic upgrade head"
```

Expected: `Running upgrade ... -> <hash>, add_apparmor_selinux_change_types`

- [ ] **Step 5: Verify enum values exist in DB**

```bash
docker exec nexplane-db-1 psql -U nexplane -d nexplane -c \
  "SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_enum.enumtypid = pg_type.oid WHERE pg_type.typname = 'change_type' AND enumlabel LIKE 'configure_%';"
```

Expected output includes: `configure_seccomp`, `configure_apparmor`, `configure_selinux`

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/change_request.py backend/app/services/safety_engine.py \
        backend/alembic/versions/
git commit -m "feat(security-policy): add configure_apparmor and configure_selinux ChangeType enum values"
```

---

## Task 2: Fix `configure_apparmor` executor rollback bug

**Files:**
- Modify: `backend/app/connectors/executors/nexplane_agent/configure_apparmor.py`

**Context:** The existing rollback passes `snapshot_id` (which doesn't exist in the execute result). The Go `apparmorRollbackOS` expects `params["snapshot"].(map[string]any)` — the full snapshot dict returned by `apparmorExecuteOS`. Fix: pass `snapshot` from `execution_result`.

- [ ] **Step 1: Read the existing file**

```bash
cat backend/app/connectors/executors/nexplane_agent/configure_apparmor.py
```

- [ ] **Step 2: Replace the rollback function**

```python
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="configure_apparmor",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=120,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await _dispatch.dispatch_agent_job(
        command="configure_apparmor",
        parameters={"snapshot": execution_result.get("snapshot", {})},
        asset_ids=asset_ids,
        timeout_seconds=120,
    )
```

- [ ] **Step 3: Run the existing rollback unit test to verify**

```bash
docker exec nexplane-backend-1 bash -c "cd /app && python -m pytest tests/unit/test_configure_seccomp_rollback.py -v 2>/dev/null || echo 'no apparmor rollback test yet'"
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/configure_apparmor.py
git commit -m "fix(executor): configure_apparmor rollback passes snapshot map not snapshot_id"
```

---

## Task 3: Plugin registry scaffold

**Files:**
- Create: `backend/app/services/security_policy/plugins/__init__.py`
- Create: `backend/app/services/security_policy/plugins/base.py`

- [ ] **Step 1: Create `base.py`**

```python
# backend/app/services/security_policy/plugins/base.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable

from app.models.change_request import ChangeType


@dataclass
class PolicyPlugin:
    policy_type: str
    learn_command: str
    synthesize: Callable[[dict], dict]
    cr_change_type: ChangeType
    delta_extract: Callable[[dict], set]
    cr_title_template: str        # receives {service_name}
    cr_description_template: str  # receives {rule_count}, {partial}
```

- [ ] **Step 2: Create `plugins/__init__.py` — empty registry, filled in later tasks**

```python
# backend/app/services/security_policy/plugins/__init__.py
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.security_policy.plugins.base import PolicyPlugin

_REGISTRY: dict[str, "PolicyPlugin"] = {}


def _register(plugin: "PolicyPlugin") -> None:
    _REGISTRY[plugin.policy_type] = plugin


def get_plugin(policy_type: str) -> "PolicyPlugin":
    plugin = _REGISTRY.get(policy_type)
    if plugin is None:
        raise ValueError(f"Unsupported policy_type: {policy_type!r}. Known: {sorted(_REGISTRY)}")
    return plugin
```

- [ ] **Step 3: Verify Python syntax**

```bash
docker exec nexplane-backend-1 python -c "from app.services.security_policy.plugins import get_plugin; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/security_policy/plugins/
git commit -m "feat(security-policy): add policy plugin registry scaffold"
```

---

## Task 4: Seccomp plugin (migrate synthesize_seccomp into registry)

**Files:**
- Create: `backend/app/services/security_policy/plugins/seccomp.py`
- Modify: `backend/app/services/security_policy/plugins/__init__.py`

- [ ] **Step 1: Create `seccomp.py`**

```python
# backend/app/services/security_policy/plugins/seccomp.py
from app.models.change_request import ChangeType
from app.services.security_policy.plugins.base import PolicyPlugin


def _synthesize(raw_observations: dict) -> dict:
    all_syscalls: set[str] = set()
    for syscalls in raw_observations.values():
        if syscalls:
            all_syscalls.update(syscalls)
    return {
        "defaultAction": "SCMP_ACT_ERRNO",
        "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
        "syscalls": [{"names": sorted(all_syscalls), "action": "SCMP_ACT_ALLOW"}],
    }


def _delta_extract(profile: dict) -> set:
    syscalls = profile.get("syscalls", [])
    if not syscalls:
        return set()
    return set(syscalls[0].get("names", []))


SECCOMP_PLUGIN = PolicyPlugin(
    policy_type="seccomp",
    learn_command="seccomp_learn",
    synthesize=_synthesize,
    cr_change_type=ChangeType.configure_seccomp,
    delta_extract=_delta_extract,
    cr_title_template="Apply seccomp profile — {service_name}",
    cr_description_template=(
        "Seccomp profile synthesized from soak session. "
        "Syscalls allowed: {rule_count}. Partial observation: {partial}."
    ),
)
```

- [ ] **Step 2: Register seccomp plugin in `__init__.py`**

```python
# backend/app/services/security_policy/plugins/__init__.py
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.security_policy.plugins.base import PolicyPlugin

_REGISTRY: dict[str, "PolicyPlugin"] = {}


def _register(plugin: "PolicyPlugin") -> None:
    _REGISTRY[plugin.policy_type] = plugin


def get_plugin(policy_type: str) -> "PolicyPlugin":
    plugin = _REGISTRY.get(policy_type)
    if plugin is None:
        raise ValueError(f"Unsupported policy_type: {policy_type!r}. Known: {sorted(_REGISTRY)}")
    return plugin


# Register built-in plugins
from app.services.security_policy.plugins.seccomp import SECCOMP_PLUGIN  # noqa: E402
_register(SECCOMP_PLUGIN)
# apparmor registered in apparmor.py (Task 5)
# selinux: SP3
# network_policy: SP4
```

- [ ] **Step 3: Verify registry finds seccomp**

```bash
docker exec nexplane-backend-1 python -c "
from app.services.security_policy.plugins import get_plugin
p = get_plugin('seccomp')
print(p.policy_type, p.learn_command, p.cr_change_type)
"
```

Expected: `seccomp seccomp_learn ChangeType.configure_seccomp`

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/security_policy/plugins/
git commit -m "feat(security-policy): add seccomp PolicyPlugin, register in registry"
```

---

## Task 5: AppArmor synthesizer plugin

**Files:**
- Create: `backend/app/services/security_policy/plugins/apparmor.py`
- Modify: `backend/app/services/security_policy/plugins/__init__.py`

AppArmor events from the agent look like:
```json
{"apparmor_events": [
  {"operation": "file_read",  "resource": "/etc/nginx/nginx.conf"},
  {"operation": "file_write", "resource": "/var/log/nginx/access.log"},
  {"operation": "capability", "resource": "net_bind_service"},
  {"operation": "network",    "resource": "tcp"}
]}
```

`raw_observations` maps `asset_id → list of event dicts`.

- [ ] **Step 1: Create `apparmor.py`**

```python
# backend/app/services/security_policy/plugins/apparmor.py
from __future__ import annotations
import re
from collections import defaultdict

from app.models.change_request import ChangeType
from app.services.security_policy.plugins.base import PolicyPlugin

_OP_TO_MASK = {
    "file_read": "r",
    "file_write": "w",
    "file_exec": "ix",
    "file_append": "a",
    "file_link": "l",
}

_GLOB_RULES = [
    (re.compile(r"^(/var/log/[^/]+)/.*"), r"\1/**"),
    (re.compile(r"^(/etc/[^/]+)/.*"),     r"\1/**"),
    (re.compile(r"^(/var/lib/[^/]+)/.*"), r"\1/**"),
    (re.compile(r"^(/run/[^/]+)/.*"),     r"\1/*"),
    (re.compile(r"^/tmp/.*"),             "/tmp/**"),
]


def _glob_path(path: str) -> str:
    for pattern, replacement in _GLOB_RULES:
        if pattern.match(path):
            return pattern.sub(replacement, path)
    return path


def _synthesize(raw_observations: dict) -> dict:
    file_rules: dict[str, set[str]] = defaultdict(set)
    capabilities: set[str] = set()
    network_protos: set[str] = set()

    for events in raw_observations.values():
        if not events:
            continue
        for event in events:
            op = event.get("operation", "")
            resource = event.get("resource", "")
            if op in _OP_TO_MASK:
                file_rules[_glob_path(resource)].add(_OP_TO_MASK[op])
            elif op == "capability":
                capabilities.add(resource)
            elif op == "network":
                network_protos.add(resource)

    lines = [
        "#include <tunables/global>",
        "",
        "/usr/sbin/{service_name} {",
        "  #include <abstractions/base>",
        "",
    ]
    for cap in sorted(capabilities):
        lines.append(f"  capability {cap},")
    if capabilities:
        lines.append("")
    for proto in sorted(network_protos):
        lines.append(f"  network {proto},")
    if network_protos:
        lines.append("")
    for path in sorted(file_rules):
        mask = "".join(sorted(file_rules[path]))
        lines.append(f"  {path} {mask},")
    lines.append("}")

    return {
        "profile_name": "nexplane-{service_name}",  # executor substitutes service_name
        "mode": "complain",
        "profile_text": "\n".join(lines),
    }


def _delta_extract(profile: dict) -> set:
    text = profile.get("profile_text", "")
    rules: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip().rstrip(",")
        if (
            stripped
            and not stripped.startswith("#")
            and stripped not in ("{", "}")
            and not stripped.startswith("/usr/sbin/")
        ):
            rules.add(stripped)
    return rules


APPARMOR_PLUGIN = PolicyPlugin(
    policy_type="apparmor",
    learn_command="apparmor_learn",
    synthesize=_synthesize,
    cr_change_type=ChangeType.configure_apparmor,
    delta_extract=_delta_extract,
    cr_title_template="Apply AppArmor profile — {service_name}",
    cr_description_template=(
        "AppArmor profile synthesized from soak session. "
        "Rules: {rule_count}. Partial observation: {partial}."
    ),
)
```

- [ ] **Step 2: Register apparmor plugin at the bottom of `plugins/__init__.py`**

Add after the seccomp registration line:

```python
from app.services.security_policy.plugins.apparmor import APPARMOR_PLUGIN  # noqa: E402
_register(APPARMOR_PLUGIN)
```

- [ ] **Step 3: Write unit test for synthesizer**

Create `backend/tests/unit/test_apparmor_plugin.py`:

```python
"""Unit tests for AppArmor synthesizer plugin."""
import pytest
from app.services.security_policy.plugins.apparmor import _synthesize, _delta_extract


def test_synthesize_empty_observations():
    profile = _synthesize({})
    assert "profile_text" in profile
    assert profile["mode"] == "complain"
    assert "{service_name}" in profile["profile_name"]


def test_synthesize_file_and_capability():
    obs = {
        "asset-1": [
            {"operation": "file_read",  "resource": "/etc/nginx/nginx.conf"},
            {"operation": "file_write", "resource": "/var/log/nginx/access.log"},
            {"operation": "capability", "resource": "net_bind_service"},
            {"operation": "network",    "resource": "tcp"},
        ]
    }
    profile = _synthesize(obs)
    text = profile["profile_text"]
    assert "capability net_bind_service," in text
    assert "network tcp," in text
    assert "/etc/nginx/**" in text   # glob applied
    assert "/var/log/nginx/**" in text


def test_delta_extract_roundtrip():
    obs = {"a": [{"operation": "capability", "resource": "net_raw"}]}
    profile = _synthesize(obs)
    rules = _delta_extract(profile)
    assert "capability net_raw" in rules


def test_delta_empty_profile():
    assert _delta_extract({}) == set()
```

- [ ] **Step 4: Run tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/unit/test_apparmor_plugin.py -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/security_policy/plugins/ backend/tests/unit/test_apparmor_plugin.py
git commit -m "feat(security-policy): add AppArmor synthesizer plugin with unit tests"
```

---

## Task 6: Refactor `soak_service.py` to dispatch through plugin registry

**Files:**
- Modify: `backend/app/services/security_policy/soak_service.py`
- Modify: `backend/app/services/security_policy/synthesizer.py`

**Context:** Three spots in `soak_service.py` hard-code seccomp:
1. `_collect_observations` uses `command="seccomp_learn"` and `.get("syscalls_seen")`
2. `stop_and_synthesize` calls `synthesize_seccomp(observations)`
3. `_create_configure_seccomp_cr` uses `ChangeType.configure_seccomp` and seccomp-specific title/description

All three must become plugin lookups. `synthesizer.compute_delta` must accept `policy_type`.

- [ ] **Step 1: Read `soak_service.py` in full before editing**

```bash
cat backend/app/services/security_policy/soak_service.py
```

- [ ] **Step 2: Replace `_collect_observations` to use plugin**

New signature adds `policy_type`:

```python
async def _collect_observations(
    asset_ids: list[str],
    window_seconds: int,
    policy_type: str,
) -> tuple[dict[str, list], bool]:
    """Run the policy-specific learn command on each asset concurrently."""
    from app.services.security_policy.plugins import get_plugin
    plugin = get_plugin(policy_type)
    partial = False

    async def _observe_one(asset_id: str) -> tuple[str, list | None]:
        try:
            result = await _dispatch.dispatch_agent_job(
                command=plugin.learn_command,
                parameters={"duration_seconds": window_seconds},
                asset_ids=[asset_id],
                timeout_seconds=window_seconds + 60,
            )
            # seccomp returns syscalls_seen; apparmor returns apparmor_events
            observations = result.get("syscalls_seen") or result.get("apparmor_events") or []
            return asset_id, observations
        except Exception:
            return asset_id, None

    results = await asyncio.gather(*[_observe_one(aid) for aid in asset_ids])
    observations: dict[str, list] = {}
    for asset_id, data in results:
        if data is None:
            partial = True
        else:
            observations[asset_id] = data
    return observations, partial
```

- [ ] **Step 3: Replace `stop_and_synthesize` to use plugin.synthesize**

Replace the line `profile = synthesize_seccomp(observations)` with:

```python
    from app.services.security_policy.plugins import get_plugin
    plugin = get_plugin(session.policy_type)
    profile = plugin.synthesize(observations)
```

And update the `_collect_observations` call to pass `policy_type=session.policy_type`.

And update the `compute_delta` call:
```python
    delta = compute_delta(baseline.profile, profile, policy_type=session.policy_type)
```

And replace the call to `_create_configure_seccomp_cr` with `_create_policy_cr` (renamed in next step):
```python
    if should_auto_propose(baseline):
        cr = await _create_policy_cr(db, session, profile, service_name, user_id)
```

- [ ] **Step 4: Rename `_create_configure_seccomp_cr` → `_create_policy_cr` and parameterize via plugin**

```python
async def _create_policy_cr(
    db: AsyncSession,
    session: SecurityPolicySoakSession,
    profile: dict,
    service_name: str,
    user_id: uuid.UUID,
) -> ChangeRequest:
    from app.services.security_policy.plugins import get_plugin
    plugin = get_plugin(session.policy_type)
    params = _build_cr_params(session, profile, service_name)
    rule_count = len(plugin.delta_extract(profile))
    cr = ChangeRequest(
        organization_id=session.organization_id,
        requester_id=user_id,
        title=plugin.cr_title_template.format(service_name=service_name),
        description=plugin.cr_description_template.format(
            rule_count=rule_count, partial=session.partial
        ),
        change_type=plugin.cr_change_type,
        target_asset_ids=[str(a) for a in session.asset_ids],
        desired_outcome=params,
        status=ChangeRequestStatus.draft,
        risk_level=RiskLevel.medium,
    )
    db.add(cr)
    await db.flush()
    return cr
```

Also update the `accept_diff` call site:
```python
    cr = await _create_policy_cr(db, session, session.synthesized_profile, service_name, user_id)
```

- [ ] **Step 5: Update imports in `soak_service.py`**

Remove `from app.services.security_policy.synthesizer import compute_delta, synthesize_seccomp`.
Add `from app.services.security_policy.synthesizer import compute_delta`.
The plugin imports are inline to avoid circular imports.

- [ ] **Step 6: Update `synthesizer.py` — make `compute_delta` policy-aware**

```python
# backend/app/services/security_policy/synthesizer.py
"""Security policy synthesizer — delegates to policy plugins."""


def synthesize_seccomp(raw_observations: dict[str, list[str]]) -> dict:
    """Kept for backward compatibility. Use plugin.synthesize() for new code."""
    from app.services.security_policy.plugins.seccomp import SECCOMP_PLUGIN
    return SECCOMP_PLUGIN.synthesize(raw_observations)


def compute_delta(prior: dict, current: dict, policy_type: str = "seccomp") -> dict:
    """Return {added, removed} rule sets between two profiles."""
    from app.services.security_policy.plugins import get_plugin
    plugin = get_plugin(policy_type)
    prior_set = plugin.delta_extract(prior)
    current_set = plugin.delta_extract(current)
    return {
        "added": sorted(current_set - prior_set),
        "removed": sorted(prior_set - current_set),
    }
```

- [ ] **Step 7: Verify no stale seccomp references remain in `soak_service.py`**

```bash
grep -n "seccomp_learn\|synthesize_seccomp\|_create_configure_seccomp_cr\|ChangeType\.configure_seccomp" \
  backend/app/services/security_policy/soak_service.py
```

Expected: no output.

- [ ] **Step 8: Run the backend to verify it starts cleanly**

```bash
docker restart nexplane-backend-1 && sleep 6 && \
  docker exec nexplane-backend-1 curl -sf http://localhost:8000/health
```

Expected: `{"status":"ok","service":"nexplane"}`

- [ ] **Step 9: Run the existing SECCOMP_AUTOGEN smoke phase to confirm seccomp still works**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "nohup docker exec nexplane-backend-1 stdbuf -oL python -u \
   /app/tests/smoke/test_aws_live.py --phases SECCOMP_AUTOGEN \
   --backend-tailscale-ip 100.101.186.39 > /tmp/seccomp_regression.log 2>&1 &"
# Wait and tail
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "tail -f /tmp/seccomp_regression.log"
```

Expected last line: `✅ ALL SELECTED PHASES PASSED`

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/security_policy/soak_service.py \
        backend/app/services/security_policy/synthesizer.py
git commit -m "refactor(security-policy): dispatch through plugin registry, no policy-type branching in soak_service"
```

---

## Task 7: `apparmor_learn` backend executor

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/apparmor_learn.py`

- [ ] **Step 1: Create `apparmor_learn.py`**

```python
# backend/app/connectors/executors/nexplane_agent/apparmor_learn.py
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="apparmor_learn",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=int(parameters.get("duration_seconds", 60)) + 60,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "apparmor_learn creates no persistent state"}
```

- [ ] **Step 2: Verify it imports cleanly**

```bash
docker exec nexplane-backend-1 python -c \
  "from app.connectors.executors.nexplane_agent import apparmor_learn; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/apparmor_learn.py
git commit -m "feat(executor): add apparmor_learn backend executor"
```

---

## Task 8: Go agent — `apparmor_learn` command

**Files:**
- Create: `agent/commands/linuxharden/apparmor_learn_linux.go`
- Create: `agent/commands/linuxharden/apparmor_learn_other.go`
- Modify: `agent/commands/linuxharden/linuxharden.go`
- Modify: `agent/executor/executor.go`

**Context:** Mirror `seccomp_learn_linux.go`. The command loads a temporary complain-mode profile, waits the observation window, parses `/var/log/audit/audit.log` for AppArmor `ALLOWED` AVC entries, cleans up the temp profile, and returns `{apparmor_events: [...]}`.

AVC log line format:
```
type=AVC msg=audit(...): apparmor="ALLOWED" operation="file_read" profile="nexplane-learn-nginx" name="/etc/nginx/nginx.conf" pid=123 comm="nginx" requested_mask="r"
type=AVC msg=audit(...): apparmor="ALLOWED" operation="capable" profile="nexplane-learn-nginx" capname="net_bind_service"
type=AVC msg=audit(...): apparmor="ALLOWED" operation="connect" profile="nexplane-learn-nginx" family="inet" sock_type="stream"
```

- [ ] **Step 1: Read `seccomp_learn_linux.go` and `linuxharden.go` before writing**

```bash
cat agent/commands/linuxharden/seccomp_learn_linux.go
cat agent/commands/linuxharden/linuxharden.go
```

- [ ] **Step 2: Create `apparmor_learn_linux.go`**

```go
//go:build linux

package linuxharden

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func apparmorLearnExecute(params map[string]any) (map[string]any, error) {
	duration, _ := params["duration_seconds"].(float64)
	if duration <= 0 {
		duration = 60
	}
	serviceName, _ := params["service_name"].(string)
	if serviceName == "" {
		serviceName = "nexplane-learn"
	}

	if _, err := exec.LookPath("aa-status"); err != nil {
		return nil, fmt.Errorf("AppArmor not available: aa-status not found in PATH")
	}

	profileName := "nexplane-learn-" + serviceName
	profilePath := filepath.Join("/etc/apparmor.d", profileName)
	complain := fmt.Sprintf(`#include <tunables/global>
profile %s flags=(complain) {
  #include <abstractions/base>
  /** mrwklix,
  capability,
  network,
}
`, profileName)

	if err := os.WriteFile(profilePath, []byte(complain), 0644); err != nil {
		return nil, fmt.Errorf("writing learn profile: %w", err)
	}
	if out, err := exec.Command("apparmor_parser", "-r", profilePath).CombinedOutput(); err != nil {
		os.Remove(profilePath)
		return nil, fmt.Errorf("loading learn profile: %s: %w", out, err)
	}

	// Record audit log offset before observation
	logPath := "/var/log/audit/audit.log"
	if _, err := os.Stat(logPath); err != nil {
		logPath = "/var/log/syslog"
	}
	startOffset := int64(0)
	if info, err := os.Stat(logPath); err == nil {
		startOffset = info.Size()
	}

	time.Sleep(time.Duration(duration) * time.Second)

	// Parse new AVC lines since startOffset
	events := parseApparmorEvents(logPath, startOffset, profileName)

	// Cleanup temp profile
	exec.Command("apparmor_parser", "-R", profilePath).Run() //nolint:errcheck
	os.Remove(profilePath)

	return map[string]any{
		"action":           "apparmor_learn",
		"service_name":     serviceName,
		"duration_seconds": int(duration),
		"apparmor_events":  events,
		"event_count":      len(events),
	}, nil
}

func parseApparmorEvents(logPath string, startOffset int64, profileName string) []map[string]any {
	f, err := os.Open(logPath)
	if err != nil {
		return nil
	}
	defer f.Close()
	f.Seek(startOffset, 0) //nolint:errcheck

	seen := map[string]bool{}
	var events []map[string]any
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.Contains(line, `apparmor="ALLOWED"`) {
			continue
		}
		if !strings.Contains(line, profileName) {
			continue
		}
		event := parseAVCLine(line)
		if event == nil {
			continue
		}
		key := event["operation"].(string) + ":" + event["resource"].(string)
		if !seen[key] {
			seen[key] = true
			events = append(events, event)
		}
	}
	return events
}

func parseAVCLine(line string) map[string]any {
	fields := map[string]string{}
	for _, token := range strings.Fields(line) {
		if idx := strings.IndexByte(token, '='); idx > 0 {
			key := token[:idx]
			val := strings.Trim(token[idx+1:], `"`)
			fields[key] = val
		}
	}
	op := fields["operation"]
	if op == "" {
		return nil
	}

	resource := ""
	switch op {
	case "file_read", "file_write", "file_exec", "file_append", "file_link":
		resource = fields["name"]
	case "capable":
		op = "capability"
		resource = fields["capname"]
	case "connect", "bind", "accept", "listen", "sendmsg", "recvmsg":
		op = "network"
		proto := fields["family"]
		if proto == "" {
			proto = "inet"
		}
		resource = proto
	default:
		return nil
	}

	if resource == "" {
		return nil
	}
	return map[string]any{"operation": op, "resource": resource}
}

func apparmorLearnRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "apparmor_learn_rollback", "status": "no_state_to_revert"}, nil
}
```

- [ ] **Step 3: Create `apparmor_learn_other.go` (non-Linux stub)**

```go
//go:build !linux

package linuxharden

import "fmt"

func apparmorLearnExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apparmor_learn is only supported on Linux")
}

func apparmorLearnRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"status": "no_state_to_revert"}, nil
}
```

- [ ] **Step 4: Export from `linuxharden.go`**

Add two lines after `SeccompLearnRollback`:

```go
func ApparmorLearnExecute(params map[string]any) (map[string]any, error) { return apparmorLearnExecute(params) }
func ApparmorLearnRollback(params map[string]any) (map[string]any, error) { return apparmorLearnRollback(params) }
```

- [ ] **Step 5: Register in `executor.go`**

In the `commands` map after `"seccomp_learn"`:
```go
"apparmor_learn":                linuxharden.ApparmorLearnExecute,
```

In the `rollbacks` map after `"seccomp_learn"`:
```go
"apparmor_learn":                linuxharden.ApparmorLearnRollback,
```

- [ ] **Step 6: Build agent**

```bash
cd agent && go build ./... 2>&1
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add agent/commands/linuxharden/apparmor_learn_linux.go \
        agent/commands/linuxharden/apparmor_learn_other.go \
        agent/commands/linuxharden/linuxharden.go \
        agent/executor/executor.go
git commit -m "feat(agent): add apparmor_learn command — AVC log observation for AppArmor policy synthesis"
```

---

## Task 9: APPARMOR_AUTOGEN smoke phase

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

**Context:** Add `run_phase_apparmor_autogen()` and wire it into `main()`. The phase provisions a Ubuntu 22.04 EC2 (AppArmor pre-installed), installs auditd + nginx, runs the full soak→CR pipeline in complain mode, then rollback, then enforce mode, then rollback, then second soak→delta→accept. Terminate instance at end.

The Ubuntu 22.04 canonical AMI owner is `099720109477`. Use a filter on the name to get the latest.

Read the SECCOMP_AUTOGEN phase (`run_phase_seccomp_autogen`, around line 21440) before writing — it is the direct template.

- [ ] **Step 1: Read `run_phase_seccomp_autogen` in full (lines 21440–21710)**

```bash
docker exec nexplane-backend-1 grep -n "def run_phase_seccomp_autogen\|def run_phase_apparmor" \
  /app/tests/smoke/test_aws_live.py
```

Then read the function body.

- [ ] **Step 2: Add Ubuntu AMI lookup helper at top of new phase function**

```python
def _get_ubuntu_2204_ami(cloud_account_id: str, client) -> str:
    """Return the latest Ubuntu 22.04 LTS HVM x86_64 AMI ID in us-east-1."""
    import boto3
    ec2 = boto3.client("ec2", region_name="us-east-1")
    images = ec2.describe_images(
        Owners=["099720109477"],
        Filters=[
            {"Name": "name",         "Values": ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]},
            {"Name": "architecture", "Values": ["x86_64"]},
            {"Name": "state",        "Values": ["available"]},
        ],
    )
    if not images["Images"]:
        raise RuntimeError("No Ubuntu 22.04 AMI found in us-east-1")
    return sorted(images["Images"], key=lambda x: x["CreationDate"])[-1]["ImageId"]
```

- [ ] **Step 3: Write `run_phase_apparmor_autogen()`**

Structure (mirror SECCOMP_AUTOGEN exactly, replacing seccomp-specific parts):

```python
def run_phase_apparmor_autogen(client, base_url, cloud_account_id=None,
                               tailscale_auth_key="", backend_tailscale_ip=""):
    import time as _time
    import boto3

    tag = int(_time.time())
    log = lambda msg: print(f"  [APPARMOR_AUTOGEN] {msg}", flush=True)
    log("Starting APPARMOR_AUTOGEN smoke phase")

    if not cloud_account_id:
        cloud_account_id = client.get_cloud_account_asset_id()
    log(f"Using cloud account {cloud_account_id}")

    # ---- 1. Ubuntu AMI lookup ----
    ami_id = _get_ubuntu_2204_ami(cloud_account_id, client)
    log(f"Ubuntu 22.04 AMI: {ami_id}")

    # ---- 2. Key pair ----
    key_name = f"nexplane-smoke-apparmor-key-{tag}"
    log(f"Creating key pair {key_name}...")
    client.run_cr(
        f"[APPARMOR_AUTOGEN] create key pair", "key_pair_create", cloud_account_id,
        {"key_name": key_name, "rollback_strategy": "rollback_unavailable"},
    )

    # ---- 3. Launch Ubuntu EC2 ----
    instance_name = f"nexplane-smoke-apparmor-nginx-{tag}"
    log(f"Launching EC2 instance {instance_name} (Ubuntu 22.04, t3.micro)...")
    cr_result = client.run_cr(
        f"[APPARMOR_AUTOGEN] launch EC2", "ec2_launch", cloud_account_id,
        {
            "instance_name": instance_name,
            "ami_id": ami_id,
            "instance_type": "t3.micro",
            "key_name": key_name,
            "rollback_strategy": "rollback_unavailable",
        },
    )
    instance_id = cr_result.get("instance_id") or cr_result.get("step_results", {}).get("instance_id", "")

    # Poll for inventory
    instance_asset = None
    for _ in range(48):
        _time.sleep(5)
        assets = client.get("/assets", params={"asset_type": "server"})
        for a in assets:
            if instance_id and instance_id in (a.get("metadata") or {}).get("instance_id", ""):
                instance_asset = a
                break
        if instance_asset:
            break
    assert instance_asset, f"Instance {instance_id} not found in inventory after 4min"
    instance_asset_id = instance_asset["id"]
    log(f"Instance {instance_id} in inventory as asset {instance_asset_id}")

    # ---- 4. Wait for SSM + Tailscale + agent ----
    log("Waiting 3min for SSM agent to become available...")
    _time.sleep(180)

    tailscale_auth_key = client.get_tailscale_auth_key(tailscale_auth_key)
    client.run_cr(
        f"[APPARMOR_AUTOGEN] tailscale join", "tailscale_join", instance_asset_id,
        {"instance_id": instance_id, "auth_key": tailscale_auth_key,
         "document_name": "AWS-RunShellScript", "rollback_strategy": "rollback_unavailable"},
    )
    client.run_cr(
        f"[APPARMOR_AUTOGEN] deploy nexplane agent", "deploy_nexplane_agent", cloud_account_id,
        {"instance_id": instance_id, "backend_url": f"http://{backend_tailscale_ip}:8000",
         "rollback_strategy": "rollback_unavailable"},
    )

    # Poll for agent registration
    agent_asset_id = None
    for _ in range(60):
        _time.sleep(5)
        assets = client.get("/assets", params={"asset_type": "server"})
        for a in assets:
            if a.get("id") == instance_asset_id and a.get("agent_id"):
                agent_asset_id = instance_asset_id
                break
        if agent_asset_id:
            break
    assert agent_asset_id, "Agent did not register within 5min"
    log(f"Agent registered on asset {agent_asset_id}")

    # ---- 5. Install auditd + nginx on Ubuntu ----
    log("Installing auditd and nginx...")
    client.run_cr(
        "[APPARMOR_AUTOGEN] install nginx", "ssm_command", instance_asset_id,
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": (
             "export DEBIAN_FRONTEND=noninteractive && "
             "apt-get update -qq && "
             "apt-get install -y auditd apparmor-utils nginx && "
             "systemctl enable auditd nginx && "
             "systemctl start auditd nginx && "
             "nginx -t && echo nginx_ok"
         ),
         "rollback_strategy": "rollback_unavailable"},
    )
    log("nginx and auditd installed ✓")

    # ---- 6. Start traffic generator ----
    log("Starting HTTP traffic generator (background, 150s)...")
    client.run_cr(
        "[APPARMOR_AUTOGEN] start traffic generator", "ssm_command", instance_asset_id,
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": (
             "nohup bash -c 'for i in $(seq 1 1500); do "
             "curl -s http://localhost/ > /dev/null; "
             "curl -s http://localhost/nonexistent > /dev/null; "
             "sleep 0.1; done' &>/tmp/traffic-gen.log &"
         ),
         "rollback_strategy": "rollback_unavailable"},
    )
    log("Traffic generator started ✓")

    # ---- 7. Get project ----
    projects = client.get("/projects")
    project_id = next(
        (p["id"] for p in projects if "Microsegmentation" in p.get("name", "")),
        projects[0]["id"] if projects else None,
    )
    assert project_id, "No project found"
    log(f"Using project {project_id}")

    # ---- 8. Clear any prior apparmor baseline ----
    try:
        client.delete(f"/security-policy/baselines/{project_id}?policy_type=apparmor")
        log("Pre-run: cleared any existing apparmor baseline ✓")
    except Exception as _e:
        log(f"Pre-run baseline cleanup (non-fatal): {_e}")

    # ---- 9. First soak session (complain mode) ----
    session = client.post("/security-policy/soak-sessions", json={
        "project_id": project_id,
        "policy_type": "apparmor",
        "asset_ids": [agent_asset_id],
        "window_seconds": 60,
    })
    session_id = session["id"]
    log(f"Session {session_id} started, status={session['status']}, window=60s")

    log("Waiting 65s for observation window...")
    _time.sleep(65)

    log("Stopping session and synthesizing profile...")
    session = client.post(f"/security-policy/soak-sessions/{session_id}/stop",
                          json={"service_name": "nginx"})
    assert session["status"] == "cr_proposed", (
        f"Expected cr_proposed (no prior baseline), got {session['status']}"
    )
    cr_id = session["cr_id"]
    cr = client.get(f"/change-requests/{cr_id}")
    assert cr["change_type"] == "configure_apparmor", f"Wrong change_type: {cr['change_type']}"
    log(f"Profile synthesized, CR proposed: {cr_id} (change_type=configure_apparmor) ✓")

    baseline = client.get(f"/security-policy/baselines/{project_id}",
                          params={"policy_type": "apparmor"})
    assert "profile_text" in baseline["profile"], "Baseline missing profile_text"
    log(f"Baseline stored ✓")

    # ---- 10. Plan → approve → execute CR (complain mode) ----
    client.post(f"/change-requests/{cr_id}/plan")
    client.post(f"/change-requests/{cr_id}/submit-for-approval")
    client.post(f"/change-requests/{cr_id}/approve",
                json={"decision": "approved", "comment": "apparmor_autogen smoke"})
    log("CR submitted and approved ✓")

    client.post(f"/change-requests/{cr_id}/execute")
    for _ in range(30):
        _time.sleep(5)
        cr = client.get(f"/change-requests/{cr_id}")
        if cr["status"] in ("completed", "failed", "rolled_back"):
            break
    assert cr["status"] == "completed", f"CR did not complete: {cr['status']}"
    log("CR executed — AppArmor profile loaded in complain mode ✓")

    # Verify nginx still responds and profile is loaded
    client.run_cr(
        "[APPARMOR_AUTOGEN] verify nginx post-apparmor", "ssm_command", instance_asset_id,
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "curl -sf http://localhost/ > /dev/null && aa-status | grep nexplane && echo aa_ok",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("nginx responding and AppArmor profile active ✓")

    # ---- 11. Rollback ----
    client.post(f"/change-requests/{cr_id}/rollback")
    for _ in range(30):
        _time.sleep(5)
        cr = client.get(f"/change-requests/{cr_id}")
        if cr["status"] in ("rolled_back", "failed"):
            break
    assert cr["status"] == "rolled_back", f"CR rollback failed: {cr['status']}"
    log("CR rolled back ✓")

    client.run_cr(
        "[APPARMOR_AUTOGEN] verify nginx post-rollback", "ssm_command", instance_asset_id,
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "curl -sf http://localhost/ > /dev/null && echo nginx_ok_post_rollback",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("nginx responding correctly after rollback ✓")

    # ---- 12. Second soak → delta review flow ----
    log("Starting second soak session to test baseline-delta flow...")
    session2 = client.post("/security-policy/soak-sessions", json={
        "project_id": project_id,
        "policy_type": "apparmor",
        "asset_ids": [agent_asset_id],
        "window_seconds": 60,
    })
    session2_id = session2["id"]
    log("Waiting 65s for second observation window...")
    _time.sleep(65)

    session2 = client.post(f"/security-policy/soak-sessions/{session2_id}/stop",
                           json={"service_name": "nginx"})
    assert session2["status"] == "synthesized", (
        f"Expected synthesized (baseline exists), got {session2['status']}"
    )
    assert session2["baseline_delta"] is not None, "Expected baseline_delta on second run"
    delta = session2["baseline_delta"]
    assert "added" in delta and "removed" in delta
    log(f"Delta computed: +{len(delta['added'])} added, -{len(delta['removed'])} removed rules ✓")

    session2 = client.post(f"/security-policy/soak-sessions/{session2_id}/accept",
                           json={"service_name": "nginx"})
    assert session2["status"] == "cr_proposed"
    assert session2["cr_id"] is not None
    log(f"Second CR proposed after operator accept: {session2['cr_id']} ✓")

    # ---- 13. Terminate instance ----
    log("Terminating EC2 instance...")
    try:
        client.run_cr(
            "[APPARMOR_AUTOGEN] terminate instance", "ec2_terminate", cloud_account_id,
            {"instance_id": instance_id},
        )
        log("Instance terminated ✓")
    except Exception as e:
        log(f"Terminate warning (non-fatal): {e}")

    log("APPARMOR_AUTOGEN PASSED ✓")
    return {"status": "passed", "session_id": session_id, "cr_id": cr_id}
```

- [ ] **Step 4: Wire into `main()`**

Find the SECCOMP_AUTOGEN block in `main()` (around line 16226) and add after it:

```python
        if "APPARMOR_AUTOGEN" in phases:
            run_phase_apparmor_autogen(client, base_url=args.base_url,
                                       cloud_account_id=cloud_account_id,
                                       tailscale_auth_key=args.tailscale_auth_key,
                                       backend_tailscale_ip=getattr(args, "backend_tailscale_ip", ""))
```

- [ ] **Step 5: Add `APPARMOR_AUTOGEN` to the `--phases` help string**

Find the `"SECCOMP_AUTOGEN=..."` entry in the help string and add after it:

```
"APPARMOR_AUTOGEN=AppArmor policy auto-generation: Ubuntu 22.04 EC2, soak→complain→rollback→delta flow. "
```

- [ ] **Step 6: Push to GitHub and pull on EC2**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add APPARMOR_AUTOGEN phase — Ubuntu 22.04, complain mode, delta flow"
git push origin master
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && git pull && docker restart nexplane-backend-1 && sleep 8 && docker exec nexplane-backend-1 curl -sf http://localhost:8000/health"
```

- [ ] **Step 7: Also build and deploy the updated agent binary to EC2**

```bash
# Build on EC2 (agent runs natively)
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane/agent && go build -o nexplane-agent . && echo built"
# The agent binary is baked into the Docker image for deploy; for the smoke test the agent
# runs on the target EC2 instance (Ubuntu), not on the platform EC2.
# The agent is deployed via the deploy_nexplane_agent CR which downloads the binary from the backend.
# Ensure the agent binary served by the backend includes apparmor_learn:
docker exec nexplane-backend-1 ls /app/agent-binaries/ 2>/dev/null || echo "check agent serving path"
```

If the backend serves the agent binary from a build artifact, rebuild it:
```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane/agent && GOOS=linux GOARCH=amd64 go build -o /home/ec2-user/nexplane/backend/agent-binaries/nexplane-agent-linux-amd64 . && echo built"
docker restart nexplane-backend-1
```

- [ ] **Step 8: Run APPARMOR_AUTOGEN smoke phase**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "nohup docker exec nexplane-backend-1 stdbuf -oL python -u \
   /app/tests/smoke/test_aws_live.py --phases APPARMOR_AUTOGEN \
   --backend-tailscale-ip 100.101.186.39 \
   > /tmp/apparmor_smoke.log 2>&1 &"
# Then watch progress:
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "tail -f /tmp/apparmor_smoke.log"
```

- [ ] **Step 9: Fix any failures and re-run until green**

Common failure modes to anticipate:
- AppArmor audit logging requires `auditd` running — verify it's started before the soak window
- `aa-status` on Ubuntu may require sudo — the agent runs as root via SSM so this is fine
- The `apparmor_parser` on Ubuntu may be at `/sbin/apparmor_parser` — check PATH in agent
- `deploy_nexplane_agent` downloads the binary from the backend; confirm the rebuilt binary is served

- [ ] **Step 10: Final commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): APPARMOR_AUTOGEN phase passing — Ubuntu 22.04, apparmor_learn, complain/rollback/delta verified"
git push origin master
```
