# Agent: Linux Security Hardening — Design Spec (Spec 5a)

**Date:** 2026-05-01
**Status:** Approved
**Scope:** New agent commands for Linux security hardening — MAC/sandboxing (SELinux, AppArmor, seccomp), kernel/network hardening (sysctl, host firewall, kernel module blacklisting, mount options), and monitoring/integrity (auditd, AIDE/Tripwire, eBPF). All commands are Linux-only. All become catalog actions in `nexplane_agent_mock.json` so they are selectable in projects and visible to the AI planning assistant.

---

## Design Decisions

- **Privilege model:** Agent must run as root (deployment requirement A). Each command runs an upfront capability preflight that returns a clear "insufficient privileges" error if the agent is not root, rather than failing mid-execution.
- **Rollback contract:** Every `Execute` that modifies system state must: (1) capture a snapshot of prior state, (2) include it in the returned result under a `"snapshot"` key, (3) implement `Rollback` that reads `snapshot` from `params` (merged from prior result) and restores it.
- **Package layout:** All commands in this spec live in `agent/commands/ossecurity/` (shared utility functions for file backup/restore, systemd reload, service restart). eBPF commands live in `agent/commands/ebpf/` due to heavier dependencies and Linux-5.8+ requirement.
- **Catalog integration:** Every command gets a `nexplane_agent_mock.json` catalog entry and a mock executor stub. This ensures commands are selectable when assembling projects and surface in the AI planning assistant's context.
- **OS-specific files:** All executor files in `ossecurity/` and `ebpf/` use `//go:build linux` build tags. No Windows implementations for this spec.

---

## Section 1: MAC and Sandboxing Commands

### 1.1 `configure_selinux`

**Parameters:**
- `mode` (string, optional) — `enforcing`, `permissive`, or `disabled`
- `policy_module_path` (string, optional) — path to a compiled `.pp` file or `.te` source on the agent host
- `generate_from_audit_log` (bool, optional, default `false`) — run `audit2allow` against recent AVC denials and install the generated module

At least one of `mode`, `policy_module_path`, or `generate_from_audit_log` must be provided.

**Preflight:**
- Checks `sestatus` is available and SELinux is supported by the kernel
- Checks caller is root
- If `generate_from_audit_log`: checks `ausearch` and `audit2allow` are available

**Execute:**
1. Snapshot: capture `getenforce` output and full contents of `/etc/selinux/config`
2. If `mode` supplied: `setenforce {0|1}` (immediate) + update `SELINUX=` line in `/etc/selinux/config` (persistent)
3. If `policy_module_path` supplied: `semodule -i {path}` — records installed module name in result
4. If `generate_from_audit_log`: run `ausearch -m avc -ts recent | audit2allow -M nexplane_generated` then `semodule -i nexplane_generated.pp`

**Result:** `{ previous_mode, new_mode, config_snapshot, modules_installed: [] }`

**Rollback:** `setenforce {previous_mode_int}` + restore `/etc/selinux/config` from snapshot + `semodule -r {module_name}` for each module installed during Execute

**Catalog entry:**
```json
{
  "action_id": "configure_selinux",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server"],
  "rollback_action": "configure_selinux",
  "safety_notes": ["Switching to enforcing mode may block services with unconfined profiles — test in permissive first"]
}
```

---

### 1.2 `configure_apparmor`

**Parameters:**
- `action` (string, required) — `load`, `enforce`, `complain`, `disable`, or `remove`
- `profile_name` (string, required) — name of the profile (used as filename under `/etc/apparmor.d/`)
- `profile_content` (string, optional) — inline AppArmor profile definition; if omitted, operates on an existing system profile

**Preflight:**
- Checks `apparmor_status` is available and AppArmor kernel module is loaded
- Checks caller is root
- If `profile_content` absent and action is not `remove`: checks profile already exists in `/etc/apparmor.d/`

