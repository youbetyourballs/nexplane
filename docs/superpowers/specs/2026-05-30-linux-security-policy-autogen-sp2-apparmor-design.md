# Linux Security Policy Auto-Generation SP2: AppArmor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the SP1 soak/synthesize/CR pipeline to support AppArmor policy auto-generation, refactoring the policy dispatch into a plugin registry so SP3 (SELinux) and beyond slot in without touching the service layer.

**Architecture:** Introduce a `PolicyPlugin` protocol in `app/services/security_policy/plugins/`. Register seccomp (migrated) and apparmor (new) plugins. Soak service dispatches through the registry — one lookup replaces all branching. Go agent gains two new commands: `apparmor_learn` (observe + collect AVC events) and `configure_apparmor` (write profile, load via `apparmor_parser`, rollback by restoring prior state). Smoke phase provisions Ubuntu 22.04, tests complain mode → enforce mode → rollback.

**Tech Stack:** Python/FastAPI (backend), Go (nexplane agent), AppArmor userspace tools (`apparmor_parser`, `aa-status`, `aa-complain`, `aa-enforce`, `auditd`), Ubuntu 22.04 EC2

**SELinux note:** SP3 (SELinux) is explicitly next after this ships. The plugin slot for `selinux` should be stubbed in the registry to make the on-ramp obvious.

---

## AppArmor Profile Format

Synthesized profiles are stored as:
```json
{
  "profile_name": "nexplane-nginx",
  "mode": "complain",
  "profile_text": "#include <tunables/global>\n/usr/sbin/nginx {\n  #include <abstractions/base>\n  capability net_bind_service,\n  /etc/nginx/** r,\n  /var/log/nginx/** w,\n  /run/nginx.pid rw,\n  /var/lib/nginx/** rw,\n  network tcp,\n}\n"
}
```

Delta extraction flattens profile rules into a sorted set of strings:
- `"file /etc/nginx/** r"`
- `"capability net_bind_service"`
- `"network tcp"`

---

## Plugin Registry Design

**`app/services/security_policy/plugins/base.py`**
```python
from dataclasses import dataclass
from typing import Callable
from app.models.change_request import ChangeType

@dataclass
class PolicyPlugin:
    policy_type: str
    learn_command: str                          # agent command name
    synthesize: Callable[[dict], dict]          # (raw_observations) -> profile dict
    cr_change_type: ChangeType                  # ChangeType enum value
    delta_extract: Callable[[dict], set]        # (profile) -> set of rule strings
    cr_title_template: str                      # f-string with {service_name}
    cr_description_template: str                # f-string with {syscall_count}, {partial}
```

**`app/services/security_policy/plugins/__init__.py`**
```python
from app.services.security_policy.plugins.seccomp import SECCOMP_PLUGIN
from app.services.security_policy.plugins.apparmor import APPARMOR_PLUGIN

REGISTRY: dict[str, PolicyPlugin] = {
    "seccomp": SECCOMP_PLUGIN,
    "apparmor": APPARMOR_PLUGIN,
    # "selinux": SELINUX_PLUGIN,       # SP3 — stub slot
    # "network_policy": NETPOL_PLUGIN, # SP4 — stub slot
}

def get_plugin(policy_type: str) -> PolicyPlugin:
    plugin = REGISTRY.get(policy_type)
    if plugin is None:
        raise ValueError(f"Unsupported policy_type: {policy_type!r}")
    return plugin
```

---

## CR Parameters for configure_apparmor

```json
{
  "session_id": "<uuid>",
  "service_name": "nginx",
  "profile": {
    "profile_name": "nexplane-nginx",
    "mode": "complain",
    "profile_text": "..."
  }
}
```

The executor writes `profile_text` to `/etc/apparmor.d/<profile_name>`, runs `apparmor_parser -r /etc/apparmor.d/<profile_name>`, then calls `aa-complain` or `aa-enforce` based on `mode`. Execution result includes `snapshot` (prior profile content, or `""` if none existed) for rollback.

---

## Files Created / Modified

