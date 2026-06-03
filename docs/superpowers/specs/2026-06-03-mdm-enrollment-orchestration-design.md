# MDM Enrollment and Orchestration Design

**Date:** 2026-06-03
**Scope:** `jamf` and `micromdm` connector types, MDM enrollment CR, full macOS device management surface, smoke test infrastructure using MicroMDM on EC2.

---

## 1. Context and Motivation

Nexplane's macOS agent (bootstrapped via `MAC_AGENT_BOOTSTRAP`) provides direct device control at execution tier 3. MDM sits above it at tier 1 — the safest, most managed path for patch delivery, profile management, and compliance enforcement. Without MDM support, the platform falls back to agent or SSH for every macOS CR, bypassing the controls that enterprise Mac fleets are built around.

The existing execution tier model already handles routing: if a `jamf` or `micromdm` connector is configured and the device is enrolled, the planning engine picks tier 1 automatically. No special routing logic required.

**APNs blocker:** Apple Push Notification service (APNs) is required for push-triggered MDM command delivery. An APNs vendor certificate is not yet available. All CRs function correctly without it by using explicit device check-in polling (`mdmclient checkin` via agent) instead of push-triggered wake-up. `push_cert_configured: false` surfaces a warning in the UI but does not block execution. A separate `MDM_APNS` smoke phase is defined but skipped until the cert exists.

---

## 2. Connector Architecture

### 2.1 Two new connector types

**`jamf`** — Jamf Pro REST API. Production target for enterprise Mac fleets.
- Auth: OAuth2 client credentials (`client_id`, `client_secret`, `base_url`)
- API surface: Jamf Pro API v1 + Classic API for legacy endpoints

**`micromdm`** — MicroMDM open-source Apple MDM server. Used for smoke tests and orgs self-hosting open-source MDM.
- Auth: single API key (`base_url`, `api_key`)
- API surface: MicroMDM HTTP API + Apple MDM protocol commands

Both connectors are `execution_tier: 1`. The planning engine picks `jamf` first if both are configured on an asset (lower connector ID wins among same-tier options — existing behavior).

### 2.2 Shared generic_action names

Both connectors implement identical `generic_action` identifiers. Jamf-only actions are non-generic and only appear when a `jamf` connector is active.

### 2.3 Credential fields

```json
// jamf
{"base_url": "string", "client_id": "string", "client_secret": "password"}

// micromdm
{"base_url": "string", "api_key": "password"}
```

---

## 3. Action Surface

### 3.1 Shared actions (jamf + micromdm)

| generic_action | Description | Rollback | blast_radius |
|---|---|---|---|
| `enroll_device` | Push enrollment profile via agent → device joins MDM | `unenroll_device` | `single_device` |
| `unenroll_device` | MDM RemoveManagement command | re-enroll (manual, noted in result) | `single_device` |
| `sync_device` | Trigger immediate MDM check-in | none | `none` |
| `push_configuration_profile` | Install .mobileconfig via MDM | `remove_configuration_profile` | `single_device` |
| `remove_configuration_profile` | Remove profile by identifier | restore from snapshot | `single_device` |
| `schedule_os_update` | MDM ScheduleOSUpdate (security patches + minor updates) | none (cannot uninstall) | `single_device` |
| `os_major_upgrade` | MDM-pushed major version upgrade via `startosinstall` | reconstitution rollback only | `irreversible` |
| `run_script` | Execute script on device via MDM | `delete_script` | `single_device` |
| `delete_script` | Remove deployed script record | none | `none` |
| `install_package` | Push .pkg via MDM InstallApplication | `remove_package` | `single_device` |
| `remove_package` | Uninstall package by bundle ID | none | `single_device` |
| `check_compliance` | Query compliance state and failing policies | none | `none` |
| `query_device` | Full inventory — hardware, software, profiles, certificates | none | `none` |
| `lock_device` | MDM DeviceLock with PIN | `unlock_device` (PIN required) | `single_device` |
| `remote_wipe` | MDM EraseDevice — full wipe | reconstitution rollback only | `irreversible` |

`remote_wipe` and `os_major_upgrade` require `confirm: true` in parameters and are blocked by the safety engine without explicit approval — same pattern as IAM key revocation.

### 3.2 Jamf-only actions (no generic_action, no micromdm equivalent)

| action_id | Description | Rollback |
|---|---|---|
| `manage_smart_group` | Add/remove device from Jamf smart group | restore prior membership |
| `assign_patch_policy` | Assign device to Jamf patch management title with deadline | remove assignment |
| `manage_extension_attribute` | Set custom inventory attribute value | restore prior value |
| `deploy_vpp_app` | Push App Store app via VPP token | `remove_package` |

---

## 4. Enrollment Flow

Enrollment is a two-actor operation requiring both the MDM connector and the Nexplane agent.

### 4.1 Execute

1. MDM connector calls server API to generate a one-time enrollment URL
   - Jamf: `POST /api/v1/enrollment-customization/prestages/{id}/scope` + enrollment profile URL
   - MicroMDM: `GET /api/v1/enrollment` returns enrollment profile URL
2. Agent receives `profiles_install` with the enrollment profile fetched from that URL
3. Device contacts MDM server, completes MDM enrollment handshake
4. Executor polls MDM server device inventory until the device UDID appears (120s timeout, 5s interval)
5. If APNs unavailable: agent triggers `mdmclient checkin` to force device to complete enrollment check-in
6. Returns `{enrolled: true, device_id: "<mdm_device_id>", udid: "<udid>", push_available: false}`

### 4.2 Rollback

