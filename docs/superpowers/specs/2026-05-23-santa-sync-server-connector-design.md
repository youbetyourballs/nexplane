# Spec B: Santa Sync Server Connector

**Date:** 2026-05-23
**Status:** Approved

## Goal

Add a `santa_sync_server` connector that manages fleet-level Santa policy via the open Santa sync protocol. Compatible with Moroz and Zentral out of the box. Structured for future North Pole commercial integration as a credential variant or separate connector type once their API surface is known.

---

## Section 1: Connector Type

**Connector type ID:** `santa_sync_server`

**Credential fields:**

| Field | Required | Description |
|---|---|---|
| `sync_server_url` | yes | Base URL, e.g. `https://moroz.internal` |
| `auth_token` | yes | Bearer token (Moroz) or session token (Zentral) |
| `default_machine_group` | no | Default rule group name for push operations |
| `machine_id_field` | no | Which machine identifier field to use: `hardware_uuid` (default) or `serial` |
| `tls_verify` | no | Default `true`; set `false` for self-signed internal CA |

**Asset type:** `macos_fleet` — a virtual asset representing the Santa-managed fleet connected to this sync server. Not a physical host; used as the target for fleet-level CRs.

---

## Section 2: Santa Sync Protocol

The open Santa sync protocol is a JSON-over-HTTPS protocol with the following endpoints:

| Endpoint | Purpose |
|---|---|
| `POST /preflight/<machine-id>` | Machine checks in, receives configuration |
| `POST /ruledownload/<machine-id>` | Machine downloads rule set |
| `POST /eventupload/<machine-id>` | Machine uploads blocked/allowed events |
| `POST /postflight/<machine-id>` | Machine reports sync completion |

Nexplane acts as a **sync server client** for the audit/push actions (policy_audit, push_rules) and does not implement the full server-side protocol. For `santa_policy_audit`, Nexplane calls the sync server's management API (Moroz: `/rules` endpoint via admin API; Zentral: `/api/santa/rules/`).

Both Moroz and Zentral expose a management REST API alongside the Santa sync protocol endpoints. Nexplane uses the management API, not the device sync endpoints.

---

## Section 3: Actions

### `santa_policy_audit`

Reads the current rule set for a machine group from the sync server.

```
params: machine_group (optional, defaults to default_machine_group credential)
```

- Calls `GET /api/santa/rules/?configuration=<group>` (Zentral) or `GET /rules` (Moroz)
- Returns `{"rules": [...], "machine_group": "...", "rule_count": N}`
- Feeds into Findings: rules with `custom_message` containing known CVE IDs or suspicious paths are surfaced as informational findings
- Read-only, no rollback

### `santa_push_rules`

Pushes a rule set to a machine group on the sync server. Machines receive the new rules on next sync.

```
params:
  rules: list of {rule_type, identifier_type, identifier, custom_message (optional)}
  machine_group: optional
  mode: replace | merge (default: merge)
```

- In `merge` mode: adds/updates the specified rules, leaves existing rules untouched
- In `replace` mode: replaces the entire rule set for the group
- **Before pushing:** calls `santa_policy_audit` and stores the full current rule set as `snapshot_before` in the CR
- Calls `POST /api/santa/rules/` (Zentral) or equivalent Moroz admin endpoint
- Returns `{"pushed": N, "machine_group": "...", "mode": "merge|replace"}`
- Rollback: pushes `snapshot_before` back with `mode: replace`

### `santa_machine_list`

Lists machines enrolled in the sync server and their last sync state.

```
params: machine_group (optional)
```

- Returns `{"machines": [{"machine_id": "...", "hostname": "...", "os_version": "...", "santa_version": "...", "last_sync": "...", "rule_count": N}]}`
- Read-only, no rollback

### `santa_machine_group_assign`

Assigns a machine to a rule group on the sync server.

```
params: machine_id, target_group
```

- Captures current group assignment as `previous_group`
- Updates machine's configuration assignment on the sync server
- Returns `{"machine_id": "...", "previous_group": "...", "new_group": "..."}`
- Rollback: re-assign to `previous_group`

### `santa_rule_deploy`

Adds a single rule to a group's policy and verifies receipt on enrolled machines.

```
params:
  rule_type: allowlist | denylist | silent_blocklist
  identifier_type: binary | certificate | teamid | signingid
  identifier: SHA-256 or cert/team/signing ID
  machine_group: optional
  custom_message: optional
  verify_machines: optional list of machine IDs to verify receipt (default: all)
  verify_timeout_seconds: default 300
```

- Pushes the single rule via `santa_push_rules` (merge mode)
- Polls `santa_machine_list` until all `verify_machines` show a last_sync timestamp newer than the push time, or until timeout
- Returns `{"deployed": true, "verified_machines": N, "unverified_machines": [...]}`
- Rollback: remove the rule via `santa_push_rules` (merge of the inverse) — or if it was previously in the rule set with a different type, restore that type

---

## Section 4: Findings Integration

`santa_policy_audit` results are ingested as findings of type `santa_policy_drift` when:
- A machine group has no explicit deny rule for a known-bad SHA-256 (cross-referenced against any existing vuln findings with file hashes)
- A machine's rule count differs significantly from its group's expected count (indicates the machine hasn't synced recently)

`santa_event_export` (from Spec A, per-device agent command) surfaces findings of type `santa_blocked_binary`:
- Each unique SHA-256 that was blocked on a Mac asset creates a finding
- Finding auto-suggests a `santa_rule_deploy` CR to add an explicit denylist rule fleet-wide
- Finding also links to `santa_binary_check` as a verification step

---

## Section 5: Smoke Phase

**Phase name:** `SANTA_SYNC`

**Infrastructure:** Moroz (open-source Santa sync server in Go) running in Docker on the EC2 backend container. No dedicated host needed — Moroz is a ~15MB binary.

**Credential gate:** reads from SSM at `/nexplane/smoke/santa/sync_server_url` and `/nexplane/smoke/santa/auth_token`. If absent, the phase provisions a local Moroz instance on the runner for the duration of the test.

**Smoke sequence:**
1. Start Moroz in Docker on the runner (or use SSM credentials if provided)
2. Register `santa_sync_server` connector pointing at Moroz
3. `santa_policy_audit` — verify empty rule set
4. `santa_push_rules` — push 2 test rules (1 allowlist, 1 denylist)
5. `santa_policy_audit` — verify 2 rules present
6. `santa_push_rules` rollback — verify rules removed
7. `santa_policy_audit` — verify empty rule set restored
8. `santa_machine_list` — graceful (no machines enrolled in smoke instance)
9. Teardown Moroz container

---

## Section 6: North Pole Commercial Path

When a design partner relationship with North Pole is established:

1. Add `north_pole` as an `auth_flavor` credential field on `santa_sync_server` (default: `moroz_zentral`)
2. The executor selects the API client implementation based on `auth_flavor`
3. North Pole-specific auth (OAuth, API key format, endpoint paths) is isolated in a `north_pole_client.py` alongside the existing `moroz_client.py` and `zentral_client.py`
4. All actions remain the same — only the HTTP client layer changes

This means zero action schema changes and no smoke phase changes when North Pole support is added.

---

## Out of scope

- Full Santa sync server implementation (Nexplane is a client of an existing sync server, not a replacement)
- Machine enrollment / initial Santa deployment (handled by MDM or manual agent install)
- Certificate rule management via Apple's certificate authority APIs
- Zentral's broader security platform features (only Santa rule management is in scope)
