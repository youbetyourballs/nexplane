# MDM Sub-Plan 1: Infrastructure + Core Executors

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `jamf` and `micromdm` connector types, catalog JSON, HTTP clients, `macos_mdm_checkin` agent command, and the five core executors (enroll, unenroll, sync, check_compliance, query_device) for both connectors.

**Architecture:** Two new connector directories under `backend/app/connectors/executors/` following the existing Intune pattern — each with a `_client.py` HTTP helper and one file per executor. `enroll_device` is two-actor: it calls `dispatch_agent_job` to push the enrollment profile via the Nexplane agent, then polls the MDM server until the device appears. A new `macos_mdm_checkin` Go agent command wraps `mdmclient CheckIn` to force MDM poll without APNs push.

**Tech Stack:** Python/httpx for executors, Go for agent command, pytest-asyncio for tests. Jamf Pro API (OAuth2 + Classic API for device lookup). MicroMDM HTTP API (Basic auth).

**Sub-plan sequence:**
- Sub-Plan 1 (this): Infrastructure + core executors
- Sub-Plan 2: Full management surface (push_configuration_profile, run_script, lock_device, remote_wipe, etc.)
- Sub-Plan 3: Smoke test phases (MDM_ENROLL, MDM_MANAGE)

---

## File Map

**Create:**
- `backend/app/connectors/executors/jamf/__init__.py`
- `backend/app/connectors/executors/jamf/_client.py`
- `backend/app/connectors/executors/jamf/enroll_device.py`
- `backend/app/connectors/executors/jamf/unenroll_device.py`
- `backend/app/connectors/executors/jamf/sync_device.py`
- `backend/app/connectors/executors/jamf/check_compliance.py`
- `backend/app/connectors/executors/jamf/query_device.py`
- `backend/app/connectors/executors/micromdm/__init__.py`
- `backend/app/connectors/executors/micromdm/_client.py`
- `backend/app/connectors/executors/micromdm/enroll_device.py`
- `backend/app/connectors/executors/micromdm/unenroll_device.py`
- `backend/app/connectors/executors/micromdm/sync_device.py`
- `backend/app/connectors/executors/micromdm/check_compliance.py`
- `backend/app/connectors/executors/micromdm/query_device.py`
- `backend/app/connectors/catalog/jamf.json`
- `backend/app/connectors/catalog/micromdm.json`
- `agent/commands/macos/mdm_checkin_darwin.go`
- `agent/commands/macos/mdm_checkin_other.go`
- `backend/tests/test_mdm_connectors.py`

**Modify:**
- `backend/app/models/connector.py` — add `jamf` and `micromdm` to `ConnectorType`
- `agent/commands/macos/macos.go` — add `MacOSMdmCheckinExecute`
- `agent/commands/macos/macos_other.go` — add `macOSMdmCheckin` stub
- `agent/executor/executor.go` — register `macos_mdm_checkin`

---

## Task 1: ConnectorType enum

**Files:**
- Modify: `backend/app/models/connector.py`

- [ ] **Step 1: Add jamf and micromdm to ConnectorType**

In `backend/app/models/connector.py`, find the `sccm = "sccm"` line and add after `intune = "intune"`:

```python
    jamf = "jamf"
    micromdm = "micromdm"
```

The block should look like:
```python
    # Windows/endpoint management
    winrm = "winrm"
    sccm = "sccm"
    intune = "intune"
    jamf = "jamf"
    micromdm = "micromdm"
```

- [ ] **Step 2: Verify no import errors**

```bash
cd /home/ec2-user/nexplane && docker exec nexplane-backend-1 python3 -c "from app.models.connector import ConnectorType; print(ConnectorType.jamf, ConnectorType.micromdm)"
```

Expected output:
```
ConnectorType.jamf ConnectorType.micromdm
```

- [ ] **Step 3: Create executor package __init__.py files**

```bash
mkdir -p backend/app/connectors/executors/jamf
mkdir -p backend/app/connectors/executors/micromdm
touch backend/app/connectors/executors/jamf/__init__.py
touch backend/app/connectors/executors/micromdm/__init__.py
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/models/connector.py \
        backend/app/connectors/executors/jamf/__init__.py \
        backend/app/connectors/executors/micromdm/__init__.py
git commit -m "feat(mdm): add jamf and micromdm ConnectorType enum values"
```

---

## Task 2: Jamf catalog JSON

**Files:**
- Create: `backend/app/connectors/catalog/jamf.json`

- [ ] **Step 1: Write the catalog**

Create `backend/app/connectors/catalog/jamf.json`:

```json
{
  "connector_type": "jamf",
  "display_name": "Jamf Pro",
  "credential_fields": [
    {"name": "base_url", "label": "Jamf Pro URL (e.g. https://acme.jamfcloud.com)", "type": "string", "required": true},
    {"name": "client_id", "label": "Client ID (API Role OAuth)", "type": "string", "required": true},
    {"name": "client_secret", "label": "Client Secret", "type": "password", "required": true}
  ],
  "actions": [
    {
      "action_id": "enroll_device",
      "generic_action": "enroll_device",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Enroll Device in MDM",
      "description": "Pushes the Jamf enrollment profile to the device via the Nexplane agent and waits for the device to appear in Jamf inventory.",
      "applicable_asset_types": ["server"],
      "parameters": [],
      "executor": "jamf.enroll_device",
      "rollback_action": "unenroll_device",
      "estimated_duration_seconds": 120,
      "blast_radius_hint": "single_device",
      "safety_notes": ["Requires Nexplane agent to be installed on the target device."]
    },
    {
      "action_id": "unenroll_device",
      "generic_action": "unenroll_device",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Unenroll Device from MDM",
      "description": "Removes the MDM enrollment profile from the device and deletes the device record from Jamf.",
      "applicable_asset_types": ["server"],
      "parameters": [{"name": "device_id", "type": "string", "required": true, "description": "Jamf computer ID from enroll_device result"}],
      "executor": "jamf.unenroll_device",
      "rollback_action": "enroll_device",
      "estimated_duration_seconds": 60,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "sync_device",
      "generic_action": "sync_device",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Sync Device",
      "description": "Triggers an immediate MDM check-in on the device to pull latest policy.",
      "applicable_asset_types": ["server"],
      "parameters": [{"name": "device_id", "type": "string", "required": true}],
      "executor": "jamf.sync_device",
      "rollback_action": null,
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "none",
      "safety_notes": ["Read-only trigger — no state changed."]
    },
    {
      "action_id": "check_compliance",
      "generic_action": "check_compliance",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Check Device Compliance",
      "description": "Returns current compliance state and failing policies for a Jamf-managed device.",
      "applicable_asset_types": ["server"],
      "parameters": [{"name": "device_id", "type": "string", "required": true}],
      "executor": "jamf.check_compliance",
      "rollback_action": null,
      "estimated_duration_seconds": 15,
      "blast_radius_hint": "none"
    },
    {
      "action_id": "query_device",
      "generic_action": "query_device",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Query Device Inventory",
      "description": "Returns full hardware, software, profile, and certificate inventory from Jamf.",
      "applicable_asset_types": ["server"],
      "parameters": [{"name": "device_id", "type": "string", "required": true}],
      "executor": "jamf.query_device",
      "rollback_action": null,
      "estimated_duration_seconds": 15,
      "blast_radius_hint": "none"
    },
    {
      "action_id": "push_configuration_profile",
      "generic_action": "push_configuration_profile",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Push Configuration Profile",
      "description": "Deploys a .mobileconfig profile to the device via Jamf MDM.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "device_id", "type": "string", "required": true},
        {"name": "profile_b64", "type": "string", "required": true, "description": "Base64-encoded .mobileconfig content"},
        {"name": "profile_name", "type": "string", "required": true}
      ],
      "executor": "jamf.push_configuration_profile",
      "rollback_action": "remove_configuration_profile",
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "remove_configuration_profile",
      "generic_action": "remove_configuration_profile",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Remove Configuration Profile",
      "description": "Removes a configuration profile by identifier from a Jamf-managed device.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "device_id", "type": "string", "required": true},
        {"name": "profile_identifier", "type": "string", "required": true}
      ],
      "executor": "jamf.remove_configuration_profile",
      "rollback_action": null,
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "schedule_os_update",
      "generic_action": "schedule_os_update",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Schedule OS Update",
      "description": "Sends MDM ScheduleOSUpdate command for security patches and minor updates.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "device_id", "type": "string", "required": true},
        {"name": "install_action", "type": "string", "required": false, "description": "Default: InstallASAP. Options: Default, DownloadOnly, InstallASAP, NotifyOnly, InstallLater"}
      ],
      "executor": "jamf.schedule_os_update",
      "rollback_action": null,
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "single_device",
      "safety_notes": ["Cannot be undone. Device will restart after update."]
    },
    {
      "action_id": "os_major_upgrade",
      "generic_action": "os_major_upgrade",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Major OS Upgrade",
      "description": "Pushes a major macOS version upgrade via Jamf policy. Irreversible — device restarts into new OS.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "device_id", "type": "string", "required": true},
        {"name": "target_version", "type": "string", "required": true, "description": "e.g. 15.0"},
        {"name": "confirm", "type": "boolean", "required": true, "description": "Must be true to proceed"}
      ],
      "executor": "jamf.os_major_upgrade",
      "rollback_action": null,
      "estimated_duration_seconds": 1800,
      "blast_radius_hint": "irreversible",
      "safety_notes": ["Irreversible. Requires confirm: true. Reconstitution rollback only."]
    },
    {
      "action_id": "run_script",
      "generic_action": "run_script",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Run Script",
      "description": "Deploys and runs a shell script on the device via Jamf.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "device_id", "type": "string", "required": true},
        {"name": "script_content", "type": "string", "required": true},
        {"name": "script_name", "type": "string", "required": true}
      ],
      "executor": "jamf.run_script",
      "rollback_action": "delete_script",
      "estimated_duration_seconds": 120,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "delete_script",
      "generic_action": "delete_script",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Delete Script",
      "description": "Removes a previously deployed script from Jamf.",
      "applicable_asset_types": ["server"],
      "parameters": [{"name": "script_id", "type": "string", "required": true}],
      "executor": "jamf.delete_script",
      "rollback_action": null,
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "none"
    },
    {
      "action_id": "install_package",
      "generic_action": "install_package",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Install Package",
      "description": "Pushes a .pkg to the device via Jamf MDM InstallApplication.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "device_id", "type": "string", "required": true},
        {"name": "manifest_url", "type": "string", "required": true, "description": "URL to a .plist app manifest or direct .pkg URL"}
      ],
      "executor": "jamf.install_package",
      "rollback_action": "remove_package",
      "estimated_duration_seconds": 300,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "remove_package",
      "generic_action": "remove_package",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Remove Package",
      "description": "Uninstalls a package by bundle ID from a Jamf-managed device.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "device_id", "type": "string", "required": true},
        {"name": "bundle_id", "type": "string", "required": true}
      ],
      "executor": "jamf.remove_package",
      "rollback_action": null,
      "estimated_duration_seconds": 60,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "lock_device",
      "generic_action": "lock_device",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Lock Device",
      "description": "Sends MDM DeviceLock with a 6-digit PIN. Device is locked immediately on next check-in.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "device_id", "type": "string", "required": true},
        {"name": "pin", "type": "string", "required": true, "description": "6-digit unlock PIN"}
      ],
      "executor": "jamf.lock_device",
      "rollback_action": "unlock_device",
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "unlock_device",
      "generic_action": "unlock_device",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Unlock Device",
      "description": "Clears the MDM device lock. Device must already be unlocked with the PIN before MDM clear takes effect.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "device_id", "type": "string", "required": true}
      ],
      "executor": "jamf.unlock_device",
      "rollback_action": null,
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "remote_wipe",
      "generic_action": "remote_wipe",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Remote Wipe",
      "description": "Sends MDM EraseDevice command. All data is destroyed. Reconstitution rollback only.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "device_id", "type": "string", "required": true},
        {"name": "confirm", "type": "boolean", "required": true, "description": "Must be true to proceed"}
      ],
      "executor": "jamf.remote_wipe",
      "rollback_action": null,
      "estimated_duration_seconds": 60,
      "blast_radius_hint": "irreversible",
      "safety_notes": ["Irreversible. All data destroyed. Requires confirm: true."]
    },
    {
      "action_id": "manage_smart_group",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Manage Smart Group Membership",
      "description": "Adds or removes the device from a Jamf static computer group.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "device_id", "type": "string", "required": true},
        {"name": "group_id", "type": "string", "required": true},
        {"name": "action", "type": "string", "required": true, "description": "add or remove"}
      ],
      "executor": "jamf.manage_smart_group",
      "rollback_action": "manage_smart_group",
      "estimated_duration_seconds": 15,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "assign_patch_policy",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Assign Patch Policy",
      "description": "Assigns the device to a Jamf patch management software title with a deadline.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "device_id", "type": "string", "required": true},
        {"name": "software_title_id", "type": "string", "required": true},
        {"name": "patch_version", "type": "string", "required": true},
        {"name": "deadline_days", "type": "integer", "required": false, "description": "Days until forced install. Default: 7"}
      ],
      "executor": "jamf.assign_patch_policy",
      "rollback_action": null,
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "manage_extension_attribute",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Set Extension Attribute",
      "description": "Sets a custom Jamf extension attribute value on the device record.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "device_id", "type": "string", "required": true},
        {"name": "attribute_id", "type": "string", "required": true},
        {"name": "value", "type": "string", "required": true}
      ],
      "executor": "jamf.manage_extension_attribute",
      "rollback_action": null,
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "none"
    },
    {
      "action_id": "deploy_vpp_app",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Deploy VPP App",
      "description": "Pushes an App Store app to the device via Jamf VPP token assignment.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "device_id", "type": "string", "required": true},
        {"name": "adam_id", "type": "string", "required": true, "description": "App Store Adam ID"}
      ],
      "executor": "jamf.deploy_vpp_app",
      "rollback_action": "remove_package",
      "estimated_duration_seconds": 120,
      "blast_radius_hint": "single_device"
    }
  ]
}
```

- [ ] **Step 2: Verify catalog loads**

```bash
docker exec nexplane-backend-1 python3 -c "
import json
with open('/app/connectors/catalog/jamf.json') as f:
    d = json.load(f)
print(len(d['actions']), 'actions loaded')
"
```