1. Executor sends MDM `RemoveManagement` command to device UDID
2. Agent triggers `mdmclient checkin` to ensure command is processed
3. Executor polls until device no longer appears in MDM inventory (60s timeout)
4. If device is offline/unreachable: records UDID in rollback result as `pending_manual_cleanup` — same reconstitution pattern as other permanent ops

### 4.3 Command delivery without APNs

For all CRs (not just enrollment): after sending a command to the MDM server, the executor instructs the agent to run `mdmclient checkin`, which forces the device to poll for pending MDM commands immediately. This makes all CRs function correctly in pull mode. The `push_cert_configured` field on the connector status endpoint signals APNs availability to the UI.

---

## 5. Executor Structure

```
backend/app/connectors/executors/jamf/
    __init__.py
    _client.py                  # OAuth2 token mgmt, Jamf Pro API wrapper
    enroll_device.py
    unenroll_device.py
    sync_device.py
    push_configuration_profile.py
    remove_configuration_profile.py
    schedule_os_update.py
    os_major_upgrade.py
    run_script.py
    delete_script.py
    install_package.py
    remove_package.py
    check_compliance.py
    query_device.py
    lock_device.py
    remote_wipe.py
    manage_smart_group.py
    assign_patch_policy.py
    manage_extension_attribute.py
    deploy_vpp_app.py

backend/app/connectors/executors/micromdm/
    __init__.py
    _client.py                  # MicroMDM API key auth, command queue wrapper
    enroll_device.py
    unenroll_device.py
    sync_device.py
    push_configuration_profile.py
    remove_configuration_profile.py
    schedule_os_update.py
    os_major_upgrade.py
    run_script.py
    delete_script.py
    install_package.py
    remove_package.py
    check_compliance.py
    query_device.py
    lock_device.py
    remote_wipe.py

backend/app/connectors/catalog/
    jamf.json
    micromdm.json
```

The `enroll_device` executors are unique in that they call the agent API mid-execution. They look up the `nexplane_agent` connector record for the target asset (same DB query used by multi-step CRs) and issue `profiles_install` directly against the agent's `/execute` endpoint. If no agent connector exists for the asset, `enroll_device` fails fast with `agent_required: true` in the error detail.

---

## 6. Smoke Test Infrastructure

### 6.1 MicroMDM EC2 instance

- Instance type: `t3.small`, same VPC as platform
- AMI cache key: `/nexplane/smoke-amis/micromdm/{config_hash}` in SSM
- AMI contents: MicroMDM binary, TLS self-signed cert, server config, empty APNs slot
- Management plane: Tailscale installed, accessible at Tailscale IP
- Device→MDM check-in: VPC private IP (same subnet as mac2.metal instance)
- TLS trust: enrollment profile includes the self-signed CA cert so device trusts it without system-wide cert install

### 6.2 Smoke phases

**`MDM_ENROLL` phase** (prerequisite: `MAC_AGENT_BOOTSTRAP` passed, device running)
1. Get-or-create MicroMDM AMI, launch instance, wait for healthy
2. Create `micromdm` connector via platform API
3. Execute `enroll_device` CR — assert device UDID appears in MicroMDM inventory
4. Execute `unenroll_device` rollback — assert device removed from inventory
5. Re-execute `enroll_device` — assert device re-enrolled (rollback+re-enroll cycle)

**`MDM_MANAGE` phase** (prerequisite: `MDM_ENROLL` passed, device enrolled)
1. `push_configuration_profile` → assert profile in `profiles list` on device → rollback → assert removed
2. `run_script` (echo test) → assert output captured in MicroMDM command result → `delete_script`
3. `schedule_os_update` → assert command queued in MicroMDM, device check-in confirms receipt (no actual install)
4. `check_compliance` → assert returns device record with UDID
5. `query_device` → assert returns hardware info and installed profiles
6. `lock_device` → assert SSH session to device drops (device locked) → `unlock_device` with PIN → assert SSH recovers
7. `install_package` + `remove_package` — install a test .pkg, assert bundle present, remove, assert gone
8. `remote_wipe` — executor short-circuits before sending the EraseDevice command when `_smoke_test: true` is injected by the test harness; asserts the executor reaches the pre-send checkpoint and returns `{queued: false, dry_run: true}`; rollback is no-op

**`MDM_APNS` phase** (skipped until APNs cert available — blocker)
- Load APNs cert into MicroMDM, verify push token exchange with Apple
- Re-run `MDM_MANAGE` asserting push-triggered check-in instead of explicit poll
- Remove `mdmclient checkin` workaround from phase assertions

### 6.3 Do not terminate mac instance after MDM smoke

The mac2.metal instance stays running after `MDM_MANAGE` passes — same constraint as after `MAC_AGENT_BOOTSTRAP`. Host release discussion deferred to user.

---

## 7. Tier Assignments

| Connector | Actions | Execution Tier |
|---|---|---|
| `jamf` | All actions | 1 — `direct_api` |
| `micromdm` | All actions | 1 — `direct_api` |
| `nexplane_agent` | `apply_mac_patches`, `push_configuration_profile` | 3 — `agent` |
| `ssh` | `softwareupdate`, raw profile push | 5 — `raw_remote_command` |

For a Mac asset with Jamf configured and device enrolled, the planning engine selects tier 1 (Jamf) for patch and profile CRs automatically. No code changes to the planning engine required.

---

## 8. New ConnectorType Enum Values

```python
# in backend/app/models/connector.py ConnectorType enum
jamf = "jamf"
micromdm = "micromdm"
```

---

## 9. Out of Scope

- Windows MDM (Intune already covers this)
- iOS/iPadOS device management (separate enrollment profile type, deferred)
- Apple Business Manager / DEP integration (requires ABM account, deferred)
- APNs certificate acquisition workflow (blocked — see Section 4.3)
- Jamf Now API (limited API surface, not targeting)