**Execute:**
1. Snapshot: capture current mode of `profile_name` from `apparmor_status --json` (or parse text output)
2. If `profile_content` provided: write to `/etc/apparmor.d/nexplane-{profile_name}`, then `apparmor_parser -r /etc/apparmor.d/nexplane-{profile_name}`
3. Apply mode: `aa-enforce`, `aa-complain`, or `aa-disable {profile_name}`; for `remove`: `apparmor_parser -R` then delete the file if written by Nexplane

**Result:** `{ previous_state, profile_name, profile_path, action_applied }`

**Rollback:**
- If previous state was `enforce` → `aa-enforce {profile_name}`
- If previous state was `complain` → `aa-complain {profile_name}`
- If previous state was absent/disabled → `aa-disable {profile_name}` + remove file if Nexplane wrote it

**Catalog entry:**
```json
{
  "action_id": "configure_apparmor",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server"],
  "rollback_action": "configure_apparmor",
  "safety_notes": ["Enforcing a new profile without complain-mode testing may deny legitimate application access"]
}
```

---

### 1.3 `configure_seccomp`

**Parameters:**
- `target` (string, required) — `systemd_service` or `container`
- `service_name` (string, required for `systemd_service`) — name of the systemd service unit
- `profile` (object, required) — JSON seccomp profile (`{"defaultAction": "SCMP_ACT_ERRNO", "syscalls": [...]}`)
- `mode` (string, default `errno`) — `allow` (allowlist), `errno` (return EPERM on denied syscalls), or `kill` (SIGSYS on violation)
- `oci_config_path` (string, required for `container`) — path to OCI runtime config JSON

**Preflight:**
- Checks kernel version ≥ 3.17 (seccomp-bpf support via `uname -r`)
- Validates `profile` JSON is well-formed and contains required fields
- For `systemd_service`: checks `systemctl` available and service exists
- Checks caller is root

**Execute:**
1. Snapshot:
   - For `systemd_service`: capture existing `/etc/systemd/system/{service}.d/99-nexplane-seccomp.conf` content (empty string if absent)
   - For `container`: capture current `seccomp` section of OCI config
2. For `systemd_service`: write drop-in with `SystemCallFilter=` (allowlist from profile) and `SystemCallErrorNumber=EPERM` (or `SIGSYS` for kill mode); then `systemctl daemon-reload && systemctl restart {service}`
3. For `container`: inject `seccomp` key into OCI runtime config JSON; runtime picks it up on next container start

**Result:** `{ target, service_name, drop_in_path, previous_drop_in_content, syscalls_count, mode_applied }`

**Rollback:**
- For `systemd_service`: write `previous_drop_in_content` back (or remove file if it was absent) → `systemctl daemon-reload && systemctl restart {service}`
- For `container`: restore previous OCI config seccomp section

**Catalog entry:**
```json
{
  "action_id": "configure_seccomp",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server"],
  "rollback_action": "configure_seccomp",
  "safety_notes": [
    "kill mode (SIGSYS) will crash processes that trigger the filter — use errno for initial rollout",
    "Service will restart during configuration — schedule a maintenance window"
  ]
}
```

---

## Section 2: Kernel and Network Hardening Commands

### 2.1 `apply_sysctl_hardening`

**Parameters:**
- `profile` (string, default `cis_level1`) — `cis_level1`, `cis_level2`, or `custom`
- `params` (map `string→string`, optional) — additional or override key/value pairs; required if `profile=custom`

**Built-in profiles** (non-exhaustive):

| Key | CIS L1 | CIS L2 |
|-----|--------|--------|
| `net.ipv4.ip_forward` | `0` | `0` |
| `net.ipv4.conf.all.send_redirects` | `0` | `0` |
| `net.ipv4.conf.all.accept_redirects` | `0` | `0` |
| `net.ipv4.tcp_syncookies` | `1` | `1` |
| `net.ipv4.conf.all.rp_filter` | `1` | `1` |
| `kernel.randomize_va_space` | `2` | `2` |
| `fs.suid_dumpable` | `0` | `0` |
| `kernel.dmesg_restrict` | — | `1` |
| `net.ipv6.conf.all.disable_ipv6` | — | `1` |

**Preflight:**
- Checks `sysctl` is available
- Validates all target keys exist in `/proc/sys/` before applying any (fail fast if unknown key)
- Checks caller is root

