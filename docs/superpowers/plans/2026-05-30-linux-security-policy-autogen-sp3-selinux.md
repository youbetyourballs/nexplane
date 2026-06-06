# SP3 SELinux Policy Auto-Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the soak/synthesize/CR pipeline to SELinux by adding a `selinux` policy plugin, a `selinux_learn` Go agent command, fixing the `configure_selinux` rollback bug, and running a passing `SELINUX_AUTOGEN` smoke phase on Amazon Linux 2.

**Architecture:** A new `plugins/selinux.py` registers `SELINUX_PLUGIN` in the existing registry. `selinux_learn_linux.go` sets the target process domain to per-type permissive, collects raw AVC denial lines during the soak window, and returns them. The Python synthesizer parses AVC lines in pure Python (no `audit2allow` on the backend) and generates a `.te` module source. The `configure_selinux` Go executor compiles the `.te` to `.pp` on the target host and installs it via `semodule`. Rollback removes the module and restores the previous enforcement config.

**Tech Stack:** Python/FastAPI, Go (agent), SELinux userspace tools (`semanage`, `checkmodule`, `semodule_package`, `semodule`, `sestatus`), `policycoreutils-python-utils`, Amazon Linux 2 EC2

---

## File Map

| File | Status | Change |
|------|--------|--------|
| `backend/app/connectors/executors/nexplane_agent/configure_selinux.py` | Modify | Fix rollback bug + unpack profile JSON (mirrors configure_apparmor.py) |
| `backend/app/connectors/change_type_definitions/selinux_learn.json` | Create | Planning engine definition |
| `backend/app/connectors/change_type_definitions/configure_selinux.json` | Create | Planning engine definition |
| `backend/app/services/security_policy/plugins/selinux.py` | Create | AVC parser, .te synthesizer, delta extractor, SELINUX_PLUGIN |
| `backend/app/services/security_policy/plugins/__init__.py` | Modify | Register SELINUX_PLUGIN |
| `backend/app/services/security_policy/soak_service.py` | Modify | Add `avc_lines` to observation key chain (one line) |
| `backend/app/connectors/executors/nexplane_agent/selinux_learn.py` | Create | Backend executor for selinux_learn agent command |
| `backend/tests/unit/test_selinux_plugin.py` | Create | Unit tests for AVC parser and synthesizer |
| `agent/commands/linuxharden/selinux_learn_linux.go` | Create | Per-type permissive, AVC collection |
| `agent/commands/linuxharden/selinux_learn_other.go` | Create | Non-Linux stub |
| `agent/commands/linuxharden/linuxharden.go` | Modify | Export SelinuxLearnExecute, SelinuxLearnRollback |
| `agent/commands/ossecurity/selinux_linux.go` | Modify | Handle module_source param: compile .te → .pp → semodule -i |
| `agent/executor/executor.go` | Modify | Register selinux_learn in commands and rollbacks maps |
| `backend/tests/smoke/test_aws_live.py` | Modify | Add SELINUX_AUTOGEN phase |

---

## Task 1: Fix `configure_selinux.py` — rollback bug + profile unpack

**Files:**
- Modify: `backend/app/connectors/executors/nexplane_agent/configure_selinux.py`

**Context:** Two bugs:
1. `rollback` passes `{"action": "restore", "snapshot_id": ...}` but Go `selinuxRollbackOS` expects `params["config_snapshot"].(map[string]any)`.
2. `execute` passes raw `parameters` to the agent but the soak service wraps the profile as `{"session_id": ..., "service_name": ..., "profile": "<json string>"}`. It must unpack `profile` into `{module_name, module_source}` before dispatching — same pattern as `configure_apparmor.py`.

- [ ] **Step 1: Replace `configure_selinux.py` with the fixed implementation**

```python
import json

from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    # Unpack synthesized profile from soak service: {session_id, service_name, profile: <json str>}
    # into the flat shape the Go agent expects: {module_name, module_source}.
    agent_params = dict(parameters)
    profile_raw = agent_params.pop("profile", None)
    service_name = agent_params.get("service_name", "")
    if profile_raw is not None:
        profile = json.loads(profile_raw) if isinstance(profile_raw, str) else profile_raw
        module_name = (profile.get("module_name") or "nexplane-{service_name}").replace(
            "{service_name}", service_name
        )
        module_source = (profile.get("module_source") or "").replace("{service_name}", service_name)
        agent_params["module_name"] = module_name
        agent_params["module_source"] = module_source

    result = await _dispatch.dispatch_agent_job(
        command="configure_selinux",
        parameters=agent_params,
        asset_ids=list(asset_ids),
        timeout_seconds=180,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await _dispatch.dispatch_agent_job(
        command="configure_selinux",
        parameters={"config_snapshot": execution_result.get("config_snapshot", {})},
        asset_ids=asset_ids,
        timeout_seconds=120,
    )
```

- [ ] **Step 2: SCP to EC2**

