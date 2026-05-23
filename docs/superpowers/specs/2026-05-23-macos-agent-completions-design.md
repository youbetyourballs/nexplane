# Spec A: macOS Agent & Backend Completions

**Date:** 2026-05-23
**Status:** Approved

## Goal

Complete macOS support in Nexplane: fill the gaps in agent commands, wire every command into the backend (change type definitions + Python executors + catalog), add `profiles_install`/remove, expand Santa per-device management, and stand up smoke infrastructure on real Mac hardware.

## Scope

1. Backend wiring for existing agent commands missing backend plumbing
2. New agent commands: `profiles_install`, `profiles_remove`, `homebrew_list`, and seven Santa commands
3. Smoke infrastructure: mac2.metal Dedicated Host strategy + MAC_AGENT_BOOTSTRAP extension

---

## Section 1: Agent Commands (Go)

### Already implemented, backend wiring missing

| Agent command | Status |
|---|---|
| `defaults_write` | Go done, no backend |
| `santa_check` | Go done, no backend |

### New Go agent commands

All live in `agent/commands/macos/`. Build constraints: `macos_darwin.go` for real implementations, `macos_other.go` stubs returning `not_supported` on non-Darwin.

#### `profiles_install`

```
params: plist_b64 (base64-encoded .mobileconfig content)
```

- Writes plist to a temp file, runs `profiles install -path <tmp>`
- Captures the profile `PayloadIdentifier` from the plist before installing (parsed from XML)
- Returns `{"identifier": "<id>", "installed": true}`
- Rollback: `profiles remove -identifier <id>` using the stored identifier

#### `profiles_remove`

```
params: identifier (profile PayloadIdentifier string)
```

- Reads current profile plist via `profiles list -output stdout-xml`, extracts the matching profile's full plist content, stores as `previous_plist_b64` in result
- Runs `profiles remove -identifier <id>`
- Rollback: write plist back to temp file, `profiles install -path <tmp>`

#### `homebrew_list`

```
params: none
```

- Runs `brew list --versions` if Homebrew is present; returns `{"installed": false}` if `brew` not found
- Returns `{"packages": [{"name": "...", "version": "..."}], "installed": true}`
- Read-only, no rollback

#### `santa_rule_add`

```
params: rule_type (allowlist|denylist|silent_blocklist), identifier_type (binary|certificate|teamid|signingid), identifier (SHA-256 or cert hash or team/signing ID), custom_message (optional)
```

- First checks existing rule: `santactl rule --check --sha256 <id>` (or equivalent), stores result as `previous_state` (absent | allow | deny)
- Runs `santactl rule --add --<rule_type> --<identifier_type> <id>`
- Returns `{"added": true, "previous_state": "absent|allow|deny", "identifier": "..."}`
- Rollback: if `previous_state` was absent → `santactl rule --remove`, else → restore via `santactl rule --add --<previous_type>`

#### `santa_rule_remove`

```
params: identifier_type, identifier
```

- Checks existing rule state before removing (same check as santa_rule_add)
- Runs `santactl rule --remove --<identifier_type> <id>`
- Returns `{"removed": true, "previous_state": "allow|deny"}`
- Rollback: re-add with `previous_state` policy type

#### `santa_rule_list`

```
params: none
```

- Runs `santactl rule --list` (or reads `/var/db/santa/rules.db` via `santactl` export if available)
- Returns `{"rules": [{"identifier": "...", "type": "allowlist|denylist", "identifier_type": "..."}]}`
- If Santa not installed: `{"installed": false, "rules": []}`
- Read-only, no rollback

#### `santa_mode_set`

```
params: mode (monitor|lockdown)
```

- Reads current mode via `santactl status` → parses `Mode` field → stores as `previous_mode`
- Sets mode by writing to Santa configuration plist: `defaults write /Library/Preferences/com.google.santa ClientMode -int <1|2>` then `santactl sync --clean` to apply
- Returns `{"mode": "monitor|lockdown", "previous_mode": "..."}`
- Rollback: restore `previous_mode` via same mechanism

#### `santa_sync_trigger`

```
params: none
```

- Runs `santactl sync`
- Returns `{"synced": true, "output": "..."}` or `{"synced": false, "error": "..."}` if no sync server configured
- No rollback (action-only)

#### `santa_event_export`

```
params: limit (optional int, default 100)
```

- Runs `santactl log` or reads `/var/db/santa/santa.log` to extract recent blocked/allowed events
- Returns `{"events": [{"timestamp": "...", "decision": "DENY|ALLOW", "path": "...", "sha256": "...", "user": "..."}]}`
- Read-only, no rollback

#### `santa_binary_check`

```
params: path (full path to binary on the Mac)
```

- Runs `santactl check --path <path>`
- Returns `{"path": "...", "decision": "ALLOW|DENY|UNKNOWN", "sha256": "...", "rule_type": "..."}`
- Read-only, no rollback

---

## Section 2: Backend Wiring

### Pattern