### New files
- `backend/app/services/security_policy/plugins/__init__.py`
- `backend/app/services/security_policy/plugins/base.py`
- `backend/app/services/security_policy/plugins/seccomp.py` — migrated from synthesizer.py
- `backend/app/services/security_policy/plugins/apparmor.py` — new
- `backend/app/connectors/executors/nexplane_agent/configure_apparmor.py` — new executor
- `agent/commands/linuxharden/apparmor_learn_linux.go` — new agent command (mirrors seccomp_learn_linux.go)
- `agent/commands/ossecurity/apparmor_linux.go` — new agent command (mirrors seccomp_linux.go)

### Modified files
- `backend/app/services/security_policy/soak_service.py` — dispatch through registry
- `backend/app/services/security_policy/synthesizer.py` — delegate to plugins, keep compute_delta as policy-aware
- `backend/app/models/change_request.py` — add `configure_apparmor` to ChangeType enum
- `backend/app/services/safety_engine.py` — add `configure_apparmor` to implicit rollback types
- `backend/app/connectors/executors/nexplane_agent/__init__.py` — register configure_apparmor executor
- `backend/tests/smoke/test_aws_live.py` — add APPARMOR_AUTOGEN phase

---

## Tasks

### Task 1: Plugin registry scaffold

**Files:**
- Create: `backend/app/services/security_policy/plugins/__init__.py`
- Create: `backend/app/services/security_policy/plugins/base.py`

- [ ] **Step 1: Write `base.py`**

```python
# backend/app/services/security_policy/plugins/base.py
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
    cr_title_template: str
    cr_description_template: str
```

- [ ] **Step 2: Write `plugins/__init__.py` with empty registry (plugins added in later tasks)**

```python
# backend/app/services/security_policy/plugins/__init__.py
from __future__ import annotations
from app.services.security_policy.plugins.base import PolicyPlugin


REGISTRY: dict[str, "PolicyPlugin"] = {}


def get_plugin(policy_type: str) -> "PolicyPlugin":
    plugin = REGISTRY.get(policy_type)
    if plugin is None:
        raise ValueError(f"Unsupported policy_type: {policy_type!r}")
    return plugin
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/security_policy/plugins/
git commit -m "feat(security-policy): add plugin registry scaffold for policy-type dispatch"
```

---

### Task 2: Migrate seccomp into a plugin

**Files:**
- Create: `backend/app/services/security_policy/plugins/seccomp.py`
- Modify: `backend/app/services/security_policy/plugins/__init__.py`
- Read: `backend/app/services/security_policy/synthesizer.py` (source of synthesize_seccomp and compute_delta)

- [ ] **Step 1: Write the seccomp plugin, migrating `synthesize_seccomp` logic**

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
from app.services.security_policy.plugins.base import PolicyPlugin
from app.services.security_policy.plugins.seccomp import SECCOMP_PLUGIN


REGISTRY: dict[str, PolicyPlugin] = {
    "seccomp": SECCOMP_PLUGIN,
    # "selinux": SELINUX_PLUGIN,       # SP3
    # "network_policy": NETPOL_PLUGIN, # SP4
}


def get_plugin(policy_type: str) -> PolicyPlugin:
    plugin = REGISTRY.get(policy_type)
    if plugin is None:
        raise ValueError(f"Unsupported policy_type: {policy_type!r}")
    return plugin
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/security_policy/plugins/
git commit -m "feat(security-policy): migrate seccomp synthesizer into plugin"
```

---

### Task 3: Refactor soak service to dispatch through plugin registry

**Files:**
- Modify: `backend/app/services/security_policy/soak_service.py`
- Modify: `backend/app/services/security_policy/synthesizer.py`

Read both files in full before editing.

The soak service has three policy-specific callsites to replace with registry lookups:

1. **`_collect_observations`** — replace hardcoded `"seccomp_learn"` with `plugin.learn_command`
2. **`stop_and_synthesize`** — replace `synthesize_seccomp(observations)` with `plugin.synthesize(observations)`
3. **`_create_configure_seccomp_cr`** — rename to `_create_policy_cr`, replace hardcoded `ChangeType.configure_seccomp`, title, and description with plugin values

Also update `compute_delta` in `synthesizer.py` to accept `policy_type` and delegate to the plugin's `delta_extract`:

```python
# synthesizer.py — updated compute_delta
def compute_delta(prior: dict, current: dict, policy_type: str = "seccomp") -> dict:
    from app.services.security_policy.plugins import get_plugin
    plugin = get_plugin(policy_type)
    prior_set = plugin.delta_extract(prior)
    current_set = plugin.delta_extract(current)
    return {
        "added": sorted(current_set - prior_set),
        "removed": sorted(prior_set - current_set),
    }