Expected: `20 actions loaded`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/catalog/jamf.json
git commit -m "feat(mdm): add jamf connector catalog with full management surface"
```

---

## Task 3: MicroMDM catalog JSON

**Files:**
- Create: `backend/app/connectors/catalog/micromdm.json`

- [ ] **Step 1: Write the catalog**

Create `backend/app/connectors/catalog/micromdm.json`:

```json
{
  "connector_type": "micromdm",
  "display_name": "MicroMDM",
  "credential_fields": [
    {"name": "base_url", "label": "MicroMDM Server URL (e.g. https://mdm.internal:8080)", "type": "string", "required": true},
    {"name": "api_key", "label": "API Key", "type": "password", "required": true}
  ],
  "actions": [
    {
      "action_id": "enroll_device",
      "generic_action": "enroll_device",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Enroll Device in MDM",
      "description": "Fetches the MicroMDM enrollment profile and pushes it to the device via the Nexplane agent.",
      "applicable_asset_types": ["server"],
      "parameters": [],
      "executor": "micromdm.enroll_device",
      "rollback_action": "unenroll_device",
      "estimated_duration_seconds": 120,
      "blast_radius_hint": "single_device",
      "safety_notes": ["Requires Nexplane agent on target device."]
    },
    {
      "action_id": "unenroll_device",
      "generic_action": "unenroll_device",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Unenroll Device from MDM",
      "description": "Removes the MDM enrollment profile via agent and deletes the device record from MicroMDM.",
      "applicable_asset_types": ["server"],
      "parameters": [{"name": "udid", "type": "string", "required": true, "description": "Device UDID from enroll_device result"}],
      "executor": "micromdm.unenroll_device",
      "rollback_action": "enroll_device",
      "estimated_duration_seconds": 60,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "sync_device",
      "generic_action": "sync_device",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Sync Device",
      "description": "Triggers immediate MDM check-in via agent mdmclient call.",
      "applicable_asset_types": ["server"],
      "parameters": [{"name": "udid", "type": "string", "required": true}],
      "executor": "micromdm.sync_device",
      "rollback_action": null,
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "none"
    },
    {
      "action_id": "check_compliance",
      "generic_action": "check_compliance",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Check Device Compliance",
      "description": "Returns device record and enrollment state from MicroMDM.",
      "applicable_asset_types": ["server"],
      "parameters": [{"name": "udid", "type": "string", "required": true}],
      "executor": "micromdm.check_compliance",
      "rollback_action": null,
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "none"
    },
    {
      "action_id": "query_device",
      "generic_action": "query_device",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Query Device Inventory",
      "description": "Returns device record and last check-in details from MicroMDM.",
      "applicable_asset_types": ["server"],
      "parameters": [{"name": "udid", "type": "string", "required": true}],
      "executor": "micromdm.query_device",
      "rollback_action": null,
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "none"
    },
    {
      "action_id": "push_configuration_profile",
      "generic_action": "push_configuration_profile",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Push Configuration Profile",
      "description": "Sends MDM InstallProfile command to the device.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "udid", "type": "string", "required": true},
        {"name": "profile_b64", "type": "string", "required": true, "description": "Base64-encoded .mobileconfig"},
        {"name": "profile_identifier", "type": "string", "required": true}
      ],
      "executor": "micromdm.push_configuration_profile",
      "rollback_action": "remove_configuration_profile",
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "remove_configuration_profile",
      "generic_action": "remove_configuration_profile",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Remove Configuration Profile",
      "description": "Sends MDM RemoveProfile command.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "udid", "type": "string", "required": true},
        {"name": "profile_identifier", "type": "string", "required": true}
      ],
      "executor": "micromdm.remove_configuration_profile",
      "rollback_action": null,
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "schedule_os_update",
      "generic_action": "schedule_os_update",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Schedule OS Update",
      "description": "Sends MDM ScheduleOSUpdate command.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "udid", "type": "string", "required": true},
        {"name": "install_action", "type": "string", "required": false, "description": "Default: InstallASAP"}
      ],
      "executor": "micromdm.schedule_os_update",
      "rollback_action": null,
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "os_major_upgrade",
      "generic_action": "os_major_upgrade",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Major OS Upgrade",
      "description": "Sends ScheduleOSUpdate with major version. Irreversible.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "udid", "type": "string", "required": true},
        {"name": "target_version", "type": "string", "required": true},
        {"name": "confirm", "type": "boolean", "required": true}
      ],
      "executor": "micromdm.os_major_upgrade",
      "rollback_action": null,
      "estimated_duration_seconds": 1800,
      "blast_radius_hint": "irreversible",
      "safety_notes": ["Irreversible. Requires confirm: true."]
    },
    {
      "action_id": "run_script",
      "generic_action": "run_script",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Run Script",
      "description": "Sends MDM InstallApplication with a script payload.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "udid", "type": "string", "required": true},
        {"name": "script_content", "type": "string", "required": true},
        {"name": "script_name", "type": "string", "required": true}
      ],
      "executor": "micromdm.run_script",
      "rollback_action": "delete_script",
      "estimated_duration_seconds": 120,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "delete_script",
      "generic_action": "delete_script",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Delete Script",
      "description": "No-op on MicroMDM (scripts are not persisted). Returns success.",
      "applicable_asset_types": ["server"],
      "parameters": [{"name": "script_name", "type": "string", "required": false}],
      "executor": "micromdm.delete_script",
      "rollback_action": null,
      "estimated_duration_seconds": 5,
      "blast_radius_hint": "none"
    },
    {
      "action_id": "install_package",
      "generic_action": "install_package",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Install Package",
      "description": "Sends MDM InstallApplication command with manifest URL.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "udid", "type": "string", "required": true},
        {"name": "manifest_url", "type": "string", "required": true}
      ],
      "executor": "micromdm.install_package",
      "rollback_action": "remove_package",
      "estimated_duration_seconds": 300,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "remove_package",
      "generic_action": "remove_package",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Remove Package",
      "description": "Sends MDM RemoveApplication command by bundle ID.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "udid", "type": "string", "required": true},
        {"name": "bundle_id", "type": "string", "required": true}
      ],
      "executor": "micromdm.remove_package",
      "rollback_action": null,
      "estimated_duration_seconds": 60,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "lock_device",
      "generic_action": "lock_device",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Lock Device",
      "description": "Sends MDM DeviceLock with 6-digit PIN.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "udid", "type": "string", "required": true},
        {"name": "pin", "type": "string", "required": true}
      ],
      "executor": "micromdm.lock_device",
      "rollback_action": "unlock_device",
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "unlock_device",
      "generic_action": "unlock_device",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Unlock Device",
      "description": "Clears the MDM device lock activation.",
      "applicable_asset_types": ["server"],
      "parameters": [{"name": "udid", "type": "string", "required": true}],
      "executor": "micromdm.unlock_device",
      "rollback_action": null,
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "single_device"
    },
    {
      "action_id": "remote_wipe",
      "generic_action": "remote_wipe",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Remote Wipe",
      "description": "Sends MDM EraseDevice. All data destroyed. Reconstitution rollback only.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "udid", "type": "string", "required": true},
        {"name": "confirm", "type": "boolean", "required": true}
      ],
      "executor": "micromdm.remote_wipe",
      "rollback_action": null,
      "estimated_duration_seconds": 60,
      "blast_radius_hint": "irreversible",
      "safety_notes": ["Irreversible. Requires confirm: true."]
    }
  ]
}
```

- [ ] **Step 2: Verify catalog loads**

```bash
docker exec nexplane-backend-1 python3 -c "
import json
with open('/app/connectors/catalog/micromdm.json') as f:
    d = json.load(f)
print(len(d['actions']), 'actions loaded')
"
```

Expected: `16 actions loaded`

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/catalog/micromdm.json
git commit -m "feat(mdm): add micromdm connector catalog"
```

---

## Task 4: macos_mdm_checkin agent command

**Files:**
- Create: `agent/commands/macos/mdm_checkin_darwin.go`
- Create: `agent/commands/macos/mdm_checkin_other.go`
- Modify: `agent/commands/macos/macos.go`
- Modify: `agent/commands/macos/macos_other.go`
- Modify: `agent/executor/executor.go`

- [ ] **Step 1: Create the darwin implementation**

Create `agent/commands/macos/mdm_checkin_darwin.go`:

```go
//go:build darwin

package macos

import (
	"os/exec"
	"strings"
	"time"
)

func macOSMdmCheckin(_ map[string]any) (map[string]any, error) {
	out, err := exec.Command("mdmclient", "CheckIn").CombinedOutput()
	output := strings.TrimSpace(string(out))
	checkedIn := err == nil
	// mdmclient returns non-zero if not enrolled — treat as non-fatal
	return map[string]any{
		"output":      output,
		"checked_in":  checkedIn,
		"checked_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}
```

- [ ] **Step 2: Create the other stub**

Create `agent/commands/macos/mdm_checkin_other.go`:

```go
//go:build !darwin

package macos

import "fmt"

func macOSMdmCheckin(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("macos_mdm_checkin is only supported on macOS")
}
```

- [ ] **Step 3: Add exported wrapper to macos.go**

In `agent/commands/macos/macos.go`, append:

```go
// MacOSMdmCheckinExecute triggers an immediate MDM check-in via mdmclient.
func MacOSMdmCheckinExecute(params map[string]any) (map[string]any, error) {
	return macOSMdmCheckin(params)
}
```

- [ ] **Step 4: Register in executor.go**

In `agent/executor/executor.go`, find the `"macos_sysinfo"` entry and add after it:

```go
"macos_mdm_checkin": macos.MacOSMdmCheckinExecute,
```

