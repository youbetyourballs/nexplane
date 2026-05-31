# SP3 SELinux Policy Auto-Generation Design

**Date:** 2026-05-30
**Status:** Approved

---

## Goal

Extend the soak/synthesize/CR pipeline to SELinux by adding a `selinux` plugin to the policy registry, a `selinux_learn` Go agent command, a `selinux_learn` backend executor, and a `SELINUX_AUTOGEN` smoke phase on Amazon Linux 2.

---

## Background and Prior Art

SP1 (seccomp) and SP2 (AppArmor) established the pipeline:

1. Operator names a service
2. Platform starts a soak session (`policy_type` = `seccomp` | `apparmor` | `selinux`)
3. Agent runs the policy-specific learn command for the observation window
4. Backend synthesizes a profile from observations
5. CR is proposed (`configure_seccomp` / `configure_apparmor` / `configure_selinux`)
6. Operator approves → CR executes → policy applied
7. Rollback removes the applied policy and restores prior state
8. Second soak computes delta against stored baseline

SP3 adds `selinux` to this pipeline with no changes to the pipeline logic itself — only a new plugin, new agent command, and new executor.

---

## Architecture

### Policy plugin registry (existing)

`backend/app/services/security_policy/plugins/__init__.py` already has a `# selinux: SP3` stub. Task: create `plugins/selinux.py` and register `SELINUX_PLUGIN`.

### Observation mechanism

SELinux in **per-type permissive mode** (`semanage permissive -a <type>`) relaxes enforcement only for the target process's domain. Everything else on the host stays enforcing. This is the right default for operators who don't know the application — only the service being profiled is relaxed, not the whole system.

The `selinux_learn` agent command:
1. Resolves the service's SELinux type: `systemctl show <service> -P SELinuxContext` → parse type from `system_u:system_r:httpd_t:s0`
2. `semanage permissive -a <type>` — set only that domain permissive
3. Records `/var/log/audit/audit.log` byte offset
4. Sleeps `duration_seconds`
5. Reads new AVC denial lines since offset that reference the type
6. `semanage permissive -d <type>` — restore enforcement
7. Returns `{avc_lines: [str, ...], selinux_type: "httpd_t", service_name: "nginx"}`

The agent returns **raw AVC denial strings only** — no compiled binary. Compilation happens on the target during CR execution (where SELinux tooling is available). This keeps base64 binaries out of CR parameters and the backend.

### `soak_service.py` — one-line change

`_collect_observations` currently reads:
```python
observations = result.get("syscalls_seen") or result.get("apparmor_events") or []
```
Add `selinux_learn`'s key to the chain:
```python
observations = result.get("syscalls_seen") or result.get("apparmor_events") or result.get("avc_lines") or []
```
This is the only change needed in `soak_service.py` for SP3.

### Python synthesizer — `plugins/selinux.py`

`_synthesize(raw_observations: dict) -> dict`:
- Unions all AVC lines across assets
- Parses each line in pure Python: extracts `scontext` (source type), `tcontext` (target type), `tclass`, and permission set from `{ ... }`
- De-duplicates `(stype, ttype, tclass, perm)` tuples
- Generates `.te` module source text:
  ```
  module nexplane-{service_name} 1.0;

  require {
      type httpd_t;
      type etc_t;
      class file { read };
  }

  allow httpd_t etc_t:file { read };
  ```
- Returns `{module_name: "nexplane-{service_name}", module_source: "<.te text>"}`

`_delta_extract(profile: dict) -> set`:
- Parses `allow` lines from `module_source`
- Returns each allow rule as a string: `"allow httpd_t etc_t:file { read }"`

### Go agent — `configure_selinux` extension

The existing `selinuxExecuteOS` in `agent/commands/ossecurity/selinux_linux.go` handles mode changes and `.pp` file installation. It needs one new parameter path to handle `module_source` (`.te` text):

When `params["module_source"]` is present:
1. Write `.te` text to `/tmp/nexplane-<service>.te`
2. `checkmodule -M -m -o /tmp/nexplane-<service>.mod /tmp/nexplane-<service>.te`
3. `semodule_package -o /tmp/nexplane-<service>.pp -m /tmp/nexplane-<service>.mod`
4. `semodule -i /tmp/nexplane-<service>.pp`
5. Record module name in snapshot for rollback
6. Clean up temp files

Rollback already works via `semodule -r <module_name>` and config restoration. **However, `configure_selinux.py` has the same rollback bug as `configure_apparmor.py` had**: it passes `{"action": "restore", "snapshot_id": ...}` but `selinuxRollbackOS` expects `params["config_snapshot"].(map[string]any)`. This must be fixed: rollback should pass `{"config_snapshot": execution_result.get("config_snapshot", {})}`.

### Backend executor — `selinux_learn.py`

Mirrors `apparmor_learn.py` exactly: dispatches `selinux_learn` agent command, stores `_asset_ids` in result, rollback is a no-op (no persistent state created).

### Smoke phase — `SELINUX_AUTOGEN`

**Target OS:** Amazon Linux 2 (SELinux enforcing by default, `httpd_t` type for nginx).