```bash
scp -i ~/.ssh/id_ed25519 \
  backend/app/connectors/executors/nexplane_agent/configure_selinux.py \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/connectors/executors/nexplane_agent/configure_selinux.py
```

- [ ] **Step 3: Verify import in backend container**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker exec nexplane-backend-1 python -c 'from app.connectors.executors.nexplane_agent import configure_selinux; print(\"ok\")'"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/executors/nexplane_agent/configure_selinux.py
git commit -m "fix(executor): configure_selinux rollback passes config_snapshot map + unpack profile JSON"
```

---

## Task 2: Extend Go `selinuxExecuteOS` to compile and install `.te` module source

**Files:**
- Modify: `agent/commands/ossecurity/selinux_linux.go`

**Context:** When `params["module_source"]` is present, compile the `.te` source to a `.pp` binary on the target host (where SELinux tools are available) and install it via `semodule -i`. The service name is substituted into the module name for labeling. This block fits after the existing `generate_from_audit_log` block and before the `return`.

- [ ] **Step 1: Read the existing file to understand variable layout**

```bash
cat agent/commands/ossecurity/selinux_linux.go
```

Note the variables in scope when you add the new block: `modulesInstalled []string`, `snapshot map[string]any`.

- [ ] **Step 2: Add `module_source` handling block inside `selinuxExecuteOS`**

After the closing `}` of the `if gen, _ := params["generate_from_audit_log"].(bool); gen {` block and before `return map[string]any{...}`, add:

```go
	if moduleSource, ok := params["module_source"].(string); ok && moduleSource != "" {
		svcName, _ := params["service_name"].(string)
		if svcName == "" {
			svcName = "nexplane"
		}
		moduleName, _ := params["module_name"].(string)
		if moduleName == "" {
			moduleName = "nexplane-" + svcName
		}
		// Substitute {service_name} placeholder if still present
		moduleName = strings.ReplaceAll(moduleName, "{service_name}", svcName)
		moduleSource = strings.ReplaceAll(moduleSource, "{service_name}", svcName)

		tePath := "/tmp/" + moduleName + ".te"
		modPath := "/tmp/" + moduleName + ".mod"
		ppPath := "/tmp/" + moduleName + ".pp"
		defer os.Remove(tePath)  //nolint:errcheck
		defer os.Remove(modPath) //nolint:errcheck
		defer os.Remove(ppPath)  //nolint:errcheck

		if err := os.WriteFile(tePath, []byte(moduleSource), 0644); err != nil {
			return nil, fmt.Errorf("writing .te file: %w", err)
		}
		if out, err := exec.Command("checkmodule", "-M", "-m", "-o", modPath, tePath).CombinedOutput(); err != nil {
			return nil, fmt.Errorf("checkmodule: %s: %w", out, err)
		}
		if out, err := exec.Command("semodule_package", "-o", ppPath, "-m", modPath).CombinedOutput(); err != nil {
			return nil, fmt.Errorf("semodule_package: %s: %w", out, err)
		}
		if out, err := exec.Command("semodule", "-i", ppPath).CombinedOutput(); err != nil {
			return nil, fmt.Errorf("semodule -i %s: %s: %w", ppPath, out, err)
		}
		modulesInstalled = append(modulesInstalled, moduleName)
		snapshot["modules_installed"] = modulesInstalled
	}
```

- [ ] **Step 3: SCP to EC2**

```bash
scp -i ~/.ssh/id_ed25519 \
  agent/commands/ossecurity/selinux_linux.go \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/agent/commands/ossecurity/selinux_linux.go
```

- [ ] **Step 4: Build agent on EC2 to verify no compile errors**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && docker run --rm \
   -v /home/ec2-user/nexplane/agent:/app \
   -w /app golang:1.22-alpine go build ./... 2>&1 && echo build_ok"
```

Expected: `build_ok`

- [ ] **Step 5: Commit**

```bash
git add agent/commands/ossecurity/selinux_linux.go
git commit -m "feat(agent): configure_selinux handles module_source — compile .te and install via semodule"
```

---

## Task 3: SELinux plugin with unit tests

**Files:**
- Create: `backend/app/services/security_policy/plugins/selinux.py`
- Create: `backend/tests/unit/test_selinux_plugin.py`

**Context:** The plugin parses raw AVC denial strings in pure Python (no `audit2allow` needed on the backend). AVC format: `type=AVC ... avc:  denied  { read } for ... scontext=...:httpd_t:... tcontext=...:etc_t:... tclass=file`. Parser extracts `(stype, ttype, tclass, perms)` tuples and generates `.te` module source.

- [ ] **Step 1: Write the failing tests first**

Create `backend/tests/unit/test_selinux_plugin.py`:

```python
"""Unit tests for SELinux synthesizer plugin."""
import pytest
from app.services.security_policy.plugins.selinux import _synthesize, _delta_extract, _parse_avc_line


_SAMPLE_AVC = (
    "type=AVC msg=audit(1234.567:890): avc:  denied  { read } for "
    "pid=1234 comm=\"nginx\" name=\"nginx.conf\" dev=\"xvda1\" ino=12345 "
    "scontext=system_u:system_r:httpd_t:s0 "
    "tcontext=system_u:object_r:etc_t:s0 tclass=file permissive=1"
)

_SAMPLE_AVC_NETWORK = (
    "type=AVC msg=audit(1234.568:891): avc:  denied  { name_bind } for "
    "pid=1234 comm=\"nginx\" "
    "scontext=system_u:system_r:httpd_t:s0 "
    "tcontext=system_u:object_r:http_port_t:s0 tclass=tcp_socket permissive=1"
)


def test_parse_avc_line_file_read():
    result = _parse_avc_line(_SAMPLE_AVC)
    assert result is not None
    stype, ttype, tclass, perms = result
    assert stype == "httpd_t"
    assert ttype == "etc_t"
    assert tclass == "file"
    assert "read" in perms


def test_parse_avc_line_non_avc_returns_none():
    assert _parse_avc_line("type=SYSCALL msg=audit(...)") is None


def test_synthesize_empty_observations():
    profile = _synthesize({})
    assert "module_source" in profile
    assert "module_name" in profile
    assert "{service_name}" in profile["module_name"]


def test_synthesize_single_avc_line():
    obs = {"asset-1": [_SAMPLE_AVC]}
    profile = _synthesize(obs)
    text = profile["module_source"]
    assert "allow httpd_t etc_t:file" in text
    assert "read" in text


def test_synthesize_merges_perms_same_type_pair():
    avc2 = _SAMPLE_AVC.replace("{ read }", "{ write }")
    obs = {"asset-1": [_SAMPLE_AVC, avc2]}
    profile = _synthesize(obs)
    text = profile["module_source"]
    # Both read and write should appear in one allow rule
    assert "allow httpd_t etc_t:file" in text
    assert "read" in text
    assert "write" in text


def test_delta_extract_roundtrip():
    obs = {"a": [_SAMPLE_AVC]}
    profile = _synthesize(obs)
    rules = _delta_extract(profile)
    assert any("allow httpd_t etc_t:file" in r for r in rules)


def test_delta_empty_profile():
    assert _delta_extract({}) == set()
```

- [ ] **Step 2: Run tests to confirm they fail (module not yet created)**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker exec nexplane-backend-1 python -m pytest tests/unit/test_selinux_plugin.py -v 2>&1 | tail -15"
```

Expected: `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Create `backend/app/services/security_policy/plugins/selinux.py`**

```python
# backend/app/services/security_policy/plugins/selinux.py
from __future__ import annotations
import re
from collections import defaultdict

from app.models.change_request import ChangeType
from app.services.security_policy.plugins.base import PolicyPlugin

# Matches: avc:  denied  { perms } ... scontext=...:stype:... tcontext=...:ttype:... tclass=cls
_AVC_RE = re.compile(
    r'avc:\s+denied\s+\{([^}]+)\}.*?'
    r'scontext=\S+:\S+:(\w+):\S*\s+'
    r'tcontext=\S+:\S+:(\w+):\S*\s+'
    r'tclass=(\w+)'
)


def _parse_avc_line(line: str) -> tuple[str, str, str, frozenset[str]] | None:
    """Parse one AVC denial line. Returns (stype, ttype, tclass, perms) or None."""
    m = _AVC_RE.search(line)
    if not m:
        return None
    perms = frozenset(m.group(1).split())
    stype, ttype, tclass = m.group(2), m.group(3), m.group(4)
    return stype, ttype, tclass, perms


def _synthesize(raw_observations: dict) -> dict:
    """Union AVC lines across assets and generate a .te module source."""
    # rules: (stype, ttype, tclass) -> set of perms
    rules: dict[tuple[str, str, str], set[str]] = defaultdict(set)

    for avc_lines in raw_observations.values():
        if not avc_lines:
            continue
        for line in avc_lines:
            parsed = _parse_avc_line(line)
            if parsed:
                stype, ttype, tclass, perms = parsed
                rules[(stype, ttype, tclass)].update(perms)

    module_name = "nexplane-{service_name}"

    if not rules:
        return {
            "module_name": module_name,
            "module_source": f"module {module_name} 1.0;\n\nrequire {{\n}}\n",
        }

    all_types: set[str] = set()
    class_perms: dict[str, set[str]] = defaultdict(set)
    for (stype, ttype, tclass), perms in rules.items():
        all_types.add(stype)
        all_types.add(ttype)
        class_perms[tclass].update(perms)

    lines = [f"module {module_name} 1.0;", "", "require {"]
    for t in sorted(all_types):
        lines.append(f"    type {t};")
    for cls in sorted(class_perms):
        perm_str = " ".join(sorted(class_perms[cls]))
        lines.append(f"    class {cls} {{ {perm_str} }};")
    lines.append("}")
    lines.append("")

    for (stype, ttype, tclass), perms in sorted(rules.items()):
        perm_str = " ".join(sorted(perms))
        lines.append(f"allow {stype} {ttype}:{tclass} {{ {perm_str} }};")

    return {
        "module_name": module_name,
        "module_source": "\n".join(lines) + "\n",
    }


def _delta_extract(profile: dict) -> set:
    """Extract allow rules from .te module source as a comparable set."""
    source = profile.get("module_source", "")
    rules: set[str] = set()
    for line in source.splitlines():
        stripped = line.strip().rstrip(";")
        if stripped.startswith("allow "):
            rules.add(stripped)
    return rules


SELINUX_PLUGIN = PolicyPlugin(
    policy_type="selinux",
    learn_command="selinux_learn",
    synthesize=_synthesize,
    cr_change_type=ChangeType.configure_selinux,
    delta_extract=_delta_extract,
    cr_title_template="Apply SELinux policy module — {service_name}",
    cr_description_template=(
        "SELinux policy module synthesized from soak session. "
        "Allow rules: {rule_count}. Partial observation: {partial}."
    ),
)
```

- [ ] **Step 4: SCP plugin and test to EC2**

```bash
scp -i ~/.ssh/id_ed25519 \
  backend/app/services/security_policy/plugins/selinux.py \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/services/security_policy/plugins/selinux.py

scp -i ~/.ssh/id_ed25519 \
  backend/tests/unit/test_selinux_plugin.py \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/unit/test_selinux_plugin.py
```

- [ ] **Step 5: Run tests**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker exec nexplane-backend-1 python -m pytest tests/unit/test_selinux_plugin.py -v"
```

Expected: 7 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/security_policy/plugins/selinux.py \
        backend/tests/unit/test_selinux_plugin.py
git commit -m "feat(security-policy): add SELinux synthesizer plugin with unit tests"
```

---

## Task 4: Register plugin, update soak_service, add backend executor and change type definitions

**Files:**
- Modify: `backend/app/services/security_policy/plugins/__init__.py`
- Modify: `backend/app/services/security_policy/soak_service.py` (line 37)
- Create: `backend/app/connectors/executors/nexplane_agent/selinux_learn.py`
- Create: `backend/app/connectors/change_type_definitions/selinux_learn.json`
- Create: `backend/app/connectors/change_type_definitions/configure_selinux.json`

- [ ] **Step 1: Register SELINUX_PLUGIN in `plugins/__init__.py`**

Add after the `_register(APPARMOR_PLUGIN)` line and its comment:

```python
from app.services.security_policy.plugins.selinux import SELINUX_PLUGIN  # noqa: E402
_register(SELINUX_PLUGIN)
```

Remove the `# selinux: SP3` comment that was the placeholder.

- [ ] **Step 2: Update `soak_service.py` line 37**

Find this line (in `_observe_one`):
```python
            observations = result.get("syscalls_seen") or result.get("apparmor_events") or []
```

Replace with:
```python
            # seccomp returns syscalls_seen; apparmor returns apparmor_events; selinux returns avc_lines
            observations = result.get("syscalls_seen") or result.get("apparmor_events") or result.get("avc_lines") or []
```

- [ ] **Step 3: Verify `get_plugin("selinux")` works in the container**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "scp -i ~/.ssh/id_ed25519 /dev/stdin ..." # SCP both files first, then:
docker exec nexplane-backend-1 python -c "
from app.services.security_policy.plugins import get_plugin
p = get_plugin('selinux')
print(p.policy_type, p.learn_command, p.cr_change_type)
"
```

Expected: `selinux selinux_learn ChangeType.configure_selinux`

After SCPing the updated `__init__.py` and `soak_service.py`:
```bash
scp -i ~/.ssh/id_ed25519 \
  backend/app/services/security_policy/plugins/__init__.py \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/services/security_policy/plugins/__init__.py

scp -i ~/.ssh/id_ed25519 \
  backend/app/services/security_policy/soak_service.py \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/services/security_policy/soak_service.py
```

Then verify:
```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker exec nexplane-backend-1 python -c \"from app.services.security_policy.plugins import get_plugin; p = get_plugin('selinux'); print(p.policy_type, p.learn_command, p.cr_change_type)\""
```

- [ ] **Step 4: Create `selinux_learn.py` backend executor**

```python
# backend/app/connectors/executors/nexplane_agent/selinux_learn.py
from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    result = await _dispatch.dispatch_agent_job(
        command="selinux_learn",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=int(parameters.get("duration_seconds", 60)) + 60,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "selinux_learn creates no persistent state"}
```

- [ ] **Step 5: Create change type definition files**

`backend/app/connectors/change_type_definitions/selinux_learn.json`:
```json
{
  "change_type": "selinux_learn",
  "display_name": "SELinux Learn (Observe AVC Denials)",
  "steps": [
    {"generic_action": "selinux_learn", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "selinux_learn",
  "rollback_connector_type": "nexplane_agent"
}
```

`backend/app/connectors/change_type_definitions/configure_selinux.json`:
```json
{
  "change_type": "configure_selinux",
  "display_name": "Configure SELinux Policy Module",
  "steps": [
    {"generic_action": "configure_selinux", "purpose": "execute", "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "verification_methods": ["api_check"],
  "rollback_action": "configure_selinux",
  "rollback_connector_type": "nexplane_agent"
}
```

- [ ] **Step 6: SCP all new files to EC2**

```bash
scp -i ~/.ssh/id_ed25519 \
  backend/app/connectors/executors/nexplane_agent/selinux_learn.py \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/connectors/executors/nexplane_agent/selinux_learn.py

scp -i ~/.ssh/id_ed25519 \
  backend/app/connectors/change_type_definitions/selinux_learn.json \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/connectors/change_type_definitions/selinux_learn.json

scp -i ~/.ssh/id_ed25519 \
  backend/app/connectors/change_type_definitions/configure_selinux.json \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/connectors/change_type_definitions/configure_selinux.json
```

- [ ] **Step 7: Restart backend and verify health**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker restart nexplane-backend-1 && sleep 8 && docker exec nexplane-backend-1 curl -sf http://localhost:8000/health"
```

Expected: `{"status":"ok","service":"nexplane"}`

- [ ] **Step 8: Commit**

```bash
git add \
  backend/app/services/security_policy/plugins/__init__.py \
  backend/app/services/security_policy/soak_service.py \
  backend/app/connectors/executors/nexplane_agent/selinux_learn.py \
  backend/app/connectors/change_type_definitions/selinux_learn.json \
  backend/app/connectors/change_type_definitions/configure_selinux.json
git commit -m "feat(security-policy): register SELinux plugin, add selinux_learn executor and change type definitions"
```

---

## Task 5: Go agent — `selinux_learn` command

**Files:**
- Create: `agent/commands/linuxharden/selinux_learn_linux.go`
- Create: `agent/commands/linuxharden/selinux_learn_other.go`
- Modify: `agent/commands/linuxharden/linuxharden.go`
- Modify: `agent/executor/executor.go`

**Context:** Read `apparmor_learn_linux.go` and `linuxharden.go` before writing — this command mirrors the same structure. Key differences from AppArmor: uses `semanage permissive -a <type>` (per-type permissive, not system-wide), collects AVC denial lines (not ALLOWED lines), resolves SELinux type from `systemctl show <service> -P SELinuxContext`.

- [ ] **Step 1: Read reference files**

```bash
cat agent/commands/linuxharden/apparmor_learn_linux.go
cat agent/commands/linuxharden/linuxharden.go
cat agent/executor/executor.go | head -120
```

- [ ] **Step 2: Create `selinux_learn_linux.go`**

```go
//go:build linux

package linuxharden

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func selinuxLearnExecute(params map[string]any) (map[string]any, error) {
	duration, _ := params["duration_seconds"].(float64)
	if duration <= 0 {
		duration = 60
	}
	serviceName, _ := params["service_name"].(string)
	if serviceName == "" {
		serviceName = "nginx"
	}

	if _, err := exec.LookPath("sestatus"); err != nil {
		return nil, fmt.Errorf("SELinux not available: sestatus not found in PATH")
	}
	if _, err := exec.LookPath("semanage"); err != nil {
		return nil, fmt.Errorf("semanage not found — install policycoreutils-python-utils")
	}

	// Resolve SELinux type from service name
	selinuxType, err := resolveSelinuxType(serviceName)
	if err != nil {
		return nil, fmt.Errorf("resolving SELinux type for %q: %w", serviceName, err)
	}

	// Set per-type permissive (only this domain, rest of system stays enforcing)
	if out, err := exec.Command("semanage", "permissive", "-a", selinuxType).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("semanage permissive -a %s: %s: %w", selinuxType, out, err)
	}
	defer func() {
		exec.Command("semanage", "permissive", "-d", selinuxType).Run() //nolint:errcheck
	}()

	// Record audit log offset before observation window
	logPath := "/var/log/audit/audit.log"
	startOffset := int64(0)
	if info, err := os.Stat(logPath); err == nil {
		startOffset = info.Size()
	}

	time.Sleep(time.Duration(duration) * time.Second)

	// Collect new AVC denial lines since offset
	avcLines := collectAVCDenials(logPath, startOffset, selinuxType)

	return map[string]any{
		"action":           "selinux_learn",
		"service_name":     serviceName,
		"selinux_type":     selinuxType,
		"duration_seconds": int(duration),
		"avc_lines":        avcLines,
		"avc_count":        len(avcLines),
	}, nil
}

func resolveSelinuxType(serviceName string) (string, error) {
	out, err := exec.Command("systemctl", "show", serviceName, "-P", "SELinuxContext").Output()
	if err == nil {
		context := strings.TrimSpace(string(out))
		parts := strings.Split(context, ":")
		if len(parts) >= 3 && parts[2] != "" {
			return parts[2], nil
		}
	}
	// Fallback: scan ps -eZ for the service name
	psOut, err := exec.Command("ps", "-eZ").Output()
	if err != nil {
		return "", fmt.Errorf("ps -eZ: %w", err)
	}
	for _, line := range strings.Split(string(psOut), "\n") {
		if strings.Contains(line, serviceName) {
			fields := strings.Fields(line)
			if len(fields) > 0 {
				parts := strings.Split(fields[0], ":")
				if len(parts) >= 3 && parts[2] != "" {
					return parts[2], nil
				}
			}
		}
	}
	return "", fmt.Errorf("could not determine SELinux type for %q from systemctl or ps", serviceName)
}

func collectAVCDenials(logPath string, startOffset int64, selinuxType string) []string {
	f, err := os.Open(logPath)
	if err != nil {
		return nil
	}
	defer f.Close()
	f.Seek(startOffset, 0) //nolint:errcheck

	seen := map[string]bool{}
	var lines []string
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.Contains(line, "avc:") || !strings.Contains(line, "denied") {
			continue
		}
		if !strings.Contains(line, selinuxType) {
			continue
		}
		if !seen[line] {
			seen[line] = true
			lines = append(lines, line)
		}
	}
	return lines
}

func selinuxLearnRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"action": "selinux_learn_rollback", "status": "no_state_to_revert"}, nil
}
```

- [ ] **Step 3: Create `selinux_learn_other.go`**

```go
//go:build !linux

package linuxharden

import "fmt"

func selinuxLearnExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("selinux_learn is only supported on Linux")
}

func selinuxLearnRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"status": "no_state_to_revert"}, nil
}
```

- [ ] **Step 4: Add exports to `linuxharden.go`**

Read the file first to find where `ApparmorLearnRollback` is declared, then add immediately after:

```go
func SelinuxLearnExecute(params map[string]any) (map[string]any, error) { return selinuxLearnExecute(params) }
func SelinuxLearnRollback(params map[string]any) (map[string]any, error) { return selinuxLearnRollback(params) }
```

- [ ] **Step 5: Register in `executor.go`**

In the `commands` map, after `"apparmor_learn"`:
```go
"selinux_learn":                  linuxharden.SelinuxLearnExecute,
```

In the `rollbacks` map, after `"apparmor_learn"`:
```go
"selinux_learn":                  linuxharden.SelinuxLearnRollback,
```

- [ ] **Step 6: SCP all four changed files to EC2**

```bash
scp -i ~/.ssh/id_ed25519 \
  agent/commands/linuxharden/selinux_learn_linux.go \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/agent/commands/linuxharden/selinux_learn_linux.go

scp -i ~/.ssh/id_ed25519 \
  agent/commands/linuxharden/selinux_learn_other.go \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/agent/commands/linuxharden/selinux_learn_other.go

scp -i ~/.ssh/id_ed25519 \
  agent/commands/linuxharden/linuxharden.go \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/agent/commands/linuxharden/linuxharden.go

scp -i ~/.ssh/id_ed25519 \
  agent/executor/executor.go \
  ec2-user@100.101.186.39:/home/ec2-user/nexplane/agent/executor/executor.go
```

- [ ] **Step 7: Build agent on EC2**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && docker run --rm \
   -v /home/ec2-user/nexplane/agent:/app \
   -w /app golang:1.22-alpine go build ./... 2>&1 && echo build_ok"
```

Expected: `build_ok`

- [ ] **Step 8: Build and deploy agent binary for smoke test**

Find the existing binary path, then rebuild:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "find /home/ec2-user/nexplane/backend -name 'nexplane-agent-linux*' 2>/dev/null | head -3"
```

Then rebuild to that path (adjust version suffix as found above):

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && docker run --rm \
   -v /home/ec2-user/nexplane/agent:/app \
   -w /app \
   -e GOOS=linux -e GOARCH=amd64 \
   golang:1.22-alpine \
   go build -o /app/nexplane-agent-linux-amd64 . && \
   cp /home/ec2-user/nexplane/agent/nexplane-agent-linux-amd64 \
      \$(find /home/ec2-user/nexplane/backend -name 'nexplane-agent-linux-amd64*' | head -1) && \
   echo deployed"
```

If the backend serves from `/opt/nexplane-downloads/` inside the container, copy into the container:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "docker cp /home/ec2-user/nexplane/agent/nexplane-agent-linux-amd64 \
   nexplane-backend-1:\$(docker exec nexplane-backend-1 find /opt -name 'nexplane-agent-linux*' | head -1) && \
   echo deployed"
```

- [ ] **Step 9: Commit**

```bash
git add \
  agent/commands/linuxharden/selinux_learn_linux.go \
  agent/commands/linuxharden/selinux_learn_other.go \
  agent/commands/linuxharden/linuxharden.go \
  agent/executor/executor.go
git commit -m "feat(agent): add selinux_learn command — per-type permissive AVC observation"
```

---

## Task 6: SELINUX_AUTOGEN smoke phase

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

**Context:** Read `run_phase_apparmor_autogen` (search for `def run_phase_apparmor_autogen`) before writing — it is the direct template. Key differences:
- Amazon Linux 2 instance (pass `"os": "amazon_linux"` or find the AL2 AMI the same way other phases do)
- Install: `yum install -y nginx audit policycoreutils-python-utils && systemctl enable auditd nginx && systemctl start auditd nginx`
- `policy_type="selinux"` in soak sessions
- Assert `cr["change_type"] == "configure_selinux"`
- Assert `"module_source"` in the baseline profile (not `"profile_text"`)
- Post-execute verify: `semodule -l | grep nexplane` confirms module installed; nginx responds
- Post-rollback verify: `semodule -l | grep nexplane` returns nothing (or empty); nginx still responds
- `setenforce` check: verify `getenforce` returns `Enforcing` before starting soak

- [ ] **Step 1: Find APPARMOR_AUTOGEN and note its structure**

```bash
docker exec nexplane-backend-1 grep -n "def run_phase_apparmor_autogen\|def run_phase_selinux" \
  /app/tests/smoke/test_aws_live.py
```

Read the function body (approx 200 lines) to understand the exact helper method signatures: `client.run_cr(label, change_type, asset_id, params)`, `client.post(path, json=...)`, `client.get(path, params=...)`, `client.delete(path)`.

- [ ] **Step 2: Write `run_phase_selinux_autogen()`**

Add immediately after `run_phase_apparmor_autogen`:

```python
def run_phase_selinux_autogen(client, base_url, cloud_account_id=None,
                              tailscale_auth_key="", backend_tailscale_ip=""):
    import time as _time

    tag = int(_time.time())
    log = lambda msg: print(f"  [SELINUX_AUTOGEN] {msg}", flush=True)
    log("Starting SELINUX_AUTOGEN smoke phase")

    if not cloud_account_id:
        cloud_account_id = client.get_cloud_account_asset_id()
    log(f"Using cloud account {cloud_account_id}")

    # ---- 1. Key pair ----
    key_name = f"nexplane-smoke-selinux-key-{tag}"
    log(f"Creating key pair {key_name}...")
    client.run_cr(
        "[SELINUX_AUTOGEN] create key pair", "key_pair_create", cloud_account_id,
        {"key_name": key_name, "rollback_strategy": "rollback_unavailable"},
    )

    # ---- 2. Launch Amazon Linux 2 EC2 (SELinux enforcing by default) ----
    instance_name = f"nexplane-smoke-selinux-nginx-{tag}"
    log(f"Launching EC2 instance {instance_name} (Amazon Linux 2, t3.micro)...")
    cr_result = client.run_cr(
        "[SELINUX_AUTOGEN] launch EC2", "ec2_launch", cloud_account_id,
        {
            "instance_name": instance_name,
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

    # ---- 3. Wait for SSM + Tailscale + agent ----
    log("Waiting 3min for SSM agent to become available...")
    _time.sleep(180)

    tailscale_auth_key = client.get_tailscale_auth_key(tailscale_auth_key)
    client.run_cr(
        "[SELINUX_AUTOGEN] tailscale join", "tailscale_join", instance_asset_id,
        {"instance_id": instance_id, "auth_key": tailscale_auth_key,
         "document_name": "AWS-RunShellScript", "rollback_strategy": "rollback_unavailable"},
    )
    client.run_cr(
        "[SELINUX_AUTOGEN] deploy nexplane agent", "deploy_nexplane_agent", cloud_account_id,
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

    # ---- 4. Install nginx + auditd + selinux tools ----
    log("Installing nginx, auditd, and policycoreutils-python-utils...")
    client.run_cr(
        "[SELINUX_AUTOGEN] install nginx", "ssm_command", instance_asset_id,
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": (
             "yum install -y nginx audit policycoreutils-python-utils && "
             "systemctl enable auditd nginx && "
             "systemctl start auditd nginx && "
             "nginx -t && echo nginx_ok"
         ),
         "rollback_strategy": "rollback_unavailable"},
    )
    log("nginx and auditd installed ✓")

    # ---- 5. Verify SELinux is enforcing ----
    client.run_cr(
        "[SELINUX_AUTOGEN] verify selinux enforcing", "ssm_command", instance_asset_id,
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "getenforce | grep -i enforcing && echo selinux_enforcing",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("SELinux is Enforcing ✓")

    # ---- 6. Start traffic generator ----
    log("Starting HTTP traffic generator (background, 150s)...")
    client.run_cr(
        "[SELINUX_AUTOGEN] start traffic generator", "ssm_command", instance_asset_id,
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

    # ---- 8. Clear any prior selinux baseline ----
    try:
        client.delete(f"/security-policy/baselines/{project_id}?policy_type=selinux")
        log("Pre-run: cleared any existing selinux baseline ✓")
    except Exception as _e:
        log(f"Pre-run baseline cleanup (non-fatal): {_e}")

    # ---- 9. First soak session ----
    session = client.post("/security-policy/soak-sessions", json={
        "project_id": project_id,
        "policy_type": "selinux",
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
    assert cr["change_type"] == "configure_selinux", f"Wrong change_type: {cr['change_type']}"
    log(f"Profile synthesized, CR proposed: {cr_id} (change_type=configure_selinux) ✓")

    baseline = client.get(f"/security-policy/baselines/{project_id}",
                          params={"policy_type": "selinux"})
    assert "module_source" in baseline["profile"], "Baseline missing module_source"
    log("Baseline stored with module_source ✓")

    # ---- 10. Plan → approve → execute CR ----
    client.post(f"/change-requests/{cr_id}/plan")
    client.post(f"/change-requests/{cr_id}/submit-for-approval")
    client.post(f"/change-requests/{cr_id}/approve",
                json={"decision": "approved", "comment": "selinux_autogen smoke"})
    log("CR submitted and approved ✓")

    client.post(f"/change-requests/{cr_id}/execute")
    for _ in range(36):
        _time.sleep(5)
        cr = client.get(f"/change-requests/{cr_id}")
        if cr["status"] in ("completed", "failed", "rolled_back"):
            break
    assert cr["status"] == "completed", f"CR did not complete: {cr['status']}"
    log("CR executed — SELinux module installed ✓")

    # Verify module installed and nginx still works
    client.run_cr(
        "[SELINUX_AUTOGEN] verify nginx post-selinux", "ssm_command", instance_asset_id,
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "curl -sf http://localhost/ > /dev/null && semodule -l | grep nexplane && echo aa_ok",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("nginx responding and SELinux module active ✓")

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
        "[SELINUX_AUTOGEN] verify nginx post-rollback", "ssm_command", instance_asset_id,
        {"instance_id": instance_id, "document_name": "AWS-RunShellScript",
         "command": "curl -sf http://localhost/ > /dev/null && echo nginx_ok_post_rollback",
         "rollback_strategy": "rollback_unavailable"},
    )
    log("nginx responding correctly after rollback ✓")

    # ---- 12. Second soak → delta flow ----
    log("Starting second soak session to test baseline-delta flow...")
    session2 = client.post("/security-policy/soak-sessions", json={
        "project_id": project_id,
        "policy_type": "selinux",
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
            "[SELINUX_AUTOGEN] terminate instance", "ec2_terminate", cloud_account_id,
            {"instance_id": instance_id},
        )
        log("Instance terminated ✓")
    except Exception as e:
        log(f"Terminate warning (non-fatal): {e}")

    log("SELINUX_AUTOGEN PASSED ✓")
    return {"status": "passed", "session_id": session_id, "cr_id": cr_id}
```

- [ ] **Step 3: Wire into `main()`**

Find the `if "APPARMOR_AUTOGEN" in phases:` block and add immediately after:

```python
        if "SELINUX_AUTOGEN" in phases:
            run_phase_selinux_autogen(client, base_url=args.base_url,
                                      cloud_account_id=cloud_account_id,
                                      tailscale_auth_key=args.tailscale_auth_key,
                                      backend_tailscale_ip=getattr(args, "backend_tailscale_ip", ""))
```

- [ ] **Step 4: Commit + push + pull on EC2**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add SELINUX_AUTOGEN phase — Amazon Linux 2, per-type permissive, module install/rollback/delta"
git push origin master
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "cd /home/ec2-user/nexplane && git pull && docker restart nexplane-backend-1 && sleep 8 && \
   docker exec nexplane-backend-1 curl -sf http://localhost:8000/health"
```

- [ ] **Step 5: Run SELINUX_AUTOGEN smoke phase**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "nohup docker exec nexplane-backend-1 stdbuf -oL python -u \
   /app/tests/smoke/test_aws_live.py --phases SELINUX_AUTOGEN \
   --backend-tailscale-ip 100.101.186.39 \
   > /tmp/selinux_smoke.log 2>&1 & echo started"
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "tail -f /tmp/selinux_smoke.log"
```

Expected last lines:
```
  [SELINUX_AUTOGEN] SELINUX_AUTOGEN PASSED ✓

============================================================
✅ ALL SELECTED PHASES PASSED
============================================================
```

- [ ] **Step 6: Fix any failures and re-run until green**

Common failure modes:
- `semanage permissive -a httpd_t` fails: verify `policycoreutils-python-utils` is installed and agent runs as root
- `checkmodule` not found: on AL2, it's in `policycoreutils` (already installed by default) — verify with `which checkmodule`
- Empty module (no AVC lines): synthesizer handles this with a minimal valid module — `semodule -i` of an empty-require module should still succeed
- `semodule -l | grep nexplane` returns nothing after execute: check the CR execution log — may be a `config_snapshot` key mismatch between Python and Go
- nginx not running as `httpd_t` on AL2: run `ps -eZ | grep nginx` on the instance to confirm the type

- [ ] **Step 7: Final commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): SELINUX_AUTOGEN phase passing — AL2, selinux_learn, per-type permissive, module rollback verified"
git push origin master
```