Each command gets:
1. `backend/app/connectors/change_type_definitions/macos_<command>.json` — schema with params, risk level, rollback flag
2. `backend/app/connectors/executors/nexplane_agent/macos_<command>.py` — thin executor that calls agent via `run_agent_command(asset_ids, connector, "<command>", parameters)`
3. Entry in `backend/app/connectors/catalog/nexplane_agent.json` under `"change_types"`

### Files to create

**Wiring for existing commands:**
- `macos_defaults_write.json` + `macos_defaults_write.py`
- `macos_santa_check.json` + `macos_santa_check.py`

**Wiring for new commands:**
- `macos_profiles_install.json` + `macos_profiles_install.py`
- `macos_profiles_remove.json` + `macos_profiles_remove.py`
- `macos_homebrew_list.json` + `macos_homebrew_list.py`
- `macos_santa_rule_add.json` + `macos_santa_rule_add.py`
- `macos_santa_rule_remove.json` + `macos_santa_rule_remove.py`
- `macos_santa_rule_list.json` + `macos_santa_rule_list.py`
- `macos_santa_mode_set.json` + `macos_santa_mode_set.py`
- `macos_santa_sync_trigger.json` + `macos_santa_sync_trigger.py`
- `macos_santa_event_export.json` + `macos_santa_event_export.py`
- `macos_santa_binary_check.json` + `macos_santa_binary_check.py`

### Risk levels

| Command | Risk | Has rollback |
|---|---|---|
| `defaults_write` | medium | yes |
| `profiles_install` | medium | yes |
| `profiles_remove` | high | yes |
| `santa_rule_add` | medium | yes |
| `santa_rule_remove` | high | yes |
| `santa_mode_set` | high | yes |
| `santa_sync_trigger` | low | no |
| `homebrew_list` | low | no |
| `santa_check` | low | no |
| `santa_rule_list` | low | no |
| `santa_event_export` | low | no |
| `santa_binary_check` | low | no |

### Executor rollback registration

Add rollback handlers in `agent/executor/executor.go` for new write commands (same map as existing macOS rollbacks):

```go
"profiles_install":   macos.ProfilesInstallRollback,
"profiles_remove":    macos.ProfilesRemoveRollback,
"santa_rule_add":     macos.SantaRuleAddRollback,
"santa_rule_remove":  macos.SantaRuleRemoveRollback,
"santa_mode_set":     macos.SantaModeSetRollback,
```

---

## Section 3: Smoke Infrastructure

### Constraint

EC2 mac2.metal requires an **Allocated Dedicated Host** (24h minimum billing, ~$0.906/hr ≈ $21.74/day). The instance itself can be started and stopped, but the host clock runs continuously. This is an accepted cost for macOS coverage.

### Strategy: Persistent Dedicated Host + AMI Cache

**One-time setup (manual, documented as a runbook):**
1. Allocate mac2.metal Dedicated Host in us-east-1 via AWS console or CLI
2. Launch a mac2.metal instance on that host using the latest `amzn-ec2-macos-*` AMI
3. Wait for SSH availability (~15 min for mac2.metal cold boot)
4. Install Nexplane agent, verify it registers
5. Snapshot as AMI; store ID in SSM at `/nexplane/smoke-amis/mac/arm64/base`
6. Store Dedicated Host ID in SSM at `/nexplane/smoke/mac/dedicated-host-id`

**Per-run (in MAC_AGENT_BOOTSTRAP phase):**
1. Read host ID from SSM; skip phase if absent
2. Read cached AMI from SSM at `/nexplane/smoke-amis/mac/arm64/base`
3. Launch mac2.metal instance on the dedicated host from cached AMI
4. Wait for SSH (~10–12 min from AMI vs ~15 min cold)
5. Run all macOS smoke commands via the registered agent
6. Terminate instance (host remains allocated)

### MAC_AGENT_BOOTSTRAP extension

Extend the existing phase to cover all new commands after the current `defaults_write` + `santa_check` tests:

```
1. defaults_write (existing)       — write + verify + rollback
2. santa_check (existing)          — read status
3. profiles_install                — install a test .mobileconfig + verify + rollback
4. homebrew_list                   — read (graceful if Homebrew absent)
5. santa_rule_add (denylist)       — add test SHA-256 + verify via santa_rule_list + rollback
6. santa_mode_set (monitor)        — set MONITOR if currently LOCKDOWN + rollback
7. santa_event_export              — read recent events
8. santa_binary_check              — check /usr/bin/true
9. santa_sync_trigger              — trigger sync (graceful if no server configured)
```

### Cost notes

- Dedicated Host: ~$21.74/day ongoing whether or not tests run
- MAC_AGENT_BOOTSTRAP instance: ~$0.906/hr × ~30 min = ~$0.45/run
- Total per-run cost: ~$0.45 + host amortization
- Recommendation: run MAC_AGENT_BOOTSTRAP nightly (not per-commit) to amortize host cost

---

## Out of scope

- Jamf Pro connector (requires 30-day trial, no permanent free tier)
- Apple Business Manager (limited API, low priority)
- FileVault escrow key management (requires MDM enrollment, future work)
- macOS asset discovery (covered by existing `macos_sysinfo` + agent fingerprint)