Phase steps:
1. Launch Amazon Linux 2 EC2 instance (use existing Amazon Linux AMI lookup — already in smoke test)
2. Key pair, Tailscale join, deploy nexplane agent
3. Install nginx + auditd: `yum install -y nginx audit && systemctl enable auditd nginx && systemctl start auditd nginx`
4. Start traffic generator (background curl loop, 150s)
5. Clear any prior selinux baseline for the project
6. Start soak session (`policy_type="selinux"`, `window_seconds=60`)
7. Wait 65s
8. Stop session → assert `cr_proposed`, `change_type == "configure_selinux"`
9. Assert baseline stored with `module_source`
10. Plan → approve → execute CR (installs selinux module via checkmodule + semodule)
11. Verify: `semodule -l | grep nexplane` confirms module installed; nginx still responds
12. Rollback CR → assert `rolled_back`; verify `semodule -l | grep nexplane` returns nothing
13. Second soak session → assert `synthesized` + `baseline_delta` with `added`/`removed`
14. `accept` → assert `cr_proposed` with new `cr_id`
15. Terminate instance

---

## File Map

| File | Status | Change |
|------|--------|--------|
| `backend/app/services/security_policy/plugins/selinux.py` | Create | SELinux plugin — AVC parsing, .te synthesis, delta extraction |
| `backend/app/services/security_policy/plugins/__init__.py` | Modify | Register `SELINUX_PLUGIN` |
| `backend/app/connectors/executors/nexplane_agent/selinux_learn.py` | Create | Backend executor for `selinux_learn` agent command |
| `backend/app/connectors/executors/nexplane_agent/configure_selinux.py` | Modify | Fix rollback bug: pass `config_snapshot` not `snapshot_id` |
| `agent/commands/linuxharden/selinux_learn_linux.go` | Create | Per-type permissive, AVC collection, returns `avc_lines` |
| `agent/commands/linuxharden/selinux_learn_other.go` | Create | Non-Linux stub |
| `agent/commands/linuxharden/linuxharden.go` | Modify | Export `SelinuxLearnExecute`, `SelinuxLearnRollback` |
| `agent/commands/ossecurity/selinux_linux.go` | Modify | Handle `module_source` param: compile .te → .pp → semodule -i |
| `agent/executor/executor.go` | Modify | Register `selinux_learn` in commands and rollbacks maps |
| `backend/tests/smoke/test_aws_live.py` | Modify | Add `SELINUX_AUTOGEN` phase |
| `backend/app/connectors/change_type_definitions/selinux_learn.json` | Create | Planning engine definition |

---

## AVC Line Parsing (pure Python)

AVC denial format:
```
type=AVC msg=audit(1234.567:890): avc:  denied  { read } for  pid=1234 comm="nginx" name="nginx.conf" dev="xvda1" ino=12345 scontext=system_u:system_r:httpd_t:s0 tcontext=system_u:object_r:etc_t:s0 tclass=file permissive=0
```

Parser extracts:
- `scontext` field → split by `:` → index 2 = source type (`httpd_t`)
- `tcontext` field → split by `:` → index 2 = target type (`etc_t`)
- `tclass` field = object class (`file`)
- Permission set from `{ read }` → `{"read"}`

Output tuple: `(stype, ttype, tclass, frozenset(perms))`

Generated allow rule: `allow httpd_t etc_t:file { read };`

Edge cases:
- Lines without `avc:  denied` are skipped
- Lines with `permissive=1` are included (these are the denials we want — permissive mode logs them without blocking)
- Multiple perms in `{ read write }` → one allow rule with merged perms
- `require {}` block groups unique types and classes

---

## Rollback Design

`configure_selinux` rollback (after fix):
```python
async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    return await _dispatch.dispatch_agent_job(
        command="configure_selinux",
        parameters={"config_snapshot": execution_result.get("config_snapshot", {})},
        asset_ids=asset_ids,
        timeout_seconds=120,
    )
```

Go `selinuxRollbackOS` (existing, no change needed):
1. Restores `previous_mode` via `setenforce`
2. Restores `/etc/selinux/config`
3. Removes installed modules via `semodule -r <name>`

The `config_snapshot` in the execute result contains `{previous_mode, selinux_config, modules_installed}` — already produced by `selinuxExecuteOS`. The Python executor fix is the only change needed on the Python side.

---

## Testing

**Unit tests** — `backend/tests/unit/test_selinux_plugin.py`:
- `test_synthesize_empty_observations` — empty → valid minimal .te module
- `test_synthesize_single_avc_line` — one AVC denial → correct allow rule
- `test_synthesize_merges_perms` — two denials same type pair → merged perms in one allow
- `test_delta_extract_roundtrip` — synthesize → delta_extract returns the allow rules
- `test_delta_empty_profile` — empty profile → empty set

**Live smoke** — `SELINUX_AUTOGEN` phase (Amazon Linux 2, mandatory before done).

---

## Constraints

- `selinux_learn` requires `semanage` — part of `policycoreutils-python-utils` on Amazon Linux 2; the smoke install step must include it: `yum install -y policycoreutils-python-utils`
- `checkmodule` and `semodule_package` are in `policycoreutils` (installed by default on AL2)
- The agent runs as root (via SSM) — `semanage` and `semodule` both require root
- Amazon Linux 2 uses `nginx` not `httpd` for the nginx binary, but the SELinux type is still `httpd_t` (nginx reuses the httpd policy on AL2)
- `semanage permissive -a httpd_t` requires SELinux to already be in enforcing mode on the host; the smoke phase must verify `getenforce` returns `Enforcing` before starting the soak

---

## Not In Scope

- SELinux boolean management (e.g., `setsebool`) — separate feature
- Multi-module composition (merging rules from multiple services into one module)
- RHEL 8/9 or CentOS support — Amazon Linux 2 only for now
- `audit2allow` on the backend — pure Python parsing only