- [ ] **Step 5: Verify darwin build**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build . 2>&1
```

Expected: no output (success)

- [ ] **Step 6: Verify linux build still passes**

```bash
cd agent && GOOS=linux GOARCH=amd64 go build . 2>&1
```

Expected: no output

- [ ] **Step 7: Commit**

```bash
git add agent/commands/macos/mdm_checkin_darwin.go \
        agent/commands/macos/mdm_checkin_other.go \
        agent/commands/macos/macos.go \
        agent/executor/executor.go
git commit -m "feat(agent/macos): add macos_mdm_checkin command for APNs-free MDM delivery"
```

---

## Task 5: Jamf _client.py

**Files:**
- Create: `backend/app/connectors/executors/jamf/_client.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_mdm_connectors.py`:

```python
"""Unit tests for jamf and micromdm connector clients and core executors."""
from __future__ import annotations
import asyncio
import base64
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ---------------------------------------------------------------------------
# Jamf client tests
# ---------------------------------------------------------------------------

def test_jamf_get_token_success():
    from app.connectors.executors.jamf._client import get_token
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"access_token": "tok-123", "expires_in": 1800}
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        token = get_token({"base_url": "https://jamf.example.com", "client_id": "id", "client_secret": "sec"})
    assert token == "tok-123"
    call_args = mock_post.call_args
    assert "client_credentials" in str(call_args)


def test_jamf_get_device_by_serial_found():
    from app.connectors.executors.jamf._client import get_device_by_serial
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "computer": {
            "general": {"id": 42, "udid": "UDID-001", "name": "mac-test"}
        }
    }
    with patch("httpx.get", return_value=mock_resp), \
         patch("app.connectors.executors.jamf._client.get_token", return_value="tok"):
        result = get_device_by_serial(
            {"base_url": "https://jamf.example.com", "client_id": "id", "client_secret": "sec"},
            "ABC123"
        )
    assert result["id"] == "42"
    assert result["udid"] == "UDID-001"


def test_jamf_get_device_by_serial_not_found():
    from app.connectors.executors.jamf._client import get_device_by_serial
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch("httpx.get", return_value=mock_resp), \
         patch("app.connectors.executors.jamf._client.get_token", return_value="tok"):
        result = get_device_by_serial(
            {"base_url": "https://jamf.example.com", "client_id": "id", "client_secret": "sec"},
            "NOTFOUND"
        )
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/ec2-user/nexplane && docker exec nexplane-backend-1 python3 -m pytest tests/test_mdm_connectors.py::test_jamf_get_token_success -v 2>&1 | tail -10
```

Expected: `ModuleNotFoundError` or `ImportError` (client doesn't exist yet)

- [ ] **Step 3: Write the client**

Create `backend/app/connectors/executors/jamf/_client.py`:

```python
import httpx