**Execute:**
1. Snapshot: `sysctl -n {key}` for every target key → stored as `{key: current_value}` map
2. Write `/etc/sysctl.d/99-nexplane-hardening.conf` with all key=value pairs
3. `sysctl --system` to apply immediately

**Result:** `{ params_applied, config_path, snapshot }`

**Rollback:** For each key in snapshot: `sysctl -w {key}={previous_value}`; remove `/etc/sysctl.d/99-nexplane-hardening.conf`

---

### 2.2 `configure_host_firewall`

**Parameters:**
- `action` (string, required) — `add_rule`, `remove_rule`, `flush_chain`, or `set_default_policy`
- `chain` (string) — `INPUT`, `OUTPUT`, `FORWARD`
- `rule` (string) — iptables-style rule specification (e.g. `-p tcp --dport 22 -j ACCEPT`)
- `ip_version` (string, default `4`) — `4`, `6`, or `both`
- `table` (string, default `filter`) — `filter`, `nat`, or `mangle`
- `policy` (string, for `set_default_policy`) — `ACCEPT` or `DROP`

**Tool detection order:** nftables → iptables/ip6tables → firewalld

**Preflight:**
- Checks at least one firewall tool is available
- For `add_rule` / `remove_rule`: validates rule syntax using `iptables-restore --test` or `nft -c`
- Checks caller is root

**Execute:**
1. Snapshot: `iptables-save` / `nft list ruleset` — full ruleset dump as string
2. Apply the requested action using the detected tool

**Result:** `{ tool_used, action, rule, chain, ruleset_snapshot }`

**Rollback:** Write snapshot string to temp file → `iptables-restore < {tmpfile}` or `nft -f {tmpfile}`; delete temp file

---

### 2.3 `blacklist_kernel_modules`

**Parameters:**
- `modules` (list of strings, required) — module names to blacklist (e.g. `["usb-storage", "firewire-core", "cramfs"]`)
- `unload_immediately` (bool, default `true`) — attempt `modprobe -r` for currently loaded modules

**Preflight:**
- Checks `/etc/modprobe.d/` exists
- Checks caller is root
- Checks which modules are currently in use (warns in result but does not block)

**Execute:**
1. Snapshot: `lsmod` output filtered to listed modules + existing `/etc/modprobe.d/nexplane-blacklist.conf` content
2. Write/append to `/etc/modprobe.d/nexplane-blacklist.conf`: `install {module} /bin/false` for each module
3. If `unload_immediately`: `modprobe -r {module}` for each loaded module (best-effort — logs failures for in-use modules but continues)

**Result:** `{ modules_blacklisted, modules_unloaded, modules_in_use, config_snapshot }`

**Rollback:** Restore `/etc/modprobe.d/nexplane-blacklist.conf` from `config_snapshot` (or remove file if it was absent). Note: unloaded modules are not reloaded — this is intentional. If reloading is needed it must be done manually or via reboot.

---

### 2.4 `harden_mount_options`

**Parameters:**
- `targets` (list of strings, default `["/tmp", "/dev/shm", "/run/shm"]`) — mount points to harden
- `options` (list of strings, default `["noexec", "nosuid", "nodev"]`) — options to add

**Preflight:**
- Checks each target is a currently mounted filesystem (reads `/proc/mounts`)
- Checks caller is root

**Execute:**
1. Snapshot: capture current mount options for each target from `/proc/mounts`; capture `/etc/fstab` contents
2. For each target: `mount -o remount,{existing_opts},{new_opts} {target}`
3. Update `/etc/fstab`: add new options to existing entry (or add a bind mount entry if target not in fstab)

**Result:** `{ targets_hardened, options_applied, mount_snapshot, fstab_snapshot }`

**Rollback:** For each target: `mount -o remount,{original_opts} {target}` using snapshot; restore `/etc/fstab` from `fstab_snapshot`

---

## Section 3: Monitoring and Integrity Commands

### 3.1 `deploy_auditd_rules`