```

Update the callsite in `stop_and_synthesize` that calls `compute_delta` to pass `session.policy_type`.

- [ ] **Step 1: Read soak_service.py and synthesizer.py in full**

- [ ] **Step 2: Rename `_create_configure_seccomp_cr` → `_create_policy_cr`, parameterize via plugin**

The new signature:
```python
async def _create_policy_cr(
    db: AsyncSession,
    session: SecurityPolicySoakSession,
    profile: dict,
    service_name: str,
    user_id: uuid.UUID,
    plugin,
) -> ChangeRequest:
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

- [ ] **Step 3: Update `_collect_observations` to accept `policy_type` and derive command from plugin**

```python
async def _collect_observations(
    asset_ids: list[str],
    window_seconds: int,
    policy_type: str,
) -> tuple[dict[str, list], bool]:
    from app.services.security_policy.plugins import get_plugin
    plugin = get_plugin(policy_type)
    # ... rest of implementation using plugin.learn_command
```

- [ ] **Step 4: Update `stop_and_synthesize` to use plugin.synthesize and pass policy_type to compute_delta**

- [ ] **Step 5: Update `synthesizer.py` `compute_delta` to accept policy_type and delegate to plugin**

- [ ] **Step 6: Verify no remaining hardcoded `"seccomp_learn"` or `ChangeType.configure_seccomp` references in soak_service.py**

Run:
```bash
grep -n "seccomp_learn\|configure_seccomp_cr\|synthesize_seccomp" backend/app/services/security_policy/soak_service.py
```
Expected: no output.

- [ ] **Step 7: Run existing unit tests**

```bash
cd backend && python -m pytest tests/unit/test_security_policy* -v 2>/dev/null || echo "no unit tests yet"
```

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/security_policy/
git commit -m "refactor(security-policy): dispatch through plugin registry, remove policy-type branching from soak service"
```

---

### Task 4: Add `configure_apparmor` ChangeType and register executor

**Files:**
- Modify: `backend/app/models/change_request.py`
- Modify: `backend/app/services/safety_engine.py`
- Create: `backend/app/connectors/executors/nexplane_agent/configure_apparmor.py`
- Modify: `backend/app/connectors/executors/nexplane_agent/__init__.py`

- [ ] **Step 1: Add `configure_apparmor` to ChangeType enum in `change_request.py`**

Find the `configure_seccomp` entry and add after it:
```python
configure_apparmor = "configure_apparmor"
```

- [ ] **Step 2: Add `configure_apparmor` to `_IMPLICIT_ROLLBACK_TYPES` in `safety_engine.py`**

After the `configure_seccomp` line:
```python
ChangeType.configure_apparmor,
```

- [ ] **Step 3: Create `configure_apparmor.py` executor**

```python
# backend/app/connectors/executors/nexplane_agent/configure_apparmor.py
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
        parameters={
            "action": "restore",
            "service_name": execution_result.get("service_name", parameters.get("service_name", "")),
            "snapshot": execution_result.get("snapshot", ""),
            "profile_name": execution_result.get("profile_name", parameters.get("profile", {}).get("profile_name", "")),
        },
        asset_ids=asset_ids,
        timeout_seconds=120,
    )