def get_token(creds: dict) -> str:
    """Exchange client credentials for a Jamf Pro Bearer token."""
    resp = httpx.post(
        f"{creds['base_url'].rstrip('/')}/api/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": creds["client_id"],
            "client_secret": creds["client_secret"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def jamf_get(creds: dict, path: str) -> dict:
    token = get_token(creds)
    resp = httpx.get(
        f"{creds['base_url'].rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def jamf_post(creds: dict, path: str, json: dict = None) -> dict:
    token = get_token(creds)
    resp = httpx.post(
        f"{creds['base_url'].rstrip('/')}{path}",
        json=json or {},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def jamf_put(creds: dict, path: str, json: dict = None) -> dict:
    token = get_token(creds)
    resp = httpx.put(
        f"{creds['base_url'].rstrip('/')}{path}",
        json=json or {},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def jamf_delete(creds: dict, path: str) -> None:
    token = get_token(creds)
    resp = httpx.delete(
        f"{creds['base_url'].rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()


def get_enrollment_profile(creds: dict) -> bytes:
    """Download the User-Initiated Enrollment .mobileconfig from Jamf Pro.

    Requires User-Initiated Enrollment to be enabled in Jamf Pro > Settings > MDM.
    """
    token = get_token(creds)
    resp = httpx.get(
        f"{creds['base_url'].rstrip('/')}/mobileconfig?profileType=User",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


def get_device_by_serial(creds: dict, serial: str) -> dict | None:
    """Look up a Jamf computer record by serial number. Returns None if not found."""
    token = get_token(creds)
    try:
        resp = httpx.get(
            f"{creds['base_url'].rstrip('/')}/JSSResource/computers/serialnumber/{serial}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        general = data.get("computer", {}).get("general", {})
        return {
            "id": str(general.get("id", "")),
            "udid": general.get("udid", ""),
            "serial": serial,
            "name": general.get("name", ""),
        }
    except Exception:
        return None


def send_mdm_command(creds: dict, device_id: str, command_xml: str) -> dict:
    """Send a raw MDM command to a device via Jamf Classic API."""
    token = get_token(creds)
    resp = httpx.post(
        f"{creds['base_url'].rstrip('/')}/JSSResource/computercommands/command/{command_xml}/id/{device_id}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker exec nexplane-backend-1 python3 -m pytest tests/test_mdm_connectors.py::test_jamf_get_token_success tests/test_mdm_connectors.py::test_jamf_get_device_by_serial_found tests/test_mdm_connectors.py::test_jamf_get_device_by_serial_not_found -v 2>&1 | tail -10
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/jamf/_client.py backend/tests/test_mdm_connectors.py
git commit -m "feat(mdm/jamf): add Jamf Pro API client with token, device lookup, enrollment profile"
```

---

## Task 6: MicroMDM _client.py

**Files:**
- Create: `backend/app/connectors/executors/micromdm/_client.py`

- [ ] **Step 1: Add MicroMDM client tests**

Append to `backend/tests/test_mdm_connectors.py`:

```python
# ---------------------------------------------------------------------------
# MicroMDM client tests
# ---------------------------------------------------------------------------

def test_micromdm_send_command():
    from app.connectors.executors.micromdm._client import send_command
    mock_resp = MagicMock()
    mock_resp.content = b'{"request_type": "DeviceLock"}'
    mock_resp.json.return_value = {"request_type": "DeviceLock"}
    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = send_command(
            {"base_url": "https://mdm.example.com", "api_key": "secret"},
            udid="UDID-001",
            request_type="DeviceLock",
            pin="123456",
        )
    assert result["request_type"] == "DeviceLock"
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs["json"]["udid"] == "UDID-001"
    assert call_kwargs["json"]["request_type"] == "DeviceLock"
    assert call_kwargs["json"]["pin"] == "123456"


def test_micromdm_get_device_by_udid_found():
    from app.connectors.executors.micromdm._client import get_device_by_udid
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "devices": [
            {"udid": "UDID-001", "serial_number": "XYZ", "model": "MacPro"},
            {"udid": "UDID-002", "serial_number": "ABC", "model": "MacPro"},
        ]
    }
    with patch("httpx.get", return_value=mock_resp):
        result = get_device_by_udid(
            {"base_url": "https://mdm.example.com", "api_key": "secret"},
            "UDID-001"
        )
    assert result["udid"] == "UDID-001"
    assert result["serial_number"] == "XYZ"


def test_micromdm_get_device_by_udid_not_found():
    from app.connectors.executors.micromdm._client import get_device_by_udid
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"devices": []}
    with patch("httpx.get", return_value=mock_resp):
        result = get_device_by_udid(
            {"base_url": "https://mdm.example.com", "api_key": "secret"},
            "NOTFOUND"
        )
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 python3 -m pytest tests/test_mdm_connectors.py::test_micromdm_send_command -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write the client**

Create `backend/app/connectors/executors/micromdm/_client.py`:

```python
import httpx


def _auth(creds: dict) -> tuple[str, str]:
    """MicroMDM uses HTTP Basic auth: username=micromdm, password=api_key."""
    return ("micromdm", creds["api_key"])


def micromdm_get(creds: dict, path: str) -> dict:
    resp = httpx.get(
        f"{creds['base_url'].rstrip('/')}{path}",
        auth=_auth(creds),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def micromdm_post(creds: dict, path: str, json: dict = None) -> dict:
    resp = httpx.post(
        f"{creds['base_url'].rstrip('/')}{path}",
        json=json or {},
        auth=_auth(creds),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def micromdm_delete(creds: dict, path: str) -> None:
    resp = httpx.delete(
        f"{creds['base_url'].rstrip('/')}{path}",
        auth=_auth(creds),
        timeout=30,
    )
    resp.raise_for_status()


def get_enrollment_profile(creds: dict) -> bytes:
    """Download the MicroMDM enrollment .mobileconfig."""
    resp = httpx.get(
        f"{creds['base_url'].rstrip('/')}/mdm/enroll",
        auth=_auth(creds),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


def send_command(creds: dict, udid: str, request_type: str, **kwargs) -> dict:
    """Send an MDM command to a device. Extra kwargs are merged into the payload."""
    payload = {"udid": udid, "request_type": request_type}
    payload.update(kwargs)
    return micromdm_post(creds, "/v1/commands", json=payload)


def get_devices(creds: dict) -> list[dict]:
    result = micromdm_get(creds, "/v1/devices")
    return result.get("devices") or []


def get_device_by_udid(creds: dict, udid: str) -> dict | None:
    for device in get_devices(creds):
        if device.get("udid") == udid:
            return device
    return None


def get_device_by_serial(creds: dict, serial: str) -> dict | None:
    for device in get_devices(creds):
        if device.get("serial_number") == serial:
            return device
    return None
```

- [ ] **Step 4: Run MicroMDM client tests**

```bash
docker exec nexplane-backend-1 python3 -m pytest tests/test_mdm_connectors.py::test_micromdm_send_command tests/test_mdm_connectors.py::test_micromdm_get_device_by_udid_found tests/test_mdm_connectors.py::test_micromdm_get_device_by_udid_not_found -v 2>&1 | tail -5
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/micromdm/_client.py backend/tests/test_mdm_connectors.py
git commit -m "feat(mdm/micromdm): add MicroMDM API client with command, device lookup, enrollment profile"
```

---

## Task 7: jamf/enroll_device.py

**Files:**
- Create: `backend/app/connectors/executors/jamf/enroll_device.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_mdm_connectors.py`:

```python
# ---------------------------------------------------------------------------
# jamf/enroll_device tests
# ---------------------------------------------------------------------------

class MockConnectorNoCreds:
    credentials = {}

class MockConnectorJamf:
    credentials = {
        "base_url": "https://jamf.example.com",
        "client_id": "test-id",
        "client_secret": "test-secret",
    }


@pytest.mark.asyncio
async def test_jamf_enroll_device_mock_mode():
    """No credentials → returns mock result without hitting any API."""
    from app.connectors.executors.jamf.enroll_device import execute
    result = await execute({}, ["asset-001"], MockConnectorNoCreds())
    assert result["enrolled"] is True
    assert result.get("mock") is True


@pytest.mark.asyncio
async def test_jamf_enroll_device_with_creds():
    """With creds: gets sysinfo, fetches profile, pushes via agent, polls until device appears."""
    from app.connectors.executors.jamf.enroll_device import execute

    sysinfo = {"serial": "SN-001", "hostname": "mac-test", "os_version": "15.0"}
    profile_bytes = b"<plist>fake-enrollment-profile</plist>"
    device_record = {"id": "42", "udid": "UDID-001", "serial": "SN-001", "name": "mac-test"}

    dispatch_calls = []

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
        dispatch_calls.append(command)
        if command == "macos_sysinfo":
            return sysinfo
        return {"status": "ok"}

    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        side_effect=fake_dispatch,
    ), patch(
        "app.connectors.executors.jamf._client.get_enrollment_profile",
        return_value=profile_bytes,
    ), patch(
        "app.connectors.executors.jamf._client.get_device_by_serial",
        return_value=device_record,
    ), patch("asyncio.sleep", new=AsyncMock()):
        result = await execute({}, ["asset-001"], MockConnectorJamf())

    assert result["enrolled"] is True
    assert result["udid"] == "UDID-001"
    assert result["device_id"] == "42"
    assert "macos_sysinfo" in dispatch_calls
    assert "profiles_install" in dispatch_calls
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker exec nexplane-backend-1 python3 -m pytest tests/test_mdm_connectors.py::test_jamf_enroll_device_mock_mode -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write the executor**

Create `backend/app/connectors/executors/jamf/enroll_device.py`:

```python
import asyncio
import base64


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})

    if not creds:
        return {
            "action": "enroll_device",
            "enrolled": True,
            "device_id": "mock-device-001",
            "udid": "mock-udid-001",
            "serial": "mock-serial",
            "push_available": False,
            "mock": True,
        }

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    from ._client import get_enrollment_profile, get_device_by_serial

    # Get device serial number for post-enrollment lookup
    sysinfo = await dispatch_agent_job("macos_sysinfo", {}, list(asset_ids), timeout_seconds=60)
    serial = sysinfo.get("serial", "")
    hostname = sysinfo.get("hostname", "")
    if not serial:
        raise RuntimeError("macos_sysinfo did not return a serial number — is the agent running on macOS?")

    # Fetch Jamf enrollment profile and push via agent
    profile_bytes = get_enrollment_profile(creds)
    profile_b64 = base64.b64encode(profile_bytes).decode("ascii")
    await dispatch_agent_job(
        "profiles_install",
        {"plist_b64": profile_b64},
        list(asset_ids),
        timeout_seconds=120,
    )

    # Trigger explicit check-in (APNs workaround — device polls for MDM handshake)
    await dispatch_agent_job("macos_mdm_checkin", {}, list(asset_ids), timeout_seconds=30)

    # Poll Jamf until device appears in inventory (max 120s)
    device = None
    for _ in range(24):
        device = get_device_by_serial(creds, serial)
        if device:
            break
        await asyncio.sleep(5)

    if not device:
        raise RuntimeError(
            f"Device with serial {serial!r} did not appear in Jamf inventory within 120s. "
            "Verify the enrollment profile is valid and the device can reach the Jamf server."
        )

    return {
        "action": "enroll_device",
        "enrolled": True,
        "device_id": device["id"],
        "udid": device["udid"],
        "serial": serial,
        "hostname": hostname,
        "push_available": False,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    device_id = execution_result.get("device_id", "")
    if not device_id or execution_result.get("mock"):
        return {"rolled_back": True, "reason": "mock or no device_id"}
    from app.connectors.executors.jamf.unenroll_device import execute as unenroll
    return await unenroll({"device_id": device_id}, list(parameters.get("_asset_ids", [])), connector)
```

- [ ] **Step 4: Run tests**

```bash
docker exec nexplane-backend-1 python3 -m pytest tests/test_mdm_connectors.py::test_jamf_enroll_device_mock_mode tests/test_mdm_connectors.py::test_jamf_enroll_device_with_creds -v 2>&1 | tail -8
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/jamf/enroll_device.py backend/tests/test_mdm_connectors.py
git commit -m "feat(mdm/jamf): enroll_device executor with two-actor profile push and enrollment polling"
```

---

## Task 8: jamf/unenroll_device.py

**Files:**
- Create: `backend/app/connectors/executors/jamf/unenroll_device.py`

- [ ] **Step 1: Add test**

Append to `backend/tests/test_mdm_connectors.py`:

```python
@pytest.mark.asyncio
async def test_jamf_unenroll_device_mock_mode():
    from app.connectors.executors.jamf.unenroll_device import execute
    result = await execute({"device_id": "42"}, ["asset-001"], MockConnectorNoCreds())
    assert result["unenrolled"] is True
    assert result.get("mock") is True


@pytest.mark.asyncio
async def test_jamf_unenroll_device_removes_profile_and_deletes_record():
    from app.connectors.executors.jamf.unenroll_device import execute

    dispatch_calls = []
    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
        dispatch_calls.append({"command": command, "params": parameters})
        if command == "profiles_list":
            return {"profiles_plist": "<plist><array><dict><key>ProfileIdentifier</key><string>com.jamf.mdm.enrollment</string></dict></array></plist>"}
        return {"status": "ok"}

    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        side_effect=fake_dispatch,
    ), patch("app.connectors.executors.jamf._client.jamf_delete") as mock_delete:
        result = await execute({"device_id": "42"}, ["asset-001"], MockConnectorJamf())

    assert result["unenrolled"] is True
    assert any(c["command"] == "profiles_list" for c in dispatch_calls)
    mock_delete.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker exec nexplane-backend-1 python3 -m pytest tests/test_mdm_connectors.py::test_jamf_unenroll_device_mock_mode -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write the executor**

Create `backend/app/connectors/executors/jamf/unenroll_device.py`:

```python
import re


def _find_mdm_profile_identifier(profiles_plist: str) -> str | None:
    """Extract the MDM enrollment profile identifier from profiles list XML output."""
    # Look for ProfileIdentifier values containing 'mdm' or 'enrollment'
    matches = re.findall(r"<key>ProfileIdentifier</key>\s*<string>([^<]+)</string>", profiles_plist)
    for m in matches:
        if "mdm" in m.lower() or "enrollment" in m.lower() or "jamf" in m.lower():
            return m
    return matches[0] if matches else None


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    device_id = parameters.get("device_id", "")

    if not creds:
        return {"action": "unenroll_device", "unenrolled": True, "device_id": device_id, "mock": True}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    from ._client import jamf_delete

    # Find and remove the MDM enrollment profile from the device
    profiles_result = await dispatch_agent_job("profiles_list", {}, list(asset_ids), timeout_seconds=30)
    profiles_plist = profiles_result.get("profiles_plist", "")
    mdm_identifier = _find_mdm_profile_identifier(profiles_plist)

    if mdm_identifier:
        await dispatch_agent_job(
            "profiles_remove",
            {"identifier": mdm_identifier},
            list(asset_ids),
            timeout_seconds=60,
        )

    # Delete the device record from Jamf (removes MDM management server-side)
    if device_id:
        jamf_delete(creds, f"/JSSResource/computers/id/{device_id}")

    return {
        "action": "unenroll_device",
        "unenrolled": True,
        "device_id": device_id,
        "profile_removed": mdm_identifier or "not_found",
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Re-enrollment requires executing enroll_device again.",
        "device_id": execution_result.get("device_id", ""),
    }
```

- [ ] **Step 4: Run tests**

```bash
docker exec nexplane-backend-1 python3 -m pytest tests/test_mdm_connectors.py::test_jamf_unenroll_device_mock_mode tests/test_mdm_connectors.py::test_jamf_unenroll_device_removes_profile_and_deletes_record -v 2>&1 | tail -8
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/jamf/unenroll_device.py backend/tests/test_mdm_connectors.py
git commit -m "feat(mdm/jamf): unenroll_device removes MDM profile via agent and deletes Jamf record"
```

---

## Task 9: jamf/sync_device.py, check_compliance.py, query_device.py

**Files:**
- Create: `backend/app/connectors/executors/jamf/sync_device.py`
- Create: `backend/app/connectors/executors/jamf/check_compliance.py`
- Create: `backend/app/connectors/executors/jamf/query_device.py`

- [ ] **Step 1: Add tests**

Append to `backend/tests/test_mdm_connectors.py`:

```python
@pytest.mark.asyncio
async def test_jamf_sync_device():
    from app.connectors.executors.jamf.sync_device import execute
    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
        return {"checked_in": True, "output": "Checking in..."}
    with patch("app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job", side_effect=fake_dispatch):
        result = await execute({"device_id": "42"}, ["asset-001"], MockConnectorJamf())
    assert result["synced"] is True


@pytest.mark.asyncio
async def test_jamf_check_compliance():
    from app.connectors.executors.jamf.check_compliance import execute
    mock_data = {
        "computer": {
            "general": {"id": 42, "name": "mac-test", "last_contact_time": "2026-06-03"},
            "hardware": {"os_version": "15.0"},
        }
    }
    with patch("app.connectors.executors.jamf._client.jamf_get", return_value=mock_data):
        result = await execute({"device_id": "42"}, ["asset-001"], MockConnectorJamf())
    assert result["device_id"] == "42"
    assert "last_contact_time" in result


@pytest.mark.asyncio
async def test_jamf_query_device():
    from app.connectors.executors.jamf.query_device import execute
    mock_data = {
        "computer": {
            "general": {"id": 42, "name": "mac-test"},
            "hardware": {"model": "Mac Pro", "serial_number": "SN001"},
            "software": {"applications": []},
            "configuration_profiles": [],
        }
    }
    with patch("app.connectors.executors.jamf._client.jamf_get", return_value=mock_data):
        result = await execute({"device_id": "42"}, ["asset-001"], MockConnectorJamf())
    assert result["device_id"] == "42"
    assert "hardware" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 python3 -m pytest tests/test_mdm_connectors.py::test_jamf_sync_device tests/test_mdm_connectors.py::test_jamf_check_compliance tests/test_mdm_connectors.py::test_jamf_query_device -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError` for all three

- [ ] **Step 3: Write sync_device.py**

Create `backend/app/connectors/executors/jamf/sync_device.py`:

```python
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    device_id = parameters.get("device_id", "")

    if not creds:
        return {"action": "sync_device", "device_id": device_id, "synced": True, "mock": True}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    checkin_result = await dispatch_agent_job("macos_mdm_checkin", {}, list(asset_ids), timeout_seconds=30)
    return {
        "action": "sync_device",
        "device_id": device_id,
        "synced": True,
        "checkin_output": checkin_result.get("output", ""),
    }
```

- [ ] **Step 4: Write check_compliance.py**

Create `backend/app/connectors/executors/jamf/check_compliance.py`:

```python
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    device_id = parameters.get("device_id", "")

    if not creds:
        return {
            "action": "check_compliance",
            "device_id": device_id,
            "compliant": True,
            "failing_policies": [],
            "mock": True,
        }

    from ._client import jamf_get

    data = jamf_get(creds, f"/JSSResource/computers/id/{device_id}")
    computer = data.get("computer", {})
    general = computer.get("general", {})
    hardware = computer.get("hardware", {})

    return {
        "action": "check_compliance",
        "device_id": device_id,
        "name": general.get("name", ""),
        "last_contact_time": general.get("last_contact_time", ""),
        "managed": general.get("remote_management", {}).get("managed", False),
        "os_version": hardware.get("os_version", ""),
        "model": hardware.get("model", ""),
        "serial": hardware.get("serial_number", ""),
    }
```

- [ ] **Step 5: Write query_device.py**

Create `backend/app/connectors/executors/jamf/query_device.py`:

```python
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    device_id = parameters.get("device_id", "")

    if not creds:
        return {
            "action": "query_device",
            "device_id": device_id,
            "hardware": {},
            "software": {"applications": []},
            "profiles": [],
            "mock": True,
        }

    from ._client import jamf_get

    data = jamf_get(creds, f"/JSSResource/computers/id/{device_id}")
    computer = data.get("computer", {})
    general = computer.get("general", {})
    hardware = computer.get("hardware", {})
    software = computer.get("software", {})
    profiles = computer.get("configuration_profiles", [])

    return {
        "action": "query_device",
        "device_id": device_id,
        "name": general.get("name", ""),
        "serial": hardware.get("serial_number", ""),
        "hardware": {
            "model": hardware.get("model", ""),
            "os_version": hardware.get("os_version", ""),
            "processor": hardware.get("processor_type", ""),
            "ram_mb": hardware.get("total_ram_mb", 0),
        },
        "software": {
            "applications": [
                {"name": a.get("name"), "version": a.get("version")}
                for a in (software.get("applications") or [])
            ]
        },
        "profiles": [
            {"identifier": p.get("identifier"), "display_name": p.get("display_name")}
            for p in (profiles if isinstance(profiles, list) else [])
        ],
    }
```

- [ ] **Step 6: Run all three tests**

```bash
docker exec nexplane-backend-1 python3 -m pytest tests/test_mdm_connectors.py::test_jamf_sync_device tests/test_mdm_connectors.py::test_jamf_check_compliance tests/test_mdm_connectors.py::test_jamf_query_device -v 2>&1 | tail -8
```

Expected: `3 passed`

- [ ] **Step 7: Commit**

```bash
git add backend/app/connectors/executors/jamf/sync_device.py \
        backend/app/connectors/executors/jamf/check_compliance.py \
        backend/app/connectors/executors/jamf/query_device.py \
        backend/tests/test_mdm_connectors.py
git commit -m "feat(mdm/jamf): sync_device, check_compliance, query_device executors"
```

---

## Task 10: micromdm/enroll_device.py

**Files:**
- Create: `backend/app/connectors/executors/micromdm/enroll_device.py`

- [ ] **Step 1: Add tests**

Append to `backend/tests/test_mdm_connectors.py`:

```python
# ---------------------------------------------------------------------------
# micromdm/enroll_device tests
# ---------------------------------------------------------------------------

class MockConnectorMicroMDM:
    credentials = {
        "base_url": "https://mdm.example.com",
        "api_key": "test-api-key",
    }


@pytest.mark.asyncio
async def test_micromdm_enroll_device_mock_mode():
    from app.connectors.executors.micromdm.enroll_device import execute
    result = await execute({}, ["asset-001"], MockConnectorNoCreds())
    assert result["enrolled"] is True
    assert result.get("mock") is True


@pytest.mark.asyncio
async def test_micromdm_enroll_device_with_creds():
    from app.connectors.executors.micromdm.enroll_device import execute

    sysinfo = {"serial": "SN-002", "hostname": "mac-micro"}
    profile_bytes = b"<plist>micromdm-enrollment</plist>"
    device_record = {"udid": "UDID-002", "serial_number": "SN-002", "model": "MacPro"}

    dispatch_calls = []
    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
        dispatch_calls.append(command)
        if command == "macos_sysinfo":
            return sysinfo
        return {"status": "ok"}

    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        side_effect=fake_dispatch,
    ), patch(
        "app.connectors.executors.micromdm._client.get_enrollment_profile",
        return_value=profile_bytes,
    ), patch(
        "app.connectors.executors.micromdm._client.get_device_by_serial",
        return_value=device_record,
    ), patch("asyncio.sleep", new=AsyncMock()):
        result = await execute({}, ["asset-001"], MockConnectorMicroMDM())

    assert result["enrolled"] is True
    assert result["udid"] == "UDID-002"
    assert "profiles_install" in dispatch_calls
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker exec nexplane-backend-1 python3 -m pytest tests/test_mdm_connectors.py::test_micromdm_enroll_device_mock_mode -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write the executor**

Create `backend/app/connectors/executors/micromdm/enroll_device.py`:

```python
import asyncio
import base64


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})

    if not creds:
        return {
            "action": "enroll_device",
            "enrolled": True,
            "udid": "mock-udid-micromdm",
            "serial": "mock-serial",
            "push_available": False,
            "mock": True,
        }

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    from ._client import get_enrollment_profile, get_device_by_serial

    sysinfo = await dispatch_agent_job("macos_sysinfo", {}, list(asset_ids), timeout_seconds=60)
    serial = sysinfo.get("serial", "")
    hostname = sysinfo.get("hostname", "")
    if not serial:
        raise RuntimeError("macos_sysinfo did not return a serial number — is the agent running on macOS?")

    profile_bytes = get_enrollment_profile(creds)
    profile_b64 = base64.b64encode(profile_bytes).decode("ascii")
    await dispatch_agent_job(
        "profiles_install",
        {"plist_b64": profile_b64},
        list(asset_ids),
        timeout_seconds=120,
    )

    await dispatch_agent_job("macos_mdm_checkin", {}, list(asset_ids), timeout_seconds=30)

    device = None
    for _ in range(24):
        device = get_device_by_serial(creds, serial)
        if device:
            break
        await asyncio.sleep(5)

    if not device:
        raise RuntimeError(
            f"Device with serial {serial!r} did not appear in MicroMDM inventory within 120s."
        )

    return {
        "action": "enroll_device",
        "enrolled": True,
        "udid": device["udid"],
        "serial": serial,
        "hostname": hostname,
        "push_available": False,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    udid = execution_result.get("udid", "")
    if not udid or execution_result.get("mock"):
        return {"rolled_back": True, "reason": "mock or no udid"}
    from app.connectors.executors.micromdm.unenroll_device import execute as unenroll
    return await unenroll({"udid": udid}, list(parameters.get("_asset_ids", [])), connector)
```

- [ ] **Step 4: Run tests**

```bash
docker exec nexplane-backend-1 python3 -m pytest tests/test_mdm_connectors.py::test_micromdm_enroll_device_mock_mode tests/test_mdm_connectors.py::test_micromdm_enroll_device_with_creds -v 2>&1 | tail -8
```

Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/micromdm/enroll_device.py backend/tests/test_mdm_connectors.py
git commit -m "feat(mdm/micromdm): enroll_device executor"
```

---

## Task 11: micromdm/unenroll_device.py, sync_device.py, check_compliance.py, query_device.py

**Files:**
- Create: `backend/app/connectors/executors/micromdm/unenroll_device.py`
- Create: `backend/app/connectors/executors/micromdm/sync_device.py`
- Create: `backend/app/connectors/executors/micromdm/check_compliance.py`
- Create: `backend/app/connectors/executors/micromdm/query_device.py`

- [ ] **Step 1: Add tests**

Append to `backend/tests/test_mdm_connectors.py`:

```python
@pytest.mark.asyncio
async def test_micromdm_unenroll_removes_profile_and_deletes_device():
    from app.connectors.executors.micromdm.unenroll_device import execute

    dispatch_calls = []
    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
        dispatch_calls.append({"command": command, "params": parameters})
        if command == "profiles_list":
            return {"profiles_plist": "<plist><array><dict><key>ProfileIdentifier</key><string>com.apple.mdm</string></dict></array></plist>"}
        return {"status": "ok"}

    with patch(
        "app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job",
        side_effect=fake_dispatch,
    ), patch("app.connectors.executors.micromdm._client.micromdm_delete") as mock_del:
        result = await execute({"udid": "UDID-002"}, ["asset-001"], MockConnectorMicroMDM())

    assert result["unenrolled"] is True
    assert any(c["command"] == "profiles_list" for c in dispatch_calls)
    mock_del.assert_called_once()


@pytest.mark.asyncio
async def test_micromdm_sync_device():
    from app.connectors.executors.micromdm.sync_device import execute
    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds=300):
        return {"checked_in": True}
    with patch("app.connectors.executors.nexplane_agent._dispatch.dispatch_agent_job", side_effect=fake_dispatch):
        result = await execute({"udid": "UDID-002"}, ["asset-001"], MockConnectorMicroMDM())
    assert result["synced"] is True


@pytest.mark.asyncio
async def test_micromdm_check_compliance():
    from app.connectors.executors.micromdm.check_compliance import execute
    device = {"udid": "UDID-002", "serial_number": "SN-002", "model": "MacPro", "last_seen": "2026-06-03"}
    with patch("app.connectors.executors.micromdm._client.get_device_by_udid", return_value=device):
        result = await execute({"udid": "UDID-002"}, ["asset-001"], MockConnectorMicroMDM())
    assert result["udid"] == "UDID-002"
    assert "last_seen" in result


@pytest.mark.asyncio
async def test_micromdm_query_device():
    from app.connectors.executors.micromdm.query_device import execute
    device = {"udid": "UDID-002", "serial_number": "SN-002", "model": "MacPro", "os_version": "15.0"}
    with patch("app.connectors.executors.micromdm._client.get_device_by_udid", return_value=device):
        result = await execute({"udid": "UDID-002"}, ["asset-001"], MockConnectorMicroMDM())
    assert result["udid"] == "UDID-002"
    assert result["serial"] == "SN-002"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
docker exec nexplane-backend-1 python3 -m pytest tests/test_mdm_connectors.py::test_micromdm_unenroll_removes_profile_and_deletes_device -v 2>&1 | tail -5
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write unenroll_device.py**

Create `backend/app/connectors/executors/micromdm/unenroll_device.py`:

```python
import re


def _find_mdm_profile_identifier(profiles_plist: str) -> str | None:
    matches = re.findall(r"<key>ProfileIdentifier</key>\s*<string>([^<]+)</string>", profiles_plist)
    for m in matches:
        if "mdm" in m.lower() or "enrollment" in m.lower():
            return m
    return matches[0] if matches else None


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    udid = parameters.get("udid", "")

    if not creds:
        return {"action": "unenroll_device", "unenrolled": True, "udid": udid, "mock": True}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job
    from ._client import micromdm_delete

    profiles_result = await dispatch_agent_job("profiles_list", {}, list(asset_ids), timeout_seconds=30)
    profiles_plist = profiles_result.get("profiles_plist", "")
    mdm_identifier = _find_mdm_profile_identifier(profiles_plist)

    if mdm_identifier:
        await dispatch_agent_job(
            "profiles_remove",
            {"identifier": mdm_identifier},
            list(asset_ids),
            timeout_seconds=60,
        )

    if udid:
        micromdm_delete(creds, f"/v1/devices/{udid}")

    return {
        "action": "unenroll_device",
        "unenrolled": True,
        "udid": udid,
        "profile_removed": mdm_identifier or "not_found",
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Re-enrollment requires executing enroll_device again.",
        "udid": execution_result.get("udid", ""),
    }
```

- [ ] **Step 4: Write sync_device.py**

Create `backend/app/connectors/executors/micromdm/sync_device.py`:

```python
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    udid = parameters.get("udid", "")

    if not creds:
        return {"action": "sync_device", "udid": udid, "synced": True, "mock": True}

    from app.connectors.executors.nexplane_agent._dispatch import dispatch_agent_job

    checkin_result = await dispatch_agent_job("macos_mdm_checkin", {}, list(asset_ids), timeout_seconds=30)
    return {
        "action": "sync_device",
        "udid": udid,
        "synced": True,
        "checkin_output": checkin_result.get("output", ""),
    }
```

- [ ] **Step 5: Write check_compliance.py**

Create `backend/app/connectors/executors/micromdm/check_compliance.py`:

```python
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    udid = parameters.get("udid", "")

    if not creds:
        return {"action": "check_compliance", "udid": udid, "enrolled": True, "mock": True}

    from ._client import get_device_by_udid

    device = get_device_by_udid(creds, udid)
    if not device:
        return {"action": "check_compliance", "udid": udid, "enrolled": False, "device_found": False}

    return {
        "action": "check_compliance",
        "udid": udid,
        "enrolled": True,
        "device_found": True,
        "serial": device.get("serial_number", ""),
        "model": device.get("model", ""),
        "last_seen": device.get("last_seen", ""),
        "os_version": device.get("os_version", ""),
    }
```

- [ ] **Step 6: Write query_device.py**

Create `backend/app/connectors/executors/micromdm/query_device.py`:

```python
async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    udid = parameters.get("udid", "")

    if not creds:
        return {"action": "query_device", "udid": udid, "hardware": {}, "mock": True}

    from ._client import get_device_by_udid

    device = get_device_by_udid(creds, udid)
    if not device:
        raise RuntimeError(f"Device with UDID {udid!r} not found in MicroMDM inventory.")

    return {
        "action": "query_device",
        "udid": udid,
        "serial": device.get("serial_number", ""),
        "model": device.get("model", ""),
        "os_version": device.get("os_version", ""),
        "last_seen": device.get("last_seen", ""),
        "hardware": {
            "model": device.get("model", ""),
            "os_version": device.get("os_version", ""),
        },
    }
```

- [ ] **Step 7: Run all four tests**

```bash
docker exec nexplane-backend-1 python3 -m pytest \
  tests/test_mdm_connectors.py::test_micromdm_unenroll_removes_profile_and_deletes_device \
  tests/test_mdm_connectors.py::test_micromdm_sync_device \
  tests/test_mdm_connectors.py::test_micromdm_check_compliance \
  tests/test_mdm_connectors.py::test_micromdm_query_device \
  -v 2>&1 | tail -8
```

Expected: `4 passed`

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/executors/micromdm/unenroll_device.py \
        backend/app/connectors/executors/micromdm/sync_device.py \
        backend/app/connectors/executors/micromdm/check_compliance.py \
        backend/app/connectors/executors/micromdm/query_device.py \
        backend/tests/test_mdm_connectors.py
git commit -m "feat(mdm/micromdm): unenroll_device, sync_device, check_compliance, query_device executors"
```

---

## Task 12: Full test run + agent binary build

**Files:**
- No new files — verification only

- [ ] **Step 1: Run all MDM connector tests**

```bash
docker exec nexplane-backend-1 python3 -m pytest tests/test_mdm_connectors.py -v 2>&1 | tail -20
```

Expected: all tests pass (no failures)

- [ ] **Step 2: Build darwin agent binary on EC2**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd nexplane && git pull && cd agent && GOOS=darwin GOARCH=arm64 CGO_ENABLED=0 go build -o nexplane-agent-darwin-arm64 . 2>&1"
```

Expected: no output (success), binary created

- [ ] **Step 3: Get current version and bump to 0.3.3**

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cat nexplane/agent/version.txt 2>/dev/null || echo 'no version file'"
```

If version file exists, bump to next patch. If no version file, check how the version is currently tracked:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cat nexplane/backend/static/nexplane-agent-darwin-arm64-version 2>/dev/null || docker exec nexplane-backend-1 curl -sf http://localhost:8000/api/v1/agent/version 2>/dev/null || echo 'check S3 version file'"
```

Then check S3:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "docker exec nexplane-backend-1 python3 -c \"
import boto3
s3 = boto3.client('s3')
obj = s3.get_object(Bucket='nexplane-agent-downloads', Key='version')
print(obj['Body'].read().decode())
\""
```

- [ ] **Step 4: Upload binary and bump version**

Replace `0.3.X` with the current version + 1:

```bash
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "
docker cp nexplane/agent/nexplane-agent-darwin-arm64 nexplane-backend-1:/tmp/nexplane-agent-darwin-arm64
docker exec nexplane-backend-1 python3 -c \"
import boto3
NEW_VERSION = '0.3.3'
s3 = boto3.client('s3')
s3.upload_file('/tmp/nexplane-agent-darwin-arm64', 'nexplane-agent-downloads', f'nexplane-agent-darwin-arm64-{NEW_VERSION}')
s3.put_object(Bucket='nexplane-agent-downloads', Key='version', Body=NEW_VERSION.encode())
print(f'Uploaded nexplane-agent-darwin-arm64-{NEW_VERSION}')
print(f'Version set to {NEW_VERSION}')
\"
"
```

Expected output:
```
Uploaded nexplane-agent-darwin-arm64-0.3.3
Version set to 0.3.3
```

- [ ] **Step 5: Push all commits to remote**

```bash
git -C "f:/Nexplane/nexplane" push origin master 2>&1
```

Expected: successful push of all commits from this sub-plan

---

## Self-Review Checklist

- All executors follow `async def execute(parameters, asset_ids, connector) -> dict` signature ✓
- Mock mode (no credentials) returns valid result with `mock: True` for every executor ✓
- `enroll_device` rollback delegates to `unenroll_device` ✓
- `unenroll_device` rollback is informational (re-enrollment is manual) ✓
- `get_device_by_serial` used consistently (both clients have it) ✓
- `_find_mdm_profile_identifier` duplicated between jamf and micromdm unenroll — acceptable for now (same package boundary issue that will be resolved in Sub-Plan 2 if a shared util is warranted) ✓
- Agent `macos_mdm_checkin` is non-fatal (mdmclient non-zero exit = not enrolled = OK) ✓
- `asyncio.sleep` is called inside polling loops — tests must mock it ✓

---

*Sub-Plan 2 (management surface): push_configuration_profile, schedule_os_update, os_major_upgrade, run_script, install_package, lock_device, remote_wipe, and Jamf-only actions for both connectors.*

*Sub-Plan 3 (smoke tests): MicroMDM EC2 AMI, MDM_ENROLL phase, MDM_MANAGE phase.*