**Parameters:**
- `profile` (string, default `cis_level1`) — `cis_level1`, `cis_level2`, `stig`, or `custom`
- `rules` (list of strings, required if `profile=custom`) — raw `auditctl` rule strings
- `rule_file_name` (string, default `99-nexplane`) — filename under `/etc/audit/rules.d/` (without `.rules` extension)

**Built-in profile content (examples):**
- CIS L1: file access on `/etc/passwd`, `/etc/shadow`, `/etc/group`, `/etc/sudoers`; privileged command execution; network config modification
- CIS L2: CIS L1 + module loading, time change, user/group modification, session initiation
- STIG: DISA STIG-mapped rule set for RHEL/CentOS

**Preflight:**
- Checks `auditctl` and `auditd` service are available and running
- Validates custom rules syntax via `auditctl --check` (dry-run equivalent)
- Checks caller is root

**Execute:**
1. Snapshot: `auditctl -l` (current active rules) + existing `/etc/audit/rules.d/{rule_file_name}.rules` content
2. Write `/etc/audit/rules.d/{rule_file_name}.rules`
3. `augenrules --load` to merge and reload all rule files

**Result:** `{ rules_file, rules_count, previous_rules_snapshot, file_snapshot }`

**Rollback:** Remove `/etc/audit/rules.d/{rule_file_name}.rules` (or restore previous content) → `augenrules --load`

---

### 3.2 `setup_file_integrity_monitoring`

**Parameters:**
- `tool` (string, optional) — `aide` or `tripwire`; auto-detected from installed binaries if omitted
- `action` (string, required) — `init`, `check`, or `update`
- `paths` (list of strings, optional) — additional paths to monitor beyond tool defaults

**Preflight:**
- Checks selected tool is installed
- For `check` and `update`: confirms database file exists (fails with clear error if `init` has not been run)
- Checks caller is root

**Execute:**
- `init`: write config additions if `paths` supplied → `aide --init` or `tripwire --init`. Snapshot: existing config file content.
- `check`: `aide --check` or `tripwire --check`. Returns diff output. Read-only — no system state changed.
- `update`: `aide --update` or `tripwire --update` (accepts current state as new baseline). Snapshot: copy of current DB before overwriting.

**Result:** `{ tool, action, db_path, config_snapshot, diff_output (check only), changed_files_count (check only) }`

**Rollback:**
- `init`: remove database file + restore config from snapshot
- `update`: restore previous DB from snapshot
- `check`: no-op

---

### 3.3 `deploy_ebpf_policy`

**Parameters:**
- `program_path` (string, required) — path to compiled `.o` file on the agent host
- `attach_type` (string, required) — `kprobe`, `tracepoint`, `tc`, `xdp`, `lsm`, or `cgroup`
- `attach_target` (string, required) — function name (kprobe), network interface (tc/xdp), cgroup path, etc.
- `framework` (string, default `raw`) — `raw`, `cilium`, `falco`, or `tetragon`
- `policy_name` (string, required for framework modes) — identifier for the policy

**Preflight:**
- Checks kernel version ≥ 5.8 for LSM/BTF support (`uname -r`)
- For `raw`: checks `bpftool` available; validates program passes verifier via dry-run (`bpftool prog load {path} /dev/null 2>&1`)
- For framework modes: checks framework daemon is running
- Checks caller is root

**Execute:**
1. Snapshot: `bpftool prog list --json` + `bpftool net list --json` → current program IDs and attachment points
2. For `raw`: `bpftool prog load {path} /sys/fs/bpf/nexplane-{name}` then `bpftool net attach {type} pinned /sys/fs/bpf/nexplane-{name} dev {target}` (or equivalent for non-net attach types)
3. For framework modes: `cilium policy import`, Falco rule deploy, or Tetragon policy apply

**Result:** `{ program_id, pin_path, attach_point, framework, snapshot }`

**Rollback:**
- For `raw`: `bpftool net detach {type} dev {target}` then `rm /sys/fs/bpf/nexplane-{name}` then `bpftool prog unload`
- For framework modes: delete/disable the named policy

---

### 3.4 `configure_ebpf_security_policy`