```

- [ ] **Step 4: Register executor in `nexplane_agent/__init__.py`**

Find the `configure_seccomp` registration and add `configure_apparmor` alongside it. Read the file to find the exact pattern before editing.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/change_request.py backend/app/services/safety_engine.py \
        backend/app/connectors/executors/nexplane_agent/
git commit -m "feat(security-policy): add configure_apparmor ChangeType and executor"
```

---

### Task 5: AppArmor synthesizer plugin

**Files:**
- Create: `backend/app/services/security_policy/plugins/apparmor.py`
- Modify: `backend/app/services/security_policy/plugins/__init__.py`

AppArmor events from the agent look like:
```json
[
  {"operation": "file_read", "resource": "/etc/nginx/nginx.conf"},
  {"operation": "file_write", "resource": "/var/log/nginx/access.log"},
  {"operation": "capability", "resource": "net_bind_service"},
  {"operation": "network", "resource": "tcp"}
]
```

Each asset in `raw_observations` maps to a list of such event dicts.

- [ ] **Step 1: Write `_synthesize` — build AppArmor profile text from events**

```python
import re
from collections import defaultdict
from app.models.change_request import ChangeType
from app.services.security_policy.plugins.base import PolicyPlugin

_GLOB_PATTERNS = [
    (re.compile(r"^(/var/log/[^/]+)/"), r"\1/*"),
    (re.compile(r"^(/etc/[^/]+)/"), r"\1/**"),
    (re.compile(r"^(/var/lib/[^/]+)/"), r"\1/**"),
    (re.compile(r"^(/run/[^/]+)/"), r"\1/*"),
    (re.compile(r"^(/tmp)/"), r"\1/**"),
]

_OP_TO_MASK = {
    "file_read": "r",
    "file_write": "w",
    "file_exec": "ix",
    "file_append": "a",
}


def _glob_path(path: str) -> str:
    for pattern, replacement in _GLOB_PATTERNS:
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
                globbed = _glob_path(resource)
                file_rules[globbed].add(_OP_TO_MASK[op])
            elif op == "capability":
                capabilities.add(resource)
            elif op == "network":
                network_protos.add(resource)

    lines = [
        '#include <tunables/global>',
        '',
        '/usr/sbin/{service} {',  # placeholder — executor fills service_name
        '  #include <abstractions/base>',
        '',
    ]
    for cap in sorted(capabilities):
        lines.append(f"  capability {cap},")
    if capabilities:
        lines.append('')
    for proto in sorted(network_protos):
        lines.append(f"  network {proto},")
    if network_protos:
        lines.append('')
    for path in sorted(file_rules):
        mask = "".join(sorted(file_rules[path]))
        lines.append(f"  {path} {mask},")
    lines.append('}')

    profile_text = "\n".join(lines)
    return {
        "profile_name": "nexplane-{service}",  # executor substitutes service_name
        "mode": "complain",
        "profile_text": profile_text,
    }


def _delta_extract(profile: dict) -> set:
    text = profile.get("profile_text", "")
    rules: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip().rstrip(",")
        if stripped and not stripped.startswith("#") and not stripped.startswith("{") and stripped != "}":
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

- [ ] **Step 2: Register AppArmor plugin in `__init__.py`**

```python
from app.services.security_policy.plugins.apparmor import APPARMOR_PLUGIN

REGISTRY: dict[str, PolicyPlugin] = {
    "seccomp": SECCOMP_PLUGIN,
    "apparmor": APPARMOR_PLUGIN,
    # "selinux": SELINUX_PLUGIN,       # SP3
    # "network_policy": NETPOL_PLUGIN, # SP4
}
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/security_policy/plugins/
git commit -m "feat(security-policy): add AppArmor synthesizer plugin"
```

---

### Task 6: Go agent — `apparmor_learn` command

**Files:**
- Create: `agent/commands/linuxharden/apparmor_learn_linux.go`

Read `agent/commands/linuxharden/seccomp_learn_linux.go` first to understand the command interface pattern before writing.

The `apparmor_learn` command:
1. Verifies AppArmor is available: `aa-status --enabled` (exit 0 = enabled)
2. Loads a temporary complain-mode profile for the service to ensure AVC events flow:
   - Profile path: `/etc/apparmor.d/nexplane-learn-<service>`
   - Profile content: minimal `#include <abstractions/base>` profile in complain mode
   - Load: `apparmor_parser -r /etc/apparmor.d/nexplane-learn-<service>`
   - Set complain: `aa-complain /etc/apparmor.d/nexplane-learn-<service>`
