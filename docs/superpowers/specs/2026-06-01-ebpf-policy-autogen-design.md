# eBPF Policy Auto-Generation Design

**Date:** 2026-06-01
**Status:** Approved

---

## Goal

Observe running workload behavior via eBPF and automatically synthesize two independent security policies: a network egress policy (which connections a workload is allowed to make) and an LSM kernel policy (which syscalls + file paths + processes are allowed). Both start in audit mode; operators review violations before explicitly promoting to enforce.

---

## Background

The security policy autogen pipeline (soak → observe → synthesize → diff → CR) already exists and is smoke-tested for seccomp, AppArmor, and SELinux. The plugin registry explicitly reserved a `network_policy` slot. The agent already has a `deploy_ebpf_policy` executor for loading compiled eBPF programs. This feature fills the remaining gap: observation executors that capture network flows and kernel events via eBPF, two synthesizer plugins that convert observations into structured policy, and an audit→enforce promotion path.

---

## Not In Scope

- Building a BPF compiler toolchain or requiring clang/kernel headers on target hosts (pre-compiled eBPF skeleton programs ship with the agent binary)
- Kubernetes NetworkPolicy integration (eBPF network policy enforces at the host TC/cgroup level only)
- eBPF-based IDS/alerting (that is Falco's domain — this feature is policy generation only)
- Automatic promotion to enforce (always operator-gated)

---

## Architecture

Two independent plugins slot into the existing pipeline. Each has its own soak session, synthesizer, baseline, and CR lifecycle. They can be run and tuned independently.

### Observation

Two new agent commands load pre-compiled eBPF observer programs, run for `window_seconds`, unload, and return structured event data:

- **`ebpf_network_soak`** — loads a TC + cgroup BPF observer, captures `{dst_ip, dst_port, protocol, process, count}` tuples per outbound connection
- **`ebpf_lsm_soak`** — loads an LSM/kprobe BPF observer, captures `{syscall, path, process, uid, count}` tuples per kernel event

### Synthesis

Two new plugin modules convert raw observations into structured policy profiles stored as JSONB on the soak session:

**`ebpf_network` profile:**
```json
{
  "rules": [
    {"dst_cidr": "10.0.0.0/8", "dst_port": 443, "protocol": "tcp", "process": "nginx"},
    {"dst_cidr": "0.0.0.0/0",  "dst_port": 53,  "protocol": "udp", "process": "*"}
  ],
  "default_action": "audit"
}
```

**`ebpf_lsm` profile:**
```json
{
  "rules": [
    {"syscall": "open",   "path_pattern": "/etc/*",     "process": "nginx", "action": "allow"},
    {"syscall": "execve", "path_pattern": "/usr/bin/*",  "process": "*",     "action": "allow"}
  ],
  "default_action": "audit"
}
```

### Enforcement

Pre-compiled eBPF enforcer programs ship with the agent binary — no clang or kernel headers required on target hosts. Policy is data loaded into BPF maps; audit→enforce is a single map flag write, not a recompile.

- **Audit mode:** violations written to a BPF ring buffer, returned as structured findings. Traffic and syscalls pass through.
- **Enforce mode:** TC hook drops packets for disallowed connections; LSM hook returns `EPERM` for disallowed kernel events.

BPF maps are pinned to `/sys/fs/bpf/nexplane/<asset_id>/network/` and `/sys/fs/bpf/nexplane/<asset_id>/lsm/` for persistence across agent restarts.

**Kernel requirements:**
- TC eBPF (network): kernel 4.1+ — universally supported
- eBPF LSM: kernel 5.7+ with `CONFIG_BPF_LSM=y` and `lsm=bpf` in kernel boot parameters
- LSM fallback: if kernel capability not detected at runtime, `configure_ebpf_lsm` falls back to kprobe-based enforcement and sets `kernel_lsm: false` in the execution result

### Audit → Enforce Promotion

A dedicated `promote_ebpf_policy` CR type writes `"enforce"` to the mode map entry at the pinned BPF path. Rollback writes `"audit"` back. This is the only mechanism for moving from audit to enforce — there is no automatic promotion.

---

## Data Model

No new tables. Two new `policy_type` values added to existing CHECK constraints via migration.

### Migration `068_ebpf_policy_types.py`

```sql
ALTER TABLE security_policy_soak_sessions
  DROP CONSTRAINT IF EXISTS security_policy_soak_sessions_policy_type_check,
  ADD CONSTRAINT security_policy_soak_sessions_policy_type_check
    CHECK (policy_type IN ('seccomp','apparmor','selinux','ebpf_network','ebpf_lsm'));

ALTER TABLE security_policy_baselines
  DROP CONSTRAINT IF EXISTS security_policy_baselines_policy_type_check,
  ADD CONSTRAINT security_policy_baselines_policy_type_check
    CHECK (policy_type IN ('seccomp','apparmor','selinux','ebpf_network','ebpf_lsm'));
```

### New ChangeType enum values

- `ebpf_network_soak`
- `configure_ebpf_network`
- `configure_ebpf_lsm`
- `promote_ebpf_policy`

### Delta extraction for baselines

- `ebpf_network`: diffs on `(dst_cidr, dst_port, protocol)` tuples → `{added: [...], removed: [...]}`
- `ebpf_lsm`: diffs on `(syscall, path_pattern, process)` tuples → `{added: [...], removed: [...]}`

---

## File Map

### Backend — new files

| File | Purpose |
|------|---------|
| `alembic/versions/068_ebpf_policy_types.py` | Add `ebpf_network`, `ebpf_lsm` to both CHECK constraints |
| `services/security_policy/plugins/ebpf_network.py` | Synthesis: flows → NetworkPolicy; delta on CIDR+port+protocol tuples |
| `services/security_policy/plugins/ebpf_lsm.py` | Synthesis: kernel events → LSMPolicy; delta on syscall+path+process tuples |
| `connectors/executors/nexplane_agent/ebpf_network_soak.py` | Dispatch `ebpf_network_soak` agent command |
| `connectors/executors/nexplane_agent/ebpf_lsm_soak.py` | Dispatch `ebpf_lsm_soak` agent command |
| `connectors/executors/nexplane_agent/configure_ebpf_network.py` | Dispatch `configure_ebpf_network`; rollback unloads program and clears maps |
| `connectors/executors/nexplane_agent/configure_ebpf_lsm.py` | Dispatch `configure_ebpf_lsm`; rollback unloads and clears |
| `connectors/executors/nexplane_agent/promote_ebpf_policy.py` | Dispatch `promote_ebpf_policy`; rollback writes `audit` back to mode map |
| `connectors/catalog/` (5 new JSON files) | CR type catalog definitions for all new change types |

### Backend — modified files

| File | Change |
|------|--------|
| `models/change_request.py` | Add 4 new `ChangeType` enum values |
| `services/security_policy/plugins/__init__.py` | Register `EBPF_NETWORK_PLUGIN` and `EBPF_LSM_PLUGIN` |

### Agent-side commands (Go — specify behavior, implementation in agent repo)

| Command | Behavior |
|---------|---------|
| `ebpf_network_soak` | Load pre-compiled TC+cgroup observer .bpf.o; capture flow tuples for `window_seconds`; unload; return `{flows: [{dst_ip, dst_port, protocol, process, count}]}` |
| `ebpf_lsm_soak` | Load pre-compiled LSM/kprobe observer .bpf.o; capture kernel events for `window_seconds`; unload; return `{events: [{syscall, path, process, uid, count}]}` |
| `configure_ebpf_network` | Load network enforcer .bpf.o; write rules into BPF map; set mode=audit; pin maps to `/sys/fs/bpf/nexplane/<asset_id>/network/`; snapshot prior state for rollback |
| `configure_ebpf_lsm` | Load LSM enforcer .bpf.o (kprobe fallback if kernel < 5.7 or no LSM support); write rules into BPF map; set mode=audit; pin maps to `/sys/fs/bpf/nexplane/<asset_id>/lsm/`; set `kernel_lsm: true/false` in result |
| `promote_ebpf_policy` | Accept `policy_type` (network/lsm) and `asset_id`; write `enforce` to mode map entry at pinned path; return `{prior_mode: "audit"}` for rollback |

---

## Smoke Test — Phase `EBPF_POLICY`

Uses the existing smoke EC2 instance (phase A result). Runs both network and LSM legs sequentially.

**Network leg:**
1. Start `ebpf_network_soak` soak session — 30s window on smoke EC2 asset
2. Assert flow tuples returned (DNS port 53 always present on any Linux host)
3. Synthesize → `configure_ebpf_network` CR (audit mode) → assert CR completed, maps pinned
4. Run known-blocked connection from the instance (`curl` to a non-allowlisted IP) → assert violation appears in audit log
5. `promote_ebpf_policy` CR → enforce mode → repeat connection attempt → assert blocked (curl fails)
6. Rollback `promote_ebpf_policy` → assert mode returns to audit
7. Rollback `configure_ebpf_network` → assert maps unloaded (pinned path gone)

**LSM leg:**
1. Start `ebpf_lsm_soak` soak session — 30s window on same asset
2. Assert kernel events returned (file opens always present)
3. Synthesize → `configure_ebpf_lsm` CR (audit mode) → assert CR completed, note `kernel_lsm` field
4. Attempt a file open outside the observed path set → assert violation in audit log
5. `promote_ebpf_policy` CR → enforce mode → repeat file open attempt → assert `EPERM`
6. Rollback `promote_ebpf_policy` → back to audit
7. Rollback `configure_ebpf_lsm` → maps unloaded

---

## Testing

### Unit tests — `backend/tests/unit/test_ebpf_policy_plugins.py`

- `test_ebpf_network_synthesize` — flow tuples → NetworkPolicy rules; wildcard process produces `process: "*"`
- `test_ebpf_network_delta` — added/removed CIDR+port+protocol tuples between two profiles
- `test_ebpf_lsm_synthesize` — kernel events → LSMPolicy rules; deduplication on syscall+path+process
- `test_ebpf_lsm_delta` — added/removed syscall+path+process tuples between two profiles
- `test_plugin_registry` — both plugins registered and retrievable via `get_plugin()`
- `test_ebpf_network_default_action` — synthesized profile always has `default_action: "audit"`
- `test_ebpf_lsm_default_action` — same