**Parameters:**
- `framework` (string, required) — `cilium`, `falco`, or `tetragon`
- `policy` (string, required) — YAML or JSON policy definition (framework-native format)
- `policy_name` (string, required) — identifier for listing and deletion

**Preflight:**
- Checks framework daemon is running
- Validates policy syntax via framework's validation command
- Checks caller is root

**Execute:**
1. Snapshot: export current policy list from framework (`cilium policy get`, `falco --list`, `kubectl get tracingpolicies` for Tetragon)
2. Apply policy via framework CLI

**Result:** `{ framework, policy_name, policy_applied, snapshot }`

**Rollback:** Delete named policy via framework CLI (`cilium policy delete`, etc.); restore does not replay all prior policies — only removes the one added.

---

### 3.5 `audit_os_security_posture` (read-only ingest)

No parameters required.

Returns and enriches asset metadata with:
- SELinux mode (`getenforce`) and loaded custom modules (`semodule -l`)
- AppArmor profile states (`apparmor_status --json`)
- Active seccomp filters on key processes (reads `/proc/{pid}/status` for `Seccomp:` field for systemd, sshd, and any processes matching the asset's application inventory)
- Recent AVC/AppArmor denials (`ausearch -m avc -ts recent`, `dmesg | grep apparmor`)

Asset tags added: `selinux-enforcing` / `selinux-permissive` / `selinux-disabled`, `apparmor-enabled`, `seccomp-active`

No rollback — read-only.

---

### 3.6 `audit_ebpf_posture` (read-only ingest)

No parameters required.

Returns and enriches asset metadata with:
- All loaded eBPF programs: ID, name, type, loading PID and process name (`bpftool prog list --json`)
- All network attachment points (`bpftool net list --json`)
- Programs not in the Nexplane-deployed set are flagged with tag `ebpf-unrecognized` — potential implant signal

Asset tags added: `ebpf-programs-present`, `ebpf-unrecognized` (if applicable)

No rollback — read-only.

---

## Section 4: New Files

| File | Purpose |
|------|---------|
| `agent/commands/ossecurity/ossecurity.go` | Package entry: `Execute`/`Rollback` dispatch for all ossecurity commands |
| `agent/commands/ossecurity/selinux_linux.go` | `configure_selinux` implementation |
| `agent/commands/ossecurity/apparmor_linux.go` | `configure_apparmor` implementation |
| `agent/commands/ossecurity/seccomp_linux.go` | `configure_seccomp` implementation |
| `agent/commands/ossecurity/sysctl_linux.go` | `apply_sysctl_hardening` implementation |
| `agent/commands/ossecurity/firewall_linux.go` | `configure_host_firewall` implementation |
| `agent/commands/ossecurity/modules_linux.go` | `blacklist_kernel_modules` implementation |
| `agent/commands/ossecurity/mounts_linux.go` | `harden_mount_options` implementation |
| `agent/commands/ossecurity/auditd_linux.go` | `deploy_auditd_rules` implementation |
| `agent/commands/ossecurity/fim_linux.go` | `setup_file_integrity_monitoring` implementation |
| `agent/commands/ossecurity/posture_linux.go` | `audit_os_security_posture` ingest implementation |
| `agent/commands/ossecurity/ossecurity_test.go` | Validation tests (build tag: linux OR windows for param tests) |
| `agent/commands/ebpf/ebpf.go` | Package entry |
| `agent/commands/ebpf/ebpf_linux.go` | `deploy_ebpf_policy`, `configure_ebpf_security_policy`, `audit_ebpf_posture` |
| `agent/commands/ebpf/ebpf_test.go` | Validation tests |

## Section 5: Modified Files

| File | Change |
|------|--------|
| `agent/executor/executor.go` | Add all 10 new commands to `commands` and `rollbacks` maps |
| `backend/app/connectors/catalog/nexplane_agent_mock.json` | Add catalog entries for all 10 commands |
| `backend/app/connectors/executors/nexplane_agent_mock/` | Add mock executor stubs for all 10 commands |
| `backend/app/tests/test_catalog_service.py` | Action count assertions updated if present |