3. Clears the audit log tail position: record current `wc -l /var/log/audit/audit.log` offset
4. Sleeps for `duration_seconds`
5. Reads new AVC lines from the log since the offset
6. Parses each line for `type=AVC` with `apparmor="ALLOWED"`:
   - Extract `operation=`, `name=` (file path), `requested_mask=`, `capname=`, `family=`/`sock_type=`
7. Cleans up the temporary profile: `apparmor_parser -R /etc/apparmor.d/nexplane-learn-<service>`, remove file
8. Returns: `{"apparmor_events": [{"operation": "file_read", "resource": "/etc/nginx/nginx.conf"}, ...]}`

**Log parsing:** AppArmor AVC lines in `/var/log/audit/audit.log` look like:
```
type=AVC msg=audit(...): apparmor="ALLOWED" operation="file_read" profile="nexplane-learn-nginx" name="/etc/nginx/nginx.conf" pid=... comm="nginx" requested_mask="r" ...
type=AVC msg=audit(...): apparmor="ALLOWED" operation="capable" profile="nexplane-learn-nginx" pid=... comm="nginx" capname="net_bind_service" ...
type=AVC msg=audit(...): apparmor="ALLOWED" operation="connect" profile="nexplane-learn-nginx" pid=... comm="nginx" family="inet" sock_type="stream" ...
```

Fallback: if `/var/log/audit/audit.log` is unavailable, try `/var/log/syslog` for `ALLOWED` entries.

- [ ] **Step 1: Read `agent/commands/seccomp_learn.go` to understand command interface**

- [ ] **Step 2: Write `apparmor_learn.go`** following the seccomp_learn pattern, implementing the 8-step flow above

- [ ] **Step 3: Register the command in the agent's command dispatcher** (same file where seccomp_learn is registered — find it by reading `agent/commands/` directory)

- [ ] **Step 4: Build the agent to verify it compiles**

```bash
cd agent && go build ./... 2>&1
```
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add agent/commands/linuxharden/apparmor_learn_linux.go agent/
git commit -m "feat(agent): add apparmor_learn command for AppArmor AVC observation"
```

---

### Task 7: Go agent — `configure_apparmor` command

**Files:**
- Create: `agent/commands/ossecurity/apparmor_linux.go`

Read `agent/commands/ossecurity/seccomp_linux.go` first to understand the pattern.

The `configure_apparmor` command handles two actions:

**`action="apply"` (default):**
1. Substitute service name into `profile_name` and `profile_text` (replace `{service}` placeholder)
2. Read existing profile at `/etc/apparmor.d/<profile_name>` if it exists → store as `snapshot`
3. Write `profile_text` to `/etc/apparmor.d/<profile_name>`
4. Load: `apparmor_parser -r /etc/apparmor.d/<profile_name>`
5. Apply mode: if `mode == "complain"` run `aa-complain /etc/apparmor.d/<profile_name>`, else `aa-enforce /etc/apparmor.d/<profile_name>`
6. Return: `{"profile_name": "...", "service_name": "...", "mode": "...", "snapshot": "<prior content or empty string>"}`

**`action="restore"`:**
1. If `snapshot` is non-empty: write snapshot content back to `/etc/apparmor.d/<profile_name>`, reload with `apparmor_parser -r`
2. If `snapshot` is empty (profile didn't exist before): `apparmor_parser -R /etc/apparmor.d/<profile_name>` to unload, remove file
3. Return: `{"restored": true}`

- [ ] **Step 1: Read `agent/commands/configure_seccomp.go` to understand the pattern**

- [ ] **Step 2: Write `configure_apparmor.go`** implementing apply and restore actions

- [ ] **Step 3: Register in agent command dispatcher**

- [ ] **Step 4: Build**

```bash
cd agent && go build ./... 2>&1
```

- [ ] **Step 5: Commit**

```bash
git add agent/commands/ossecurity/apparmor_linux.go agent/
git commit -m "feat(agent): add configure_apparmor command with complain/enforce modes and snapshot rollback"
```

---

### Task 8: APPARMOR_AUTOGEN smoke phase

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py`

Add `APPARMOR_AUTOGEN` as a new smoke phase. Follow the SECCOMP_AUTOGEN phase structure. Key differences:
- Provision Ubuntu 22.04 LTS (use AMI filter: `ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*`, owner `099720109477` = Canonical)
- Install: `apt-get update && apt-get install -y apparmor apparmor-utils auditd nginx`
- Enable/start services: `systemctl enable auditd nginx && systemctl start auditd nginx`
- Soak session with `policy_type="apparmor"`
- After first CR execute: verify via `aa-status --json | python3 -c "import sys,json; d=json.load(sys.stdin); assert any('nexplane' in p for p in d.get('profiles', {}))" && echo aa_ok`
- Apply complain mode first (default from synthesizer), verify nginx responds
- Rollback, verify profile gone: `aa-status | grep nexplane | wc -l` → should be 0
- Apply enforce mode (second CR with `mode=enforce` in desired_outcome): verify nginx responds + `aa-status` shows enforce
- Rollback enforce mode
- Second soak → delta → accept → second CR proposed
- Terminate instance

The phase must go through the full Nexplane CR lifecycle (create→plan→approve→execute→rollback) — no direct `apparmor_parser` calls from the test.

**Ubuntu AMI lookup:**
```python
ec2_client = boto3.client("ec2", region_name="us-east-1")
images = ec2_client.describe_images(
    Owners=["099720109477"],
    Filters=[
        {"Name": "name", "Values": ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]},
        {"Name": "architecture", "Values": ["x86_64"]},
        {"Name": "state", "Values": ["available"]},
    ],
)
ami_id = sorted(images["Images"], key=lambda x: x["CreationDate"])[-1]["ImageId"]
```

Add `APPARMOR_AUTOGEN` to the `--phases` argument parser alongside existing phases.

- [ ] **Step 1: Read the SECCOMP_AUTOGEN phase in test_aws_live.py (lines ~21440–21710) as the template**

- [ ] **Step 2: Add Ubuntu AMI lookup helper near the top of the phase function**

- [ ] **Step 3: Write `run_phase_apparmor_autogen()` implementing the full flow above**

- [ ] **Step 4: Register phase in `main()` alongside SECCOMP_AUTOGEN**

- [ ] **Step 5: Run the smoke phase from EC2**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 \
  "nohup docker exec nexplane-backend-1 stdbuf -oL python -u \
   /app/tests/smoke/test_aws_live.py --phases APPARMOR_AUTOGEN \
   --backend-tailscale-ip 100.101.186.39 \
   > /tmp/apparmor_smoke.log 2>&1 &"
```

Monitor until `APPARMOR_AUTOGEN PASSED ✓` or failure.

- [ ] **Step 6: Fix any failures, re-run until green**

- [ ] **Step 7: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat(smoke): add APPARMOR_AUTOGEN phase with Ubuntu 22.04, complain/enforce modes, rollback"
```

---

## SELinux Stub (SP3 on-ramp)

The plugin registry `__init__.py` should include commented-out slots:

```python
# "selinux": SELINUX_PLUGIN,       # SP3: Amazon Linux / RHEL — audit2allow-based synthesis
# "network_policy": NETPOL_PLUGIN, # SP4: Kubernetes NetworkPolicy
```

SELinux synthesis will follow the same pattern: `selinux_learn` agent command parses `/var/log/audit/audit.log` for AVC denials during a complain window, `audit2allow` generates a policy module, `configure_selinux` loads it via `semodule -i`. Amazon Linux is the natural smoke target (SELinux enabled by default).
