# Action Catalog, Executor Modules & Engine Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded connector logic with a JSON-driven action catalog, per-connector executor modules, and catalog-resolved planning/execution — enabling new connectors to be added without Python changes.

**Architecture:** Per-connector JSON files in `backend/app/connectors/catalog/` define every action (with `generic_action`, `execution_tier`, `executor` reference). `ActionCatalogService` loads and indexes these at startup. The planning engine reads `change_type_definitions/` JSON to get step sequences, resolves each step to the best available connector via the catalog, and stores `connector_type`+`action_id` on each plan step. Workflow activities call executor modules directly via the catalog.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, pytest + pytest-asyncio, aiosqlite (tests)

**Note:** This is Plan 1 of 2. Plan 2 covers the Project entity. Run `pytest backend/app/tests/` from the `backend/` directory throughout.

---

### Task 1: Catalog package skeleton + cloudflare_mock.json

**Files:**
- Create: `backend/app/connectors/__init__.py`
- Create: `backend/app/connectors/catalog/__init__.py`
- Create: `backend/app/connectors/catalog/cloudflare_mock.json`
- Create: `backend/app/connectors/executors/__init__.py`
- Create: `backend/app/connectors/change_type_definitions/__init__.py`

- [ ] **Step 1: Create the directory skeleton**

```bash
mkdir -p backend/app/connectors/catalog
mkdir -p backend/app/connectors/executors
mkdir -p backend/app/connectors/change_type_definitions
touch backend/app/connectors/__init__.py
touch backend/app/connectors/catalog/__init__.py
touch backend/app/connectors/executors/__init__.py
touch backend/app/connectors/change_type_definitions/__init__.py
```

- [ ] **Step 2: Write `backend/app/connectors/catalog/cloudflare_mock.json`**

```json
{
  "connector_type": "cloudflare_mock",
  "display_name": "Cloudflare (Mock)",
  "actions": [
    {
      "action_id": "capture_dns_record",
      "generic_action": "capture_dns_record",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Capture DNS Record",
      "description": "Reads the current DNS record value for rollback purposes",
      "applicable_asset_types": ["dns_zone"],
      "parameters": [
        {"name": "record_name", "type": "string", "required": true},
        {"name": "record_type", "type": "string", "required": false, "default": "A"}
      ],
      "executor": "cloudflare_mock.capture_dns_record",
      "estimated_duration_seconds": 5
    },
    {
      "action_id": "validate_dns_target",
      "generic_action": "validate_dns_target",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Validate DNS Target",
      "description": "Validates that the new DNS target value is reachable",
      "applicable_asset_types": ["dns_zone"],
      "parameters": [
        {"name": "new_value", "type": "string", "required": true}
      ],
      "executor": "cloudflare_mock.validate_dns_target",
      "estimated_duration_seconds": 5
    },
    {
      "action_id": "update_dns_record",
      "generic_action": "update_dns_record",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Update DNS Record",
      "description": "Updates a DNS record via the Cloudflare API",
      "applicable_asset_types": ["dns_zone"],
      "parameters": [
        {"name": "record_name", "type": "string", "required": true},
        {"name": "record_type", "type": "string", "required": true},
        {"name": "new_value", "type": "string", "required": true},
        {"name": "ttl", "type": "integer", "required": false, "default": 300}
      ],
      "executor": "cloudflare_mock.update_dns_record",
      "rollback_action": "restore_dns_record",
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "dns_propagation_delay",
      "safety_notes": ["TTL propagation may take up to 5 minutes"]
    },
    {
      "action_id": "wait_dns_propagation",
      "generic_action": "wait_dns_propagation",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Wait for DNS Propagation",
      "description": "Waits for DNS record change to propagate across resolvers",
      "applicable_asset_types": ["dns_zone"],
      "parameters": [
        {"name": "ttl", "type": "integer", "required": false, "default": 300}
      ],
      "executor": "cloudflare_mock.wait_dns_propagation",
      "estimated_duration_seconds": 300
    },
    {
      "action_id": "restore_dns_record",
      "generic_action": "restore_dns_record",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Restore DNS Record",
      "description": "Restores a DNS record to its previously captured value",
      "applicable_asset_types": ["dns_zone"],
      "parameters": [
        {"name": "record_name", "type": "string", "required": true},
        {"name": "previous_value", "type": "string", "required": true}
      ],
      "executor": "cloudflare_mock.restore_dns_record",
      "estimated_duration_seconds": 10
    }
  ]
}
```

- [ ] **Step 3: Verify JSON is valid**

```bash
python -c "import json; json.load(open('backend/app/connectors/catalog/cloudflare_mock.json')); print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add backend/app/connectors/
git commit -m "feat: add connectors package skeleton and cloudflare_mock catalog"
```

---

### Task 2: Remaining connector catalog JSON files

**Files:**
- Create: `backend/app/connectors/catalog/aws_mock.json`
- Create: `backend/app/connectors/catalog/okta_mock.json`
- Create: `backend/app/connectors/catalog/ssh_mock.json`
- Create: `backend/app/connectors/catalog/paloalto_mock.json`

- [ ] **Step 1: Write `backend/app/connectors/catalog/aws_mock.json`**

```json
{
  "connector_type": "aws_mock",
  "display_name": "Amazon Web Services (Mock)",
  "actions": [
    {
      "action_id": "health_check",
      "generic_action": "health_check",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Asset Health Check",
      "description": "Verifies asset health before making changes",
      "applicable_asset_types": ["server", "cloud_account"],
      "parameters": [],
      "executor": "aws_mock.health_check",
      "estimated_duration_seconds": 5
    },
    {
      "action_id": "create_snapshot",
      "generic_action": "create_snapshot",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Create Asset Snapshot",
      "description": "Creates a point-in-time snapshot of an asset",
      "applicable_asset_types": ["server", "cloud_account"],
      "parameters": [
        {"name": "snapshot_tag", "type": "string", "required": false, "default": "nexplane-managed"}
      ],
      "executor": "aws_mock.create_snapshot",
      "estimated_duration_seconds": 60,
      "blast_radius_hint": "snapshot_io_impact"
    },
    {
      "action_id": "verify_snapshot",
      "generic_action": "verify_snapshot",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Verify Snapshot Integrity",
      "description": "Verifies that a snapshot was created successfully and is complete",
      "applicable_asset_types": ["server", "cloud_account"],
      "parameters": [],
      "executor": "aws_mock.verify_snapshot",
      "estimated_duration_seconds": 15
    },
    {
      "action_id": "export_security_group",
      "generic_action": "export_security_group",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Export Security Group Rules",
      "description": "Exports current security group rules for rollback purposes",
      "applicable_asset_types": ["cloud_account", "firewall"],
      "parameters": [
        {"name": "group_id", "type": "string", "required": true}
      ],
      "executor": "aws_mock.export_security_group",
      "estimated_duration_seconds": 5
    },
    {
      "action_id": "validate_security_rules",
      "generic_action": "validate_security_rules",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Validate Security Group Rules",
      "description": "Validates proposed security group rule syntax before applying",
      "applicable_asset_types": ["cloud_account", "firewall"],
      "parameters": [
        {"name": "rules", "type": "array", "required": true}
      ],
      "executor": "aws_mock.validate_security_rules",
      "estimated_duration_seconds": 5
    },
    {
      "action_id": "update_security_group",
      "generic_action": "update_security_group",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Update Security Group",
      "description": "Applies security group rule changes via the AWS API",
      "applicable_asset_types": ["cloud_account", "firewall"],
      "parameters": [
        {"name": "group_id", "type": "string", "required": true},
        {"name": "rules", "type": "array", "required": true}
      ],
      "executor": "aws_mock.update_security_group",
      "rollback_action": "restore_security_group",
      "estimated_duration_seconds": 15,
      "blast_radius_hint": "network_access_change",
      "safety_notes": ["Verify rules do not block critical management traffic"]
    },
    {
      "action_id": "restore_security_group",
      "generic_action": "restore_security_group",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Restore Security Group",
      "description": "Restores security group rules from a previously captured snapshot",
      "applicable_asset_types": ["cloud_account", "firewall"],
      "parameters": [
        {"name": "pre_change_snapshot_id", "type": "string", "required": true}
      ],
      "executor": "aws_mock.restore_security_group",
      "estimated_duration_seconds": 15
    }
  ]
}
```

- [ ] **Step 2: Write `backend/app/connectors/catalog/okta_mock.json`**

```json
{
  "connector_type": "okta_mock",
  "display_name": "Okta (Mock)",
  "actions": [
    {
      "action_id": "generate_key",
      "generic_action": "generate_key",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Generate New Key",
      "description": "Generates a new API key or credential",
      "applicable_asset_types": ["identity_provider"],
      "parameters": [
        {"name": "service", "type": "string", "required": true},
        {"name": "key_type", "type": "string", "required": false, "default": "api_key"}
      ],
      "executor": "okta_mock.generate_key",
      "estimated_duration_seconds": 5
    },
    {
      "action_id": "distribute_key",
      "generic_action": "distribute_key",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Distribute New Key",
      "description": "Distributes the new key to all registered consumers",
      "applicable_asset_types": ["identity_provider"],
      "parameters": [
        {"name": "consumers", "type": "array", "required": true}
      ],
      "executor": "okta_mock.distribute_key",
      "estimated_duration_seconds": 10
    },
    {
      "action_id": "verify_consumers",
      "generic_action": "verify_consumers",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Verify Consumer Health",
      "description": "Confirms all consumers are operating correctly with the new key",
      "applicable_asset_types": ["identity_provider"],
      "parameters": [
        {"name": "consumers", "type": "array", "required": true}
      ],
      "executor": "okta_mock.verify_consumers",
      "estimated_duration_seconds": 15
    },
    {
      "action_id": "schedule_revoke",
      "generic_action": "schedule_revoke",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Schedule Old Key Revocation",
      "description": "Schedules the old key for revocation after a grace period",
      "applicable_asset_types": ["identity_provider"],
      "parameters": [
        {"name": "grace_period_hours", "type": "integer", "required": false, "default": 24}
      ],
      "executor": "okta_mock.schedule_revoke",
      "rollback_action": "cancel_revoke",
      "estimated_duration_seconds": 5
    },
    {
      "action_id": "cancel_revoke",
      "generic_action": "cancel_revoke",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Cancel Key Revocation",
      "description": "Cancels a scheduled key revocation, restoring the old key to active status",
      "applicable_asset_types": ["identity_provider"],
      "parameters": [
        {"name": "old_key_id", "type": "string", "required": true}
      ],
      "executor": "okta_mock.cancel_revoke",
      "estimated_duration_seconds": 5
    }
  ]
}
```

- [ ] **Step 3: Write `backend/app/connectors/catalog/ssh_mock.json`**

```json
{
  "connector_type": "ssh_mock",
  "display_name": "SSH Runner (Mock)",
  "actions": [
    {
      "action_id": "check_prerequisites",
      "generic_action": "check_prerequisites",
      "action_type": "change",
      "execution_tier": 5,
      "display_name": "Check Agent Prerequisites",
      "description": "Verifies that host meets requirements for agent installation",
      "applicable_asset_types": ["server"],
      "parameters": [],
      "executor": "ssh_mock.check_prerequisites",
      "estimated_duration_seconds": 10
    },
    {
      "action_id": "download_package",
      "generic_action": "download_package",
      "action_type": "change",
      "execution_tier": 5,
      "display_name": "Download Agent Package",
      "description": "Downloads the agent installation package to the host",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "agent_type", "type": "string", "required": true},
        {"name": "agent_version", "type": "string", "required": false, "default": "8.12.0"}
      ],
      "executor": "ssh_mock.download_package",
      "estimated_duration_seconds": 30
    },
    {
      "action_id": "install_agent",
      "generic_action": "install_agent",
      "action_type": "change",
      "execution_tier": 5,
      "display_name": "Install Agent",
      "description": "Installs the telemetry or management agent on the host",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "agent_type", "type": "string", "required": true},
        {"name": "agent_config", "type": "object", "required": false}
      ],
      "executor": "ssh_mock.install_agent",
      "rollback_action": "uninstall_agent",
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "host_service_interruption"
    },
    {
      "action_id": "uninstall_agent",
      "generic_action": "uninstall_agent",
      "action_type": "change",
      "execution_tier": 5,
      "display_name": "Uninstall Agent",
      "description": "Removes the agent from the host",
      "applicable_asset_types": ["server"],
      "parameters": [],
      "executor": "ssh_mock.uninstall_agent",
      "estimated_duration_seconds": 15
    },
    {
      "action_id": "start_service",
      "generic_action": "start_service",
      "action_type": "change",
      "execution_tier": 5,
      "display_name": "Start and Enable Service",
      "description": "Starts the installed agent service and enables it on boot",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "agent_type", "type": "string", "required": true}
      ],
      "executor": "ssh_mock.start_service",
      "estimated_duration_seconds": 10
    },
    {
      "action_id": "validate_template",
      "generic_action": "validate_template",
      "action_type": "change",
      "execution_tier": 5,
      "display_name": "Validate Command Template",
      "description": "Validates that the specified command template is approved and parameters are valid",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "template_id", "type": "string", "required": true},
        {"name": "parameters", "type": "object", "required": false}
      ],
      "executor": "ssh_mock.validate_template",
      "estimated_duration_seconds": 2
    },
    {
      "action_id": "execute_template",
      "generic_action": "execute_template",
      "action_type": "change",
      "execution_tier": 5,
      "display_name": "Execute Approved Command Template",
      "description": "Executes a pre-approved command template on target hosts. No freeform commands.",
      "applicable_asset_types": ["server"],
      "parameters": [
        {"name": "template_id", "type": "string", "required": true},
        {"name": "parameters", "type": "object", "required": false}
      ],
      "executor": "ssh_mock.execute_template",
      "estimated_duration_seconds": 30,
      "blast_radius_hint": "host_service_interruption",
      "safety_notes": ["Only approved templates may be executed — no freeform commands permitted"]
    },
    {
      "action_id": "collect_output",
      "generic_action": "collect_output",
      "action_type": "change",
      "execution_tier": 5,
      "display_name": "Collect Execution Output",
      "description": "Collects and aggregates output from remote command execution",
      "applicable_asset_types": ["server"],
      "parameters": [],
      "executor": "ssh_mock.collect_output",
      "estimated_duration_seconds": 5
    }
  ]
}
```

- [ ] **Step 4: Write `backend/app/connectors/catalog/paloalto_mock.json`**

```json
{
  "connector_type": "paloalto_mock",
  "display_name": "Palo Alto Networks (Mock)",
  "actions": [
    {
      "action_id": "analyze_flows",
      "generic_action": "analyze_flows",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Analyze Current Traffic Flows",
      "description": "Analyzes existing traffic flows to understand current network behavior",
      "applicable_asset_types": ["firewall"],
      "parameters": [],
      "executor": "paloalto_mock.analyze_flows",
      "estimated_duration_seconds": 20
    },
    {
      "action_id": "generate_diff",
      "generic_action": "generate_diff",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Generate Policy Diff",
      "description": "Generates a diff between current and proposed firewall policies",
      "applicable_asset_types": ["firewall"],
      "parameters": [
        {"name": "policy_rules", "type": "array", "required": true}
      ],
      "executor": "paloalto_mock.generate_diff",
      "estimated_duration_seconds": 10
    },
    {
      "action_id": "stage_policy",
      "generic_action": "stage_policy",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Stage Policy (Simulation Mode)",
      "description": "Stages the new policy in simulation mode — no traffic is affected",
      "applicable_asset_types": ["firewall"],
      "parameters": [
        {"name": "policy_rules", "type": "array", "required": true},
        {"name": "critical_flows", "type": "array", "required": false}
      ],
      "executor": "paloalto_mock.stage_policy",
      "rollback_action": "remove_staged_policy",
      "estimated_duration_seconds": 15,
      "blast_radius_hint": "simulation_only",
      "safety_notes": ["Simulation mode only — live enforcement requires a separate approval workflow"]
    },
    {
      "action_id": "remove_staged_policy",
      "generic_action": "remove_staged_policy",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Remove Staged Policy",
      "description": "Removes a staged simulation-mode policy",
      "applicable_asset_types": ["firewall"],
      "parameters": [
        {"name": "staged_policy_id", "type": "string", "required": true}
      ],
      "executor": "paloalto_mock.remove_staged_policy",
      "estimated_duration_seconds": 5
    },
    {
      "action_id": "validate_staged",
      "generic_action": "validate_staged",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Validate Staged Policy",
      "description": "Validates that the staged policy does not block critical flows",
      "applicable_asset_types": ["firewall"],
      "parameters": [
        {"name": "critical_flows", "type": "array", "required": false}
      ],
      "executor": "paloalto_mock.validate_staged",
      "estimated_duration_seconds": 10
    }
  ]
}
```

- [ ] **Step 5: Validate all JSON files**

```bash
python -c "
import json, pathlib
for f in pathlib.Path('backend/app/connectors/catalog').glob('*.json'):
    json.load(open(f))
    print(f'OK: {f.name}')
"
```
Expected: `OK:` line for each file, no errors.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/catalog/
git commit -m "feat: add connector catalog JSON files for all mock connectors"
```

---

### Task 3: Change type definition JSON files

**Files:**
- Create: `backend/app/connectors/change_type_definitions/dns_update.json`
- Create: `backend/app/connectors/change_type_definitions/snapshot_asset.json`
- Create: `backend/app/connectors/change_type_definitions/security_group_update.json`
- Create: `backend/app/connectors/change_type_definitions/key_rotation.json`
- Create: `backend/app/connectors/change_type_definitions/telemetry_agent_deploy.json`
- Create: `backend/app/connectors/change_type_definitions/remote_command.json`
- Create: `backend/app/connectors/change_type_definitions/microsegmentation_policy.json`

- [ ] **Step 1: Write all seven definition files**

`backend/app/connectors/change_type_definitions/dns_update.json`:
```json
{
  "change_type": "dns_update",
  "display_name": "DNS Record Update",
  "steps": [
    {"generic_action": "capture_dns_record",  "purpose": "preflight_capture",  "required": true},
    {"generic_action": "validate_dns_target", "purpose": "preflight_validate", "required": true},
    {"generic_action": "update_dns_record",   "purpose": "execute",            "required": true},
    {"generic_action": "wait_dns_propagation","purpose": "verify",             "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["dns_lookup", "http_probe"]
}
```

`backend/app/connectors/change_type_definitions/snapshot_asset.json`:
```json
{
  "change_type": "snapshot_asset",
  "display_name": "Asset Snapshot",
  "steps": [
    {"generic_action": "health_check",    "purpose": "preflight_capture", "required": true},
    {"generic_action": "create_snapshot", "purpose": "execute",           "required": true},
    {"generic_action": "verify_snapshot", "purpose": "verify",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["api_check"]
}
```

`backend/app/connectors/change_type_definitions/security_group_update.json`:
```json
{
  "change_type": "security_group_update",
  "display_name": "Security Group Update",
  "steps": [
    {"generic_action": "export_security_group",   "purpose": "preflight_capture",  "required": true},
    {"generic_action": "validate_security_rules", "purpose": "preflight_validate", "required": true},
    {"generic_action": "update_security_group",   "purpose": "execute",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["api_check"]
}
```

`backend/app/connectors/change_type_definitions/key_rotation.json`:
```json
{
  "change_type": "key_rotation",
  "display_name": "Key Rotation",
  "steps": [
    {"generic_action": "generate_key",    "purpose": "execute",  "required": true},
    {"generic_action": "distribute_key",  "purpose": "execute",  "required": true},
    {"generic_action": "verify_consumers","purpose": "verify",   "required": true},
    {"generic_action": "schedule_revoke", "purpose": "execute",  "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["auth_test", "health_check"]
}
```

`backend/app/connectors/change_type_definitions/telemetry_agent_deploy.json`:
```json
{
  "change_type": "telemetry_agent_deploy",
  "display_name": "Telemetry Agent Deploy",
  "steps": [
    {"generic_action": "check_prerequisites", "purpose": "preflight_validate", "required": true},
    {"generic_action": "download_package",    "purpose": "preflight_capture",  "required": true},
    {"generic_action": "install_agent",       "purpose": "execute",            "required": true},
    {"generic_action": "start_service",       "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["service_check", "health_check"]
}
```

`backend/app/connectors/change_type_definitions/remote_command.json`:
```json
{
  "change_type": "remote_command",
  "display_name": "Remote Command",
  "steps": [
    {"generic_action": "validate_template", "purpose": "preflight_validate", "required": true},
    {"generic_action": "execute_template",  "purpose": "execute",            "required": true},
    {"generic_action": "collect_output",    "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["output_check"]
}
```

`backend/app/connectors/change_type_definitions/microsegmentation_policy.json`:
```json
{
  "change_type": "microsegmentation_policy",
  "display_name": "Microsegmentation Policy",
  "steps": [
    {"generic_action": "analyze_flows",        "purpose": "preflight_capture",  "required": true},
    {"generic_action": "generate_diff",        "purpose": "preflight_validate", "required": true},
    {"generic_action": "stage_policy",         "purpose": "execute",            "required": true},
    {"generic_action": "validate_staged",      "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["flow_test"]
}
```

- [ ] **Step 2: Validate all JSON files**

```bash
python -c "
import json, pathlib
for f in pathlib.Path('backend/app/connectors/change_type_definitions').glob('*.json'):
    json.load(open(f))
    print(f'OK: {f.name}')
"
```
Expected: `OK:` for each of 7 files.

- [ ] **Step 3: Commit**

```bash
git add backend/app/connectors/change_type_definitions/
git commit -m "feat: add change_type_definitions JSON files"
```

---

### Task 4: ActionCatalogService — load and index

**Files:**
- Create: `backend/app/connectors/catalog_service.py`
- Create: `backend/app/tests/test_catalog_service.py`

- [ ] **Step 1: Write the failing test**

`backend/app/tests/test_catalog_service.py`:
```python
import pathlib
import pytest
from app.connectors.catalog_service import ActionCatalogService, ActionOption

CATALOG_DIR = pathlib.Path(__file__).parent.parent / "connectors" / "catalog"


def test_load_builds_connector_index():
    svc = ActionCatalogService(CATALOG_DIR)
    assert "cloudflare_mock" in svc._catalog
    assert len(svc._catalog["cloudflare_mock"]) == 5  # 5 actions defined


def test_load_builds_generic_index():
    svc = ActionCatalogService(CATALOG_DIR)
    assert "update_dns_record" in svc._generic_index
    options = svc._generic_index["update_dns_record"]
    assert len(options) == 1
    assert options[0].connector_type == "cloudflare_mock"
    assert options[0].action_id == "update_dns_record"
    assert options[0].execution_tier == 1


def test_load_indexes_all_connectors():
    svc = ActionCatalogService(CATALOG_DIR)
    assert set(svc._catalog.keys()) == {
        "cloudflare_mock", "aws_mock", "okta_mock", "ssh_mock", "paloalto_mock"
    }
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
cd backend && pytest app/tests/test_catalog_service.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError` or `ImportError` — `catalog_service` does not exist yet.

- [ ] **Step 3: Implement `ActionCatalogService` load + index**

`backend/app/connectors/catalog_service.py`:
```python
from __future__ import annotations
import json
import pathlib
from dataclasses import dataclass
from typing import Callable


@dataclass
class ActionOption:
    connector_type: str
    action_id: str
    action_def: dict
    execution_tier: int


class ActionCatalogService:
    def __init__(self, catalog_dir: pathlib.Path):
        self._catalog: dict[str, list[dict]] = {}
        self._generic_index: dict[str, list[ActionOption]] = {}
        self._load(catalog_dir)

    def _load(self, catalog_dir: pathlib.Path) -> None:
        for json_file in sorted(catalog_dir.glob("*.json")):
            data = json.loads(json_file.read_text())
            connector_type = data["connector_type"]
            actions = data.get("actions", [])
            self._catalog[connector_type] = actions
            for action_def in actions:
                generic = action_def["generic_action"]
                option = ActionOption(
                    connector_type=connector_type,
                    action_id=action_def["action_id"],
                    action_def=action_def,
                    execution_tier=action_def.get("execution_tier", 99),
                )
                self._generic_index.setdefault(generic, []).append(option)
        # Sort each generic index entry by execution_tier ascending
        for key in self._generic_index:
            self._generic_index[key].sort(key=lambda o: o.execution_tier)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
cd backend && pytest app/tests/test_catalog_service.py -v
```
Expected: 3 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/catalog_service.py backend/app/tests/test_catalog_service.py
git commit -m "feat: add ActionCatalogService with load and index"
```

---

### Task 5: ActionCatalogService — query methods

**Files:**
- Modify: `backend/app/connectors/catalog_service.py`
- Modify: `backend/app/tests/test_catalog_service.py`

- [ ] **Step 1: Add failing tests for query methods**

Append to `backend/app/tests/test_catalog_service.py`:
```python
def test_get_options_for_action_returns_sorted_by_tier():
    svc = ActionCatalogService(CATALOG_DIR)
    options = svc.get_options_for_action("execute_template")
    assert len(options) == 1
    assert options[0].connector_type == "ssh_mock"
    assert options[0].execution_tier == 5


def test_get_options_filters_by_asset_type():
    svc = ActionCatalogService(CATALOG_DIR)
    options = svc.get_options_for_action("update_dns_record", asset_types=["server"])
    assert len(options) == 0  # cloudflare only applies to dns_zone

    options = svc.get_options_for_action("update_dns_record", asset_types=["dns_zone"])
    assert len(options) == 1


def test_get_options_filters_by_active_connectors():
    svc = ActionCatalogService(CATALOG_DIR)
    options = svc.get_options_for_action(
        "update_dns_record", active_connector_types=["aws_mock"]
    )
    assert len(options) == 0  # aws_mock can't update DNS

    options = svc.get_options_for_action(
        "update_dns_record", active_connector_types=["cloudflare_mock"]
    )
    assert len(options) == 1


def test_get_action_def_returns_correct_def():
    svc = ActionCatalogService(CATALOG_DIR)
    defn = svc.get_action_def("cloudflare_mock", "update_dns_record")
    assert defn["executor"] == "cloudflare_mock.update_dns_record"
    assert defn["rollback_action"] == "restore_dns_record"


def test_get_action_def_raises_for_unknown():
    svc = ActionCatalogService(CATALOG_DIR)
    with pytest.raises(KeyError):
        svc.get_action_def("cloudflare_mock", "does_not_exist")


def test_list_generic_actions_change_only():
    svc = ActionCatalogService(CATALOG_DIR)
    actions = svc.list_generic_actions(action_type="change")
    assert "update_dns_record" in actions
    assert "capture_dns_record" in actions


def test_list_generic_actions_all():
    svc = ActionCatalogService(CATALOG_DIR)
    all_actions = svc.list_generic_actions()
    assert len(all_actions) > 0
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd backend && pytest app/tests/test_catalog_service.py -v 2>&1 | tail -15
```
Expected: `AttributeError: 'ActionCatalogService' object has no attribute 'get_options_for_action'`

- [ ] **Step 3: Add query methods to `catalog_service.py`**

Add after `_load` in `ActionCatalogService`:
```python
    def get_options_for_action(
        self,
        generic_action: str,
        asset_types: list[str] | None = None,
        active_connector_types: list[str] | None = None,
    ) -> list[ActionOption]:
        options = self._generic_index.get(generic_action, [])
        if asset_types is not None:
            options = [
                o for o in options
                if any(t in o.action_def.get("applicable_asset_types", []) for t in asset_types)
            ]
        if active_connector_types is not None:
            options = [o for o in options if o.connector_type in active_connector_types]
        return options

    def get_action_def(self, connector_type: str, action_id: str) -> dict:
        actions = self._catalog.get(connector_type, [])
        for action in actions:
            if action["action_id"] == action_id:
                return action
        raise KeyError(f"Action '{action_id}' not found in connector '{connector_type}'")

    def list_generic_actions(self, action_type: str | None = None) -> list[str]:
        if action_type is None:
            return list(self._generic_index.keys())
        result = set()
        for generic, options in self._generic_index.items():
            if any(o.action_def.get("action_type") == action_type for o in options):
                result.add(generic)
        return list(result)
```

- [ ] **Step 4: Run tests**

```bash
cd backend && pytest app/tests/test_catalog_service.py -v
```
Expected: all 10 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/catalog_service.py backend/app/tests/test_catalog_service.py
git commit -m "feat: add ActionCatalogService query methods"
```

---

### Task 6: ActionCatalogService — executor resolution + singleton

**Files:**
- Modify: `backend/app/connectors/catalog_service.py`
- Modify: `backend/app/tests/test_catalog_service.py`

- [ ] **Step 1: Add failing test for executor resolution**

Append to `backend/app/tests/test_catalog_service.py`:
```python
def test_get_executor_raises_before_executors_exist():
    svc = ActionCatalogService(CATALOG_DIR)
    with pytest.raises(ImportError):
        svc.get_executor("cloudflare_mock", "update_dns_record")


def test_singleton_init_and_get():
    from app.connectors.catalog_service import init_catalog_service, get_catalog_service
    init_catalog_service(CATALOG_DIR)
    svc = get_catalog_service()
    assert isinstance(svc, ActionCatalogService)
    assert "cloudflare_mock" in svc._catalog
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd backend && pytest app/tests/test_catalog_service.py::test_get_executor_raises_before_executors_exist app/tests/test_catalog_service.py::test_singleton_init_and_get -v
```
Expected: both FAIL with `AttributeError`.

- [ ] **Step 3: Add executor resolution and singleton to `catalog_service.py`**

Add at the bottom of the file, after the class definition:
```python
    def get_executor(self, connector_type: str, action_id: str) -> object:
        """
        Resolves 'connector_type.module_name' executor reference to a module.
        The module must live at app/connectors/executors/{connector_type}/{action_id}.py
        and expose execute() and optionally rollback().
        """
        action_def = self.get_action_def(connector_type, action_id)
        executor_ref = action_def.get("executor", "")
        parts = executor_ref.split(".")
        if len(parts) != 2:
            raise ValueError(f"Invalid executor reference '{executor_ref}' — expected 'connector.module'")
        module_path = f"app.connectors.executors.{parts[0]}.{parts[1]}"
        import importlib
        try:
            return importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            raise ImportError(f"Executor module '{module_path}' not found: {exc}") from exc


_catalog_service: ActionCatalogService | None = None


def init_catalog_service(catalog_dir: pathlib.Path) -> None:
    global _catalog_service
    _catalog_service = ActionCatalogService(catalog_dir)


def get_catalog_service() -> ActionCatalogService:
    global _catalog_service
    if _catalog_service is None:
        default_dir = pathlib.Path(__file__).parent / "catalog"
        init_catalog_service(default_dir)
    return _catalog_service
```

- [ ] **Step 4: Run all catalog tests**

```bash
cd backend && pytest app/tests/test_catalog_service.py -v
```
Expected: 12 tests PASSED (`test_get_executor_raises_before_executors_exist` passes because executor modules don't exist yet).

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/catalog_service.py backend/app/tests/test_catalog_service.py
git commit -m "feat: add executor resolution and singleton to ActionCatalogService"
```

---

### Task 7: Executor stubs for all catalogued actions

**Files:** Create `backend/app/connectors/executors/{connector}/` packages with stub modules for every `executor` reference in the catalog JSON files.

- [ ] **Step 1: Write a helper script to generate stubs, then run it**

```bash
python - <<'EOF'
import json, pathlib, textwrap

catalog_dir = pathlib.Path("backend/app/connectors/catalog")
executors_dir = pathlib.Path("backend/app/connectors/executors")

for json_file in catalog_dir.glob("*.json"):
    data = json.loads(json_file.read_text())
    connector_type = data["connector_type"]
    pkg_dir = executors_dir / connector_type
    pkg_dir.mkdir(exist_ok=True)
    init = pkg_dir / "__init__.py"
    if not init.exists():
        init.write_text("")
    for action in data["actions"]:
        executor_ref = action["executor"]  # e.g. "cloudflare_mock.update_dns_record"
        module_name = executor_ref.split(".")[1]
        mod_file = pkg_dir / f"{module_name}.py"
        if not mod_file.exists():
            mod_file.write_text(textwrap.dedent(f'''
                async def execute(parameters: dict, asset_ids: list, connector) -> dict:
                    return {{"action": "{action["action_id"]}", "status": "stub"}}

                async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
                    return {{"rolled_back": True, "action": "{action["action_id"]}_rollback", "status": "stub"}}
            ''').lstrip())
            print(f"Created stub: {mod_file}")
        else:
            print(f"Exists: {mod_file}")
EOF
```

- [ ] **Step 2: Write a test that resolves every executor in the catalog**

Append to `backend/app/tests/test_catalog_service.py`:
```python
def test_all_catalog_executors_resolve():
    import pathlib
    catalog_dir = pathlib.Path(__file__).parent.parent / "connectors" / "catalog"
    svc = ActionCatalogService(catalog_dir)
    for connector_type, actions in svc._catalog.items():
        for action in actions:
            mod = svc.get_executor(connector_type, action["action_id"])
            assert hasattr(mod, "execute"), (
                f"{connector_type}.{action['action_id']} executor missing execute()"
            )
```

- [ ] **Step 3: Run the new test**

```bash
cd backend && pytest app/tests/test_catalog_service.py::test_all_catalog_executors_resolve -v
```
Expected: PASSED.

- [ ] **Step 4: Run full catalog test suite**

```bash
cd backend && pytest app/tests/test_catalog_service.py -v
```
Expected: all 13 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/
git commit -m "feat: add executor stubs for all catalogued actions"
```

---

### Task 8: Implement cloudflare_mock executors

**Files:**
- Modify: `backend/app/connectors/executors/cloudflare_mock/capture_dns_record.py`
- Modify: `backend/app/connectors/executors/cloudflare_mock/validate_dns_target.py`
- Modify: `backend/app/connectors/executors/cloudflare_mock/update_dns_record.py`
- Modify: `backend/app/connectors/executors/cloudflare_mock/wait_dns_propagation.py`
- Modify: `backend/app/connectors/executors/cloudflare_mock/restore_dns_record.py`
- Create: `backend/app/tests/test_executors_cloudflare.py`

- [ ] **Step 1: Write failing tests**

`backend/app/tests/test_executors_cloudflare.py`:
```python
import pytest
from app.connectors.executors.cloudflare_mock import (
    capture_dns_record,
    validate_dns_target,
    update_dns_record,
    wait_dns_propagation,
    restore_dns_record,
)


@pytest.mark.asyncio
async def test_capture_dns_record_returns_current_value():
    result = await capture_dns_record.execute(
        {"record_name": "api.example.com", "record_type": "A"}, [], None
    )
    assert result["action"] == "capture_dns_record"
    assert "current_value" in result
    assert result["record_name"] == "api.example.com"


@pytest.mark.asyncio
async def test_validate_dns_target_passes():
    result = await validate_dns_target.execute({"new_value": "1.2.3.4"}, [], None)
    assert result["action"] == "validate_dns_target"
    assert result["reachable"] is True


@pytest.mark.asyncio
async def test_update_dns_record_returns_previous_and_new():
    result = await update_dns_record.execute(
        {"record_name": "api.example.com", "record_type": "A", "new_value": "1.2.3.4", "ttl": 300},
        [], None
    )
    assert result["action"] == "update_dns_record"
    assert result["new_value"] == "1.2.3.4"
    assert "previous_value" in result
    assert "propagation_id" in result


@pytest.mark.asyncio
async def test_update_dns_record_rollback_restores():
    result = await update_dns_record.rollback(
        {"record_name": "api.example.com"},
        {"previous_value": "10.0.0.1"},
        None
    )
    assert result["rolled_back"] is True
    assert result["restored_value"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_restore_dns_record_uses_previous_value():
    result = await restore_dns_record.execute(
        {"record_name": "api.example.com", "previous_value": "10.0.0.1"}, [], None
    )
    assert result["action"] == "restore_dns_record"
    assert result["restored_value"] == "10.0.0.1"


@pytest.mark.asyncio
async def test_wait_dns_propagation_succeeds():
    result = await wait_dns_propagation.execute({"ttl": 300}, [], None)
    assert result["action"] == "wait_dns_propagation"
    assert result["propagated"] is True
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd backend && pytest app/tests/test_executors_cloudflare.py -v 2>&1 | tail -10
```
Expected: 6 tests FAILED (stub returns `{"status": "stub"}`).

- [ ] **Step 3: Implement `capture_dns_record.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "capture_dns_record",
        "record_name": parameters.get("record_name"),
        "record_type": parameters.get("record_type", "A"),
        "current_value": "203.0.113.10",  # mock current value
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "capture has no rollback"}
```

- [ ] **Step 4: Implement `validate_dns_target.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "validate_dns_target",
        "new_value": parameters.get("new_value"),
        "reachable": True,
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "validation has no rollback"}
```

- [ ] **Step 5: Implement `update_dns_record.py`**

```python
import random, string
from datetime import datetime, timezone

def _fake_id(prefix: str = "") -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"{prefix}{suffix}"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "update_dns_record",
        "record_name": parameters.get("record_name"),
        "record_type": parameters.get("record_type", "A"),
        "previous_value": "203.0.113.10",
        "new_value": parameters.get("new_value"),
        "ttl": parameters.get("ttl", 300),
        "propagation_id": _fake_id("prop-"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    previous_value = execution_result.get("previous_value", "unknown")
    return {
        "rolled_back": True,
        "action": "restore_dns_record",
        "restored_value": previous_value,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 6: Implement `wait_dns_propagation.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "wait_dns_propagation",
        "ttl_seconds": parameters.get("ttl", 300),
        "propagated": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "propagation wait has no rollback"}
```

- [ ] **Step 7: Implement `restore_dns_record.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "restore_dns_record",
        "record_name": parameters.get("record_name"),
        "restored_value": parameters.get("previous_value"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore has no further rollback"}
```

- [ ] **Step 8: Run tests**

```bash
cd backend && pytest app/tests/test_executors_cloudflare.py -v
```
Expected: all 6 tests PASSED.

- [ ] **Step 9: Commit**

```bash
git add backend/app/connectors/executors/cloudflare_mock/ backend/app/tests/test_executors_cloudflare.py
git commit -m "feat: implement cloudflare_mock executor modules"
```

---

### Task 9: Implement aws_mock executors

**Files:**
- Modify: `backend/app/connectors/executors/aws_mock/*.py`
- Create: `backend/app/tests/test_executors_aws.py`

- [ ] **Step 1: Write failing tests**

`backend/app/tests/test_executors_aws.py`:
```python
import pytest
from app.connectors.executors.aws_mock import (
    health_check, create_snapshot, verify_snapshot,
    export_security_group, validate_security_rules,
    update_security_group, restore_security_group,
)


@pytest.mark.asyncio
async def test_health_check_passes():
    result = await health_check.execute({}, ["asset-1"], None)
    assert result["action"] == "health_check"
    assert result["healthy"] is True


@pytest.mark.asyncio
async def test_create_snapshot_returns_per_asset():
    result = await create_snapshot.execute(
        {"snapshot_tag": "test"}, ["asset-1", "asset-2"], None
    )
    assert result["action"] == "create_snapshot"
    assert len(result["snapshots"]) == 2
    assert all(s["snapshot_id"].startswith("snap-") for s in result["snapshots"])


@pytest.mark.asyncio
async def test_verify_snapshot_passes():
    result = await verify_snapshot.execute({}, ["asset-1"], None)
    assert result["verified"] is True


@pytest.mark.asyncio
async def test_update_security_group_records_rules():
    rules = [{"action": "add", "protocol": "tcp", "port": 443, "source": "0.0.0.0/0"}]
    result = await update_security_group.execute(
        {"group_id": "sg-123", "rules": rules}, ["asset-1"], None
    )
    assert result["action"] == "update_security_group"
    assert result["rules_applied"] == 1
    assert "pre_change_snapshot_id" in result


@pytest.mark.asyncio
async def test_update_security_group_rollback():
    result = await update_security_group.rollback(
        {}, {"pre_change_snapshot_id": "sgsnap-abc"}, None
    )
    assert result["rolled_back"] is True
    assert result["restored_from_snapshot"] == "sgsnap-abc"
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd backend && pytest app/tests/test_executors_aws.py -v 2>&1 | tail -10
```
Expected: 5 FAILED.

- [ ] **Step 3: Implement `health_check.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "health_check",
        "healthy": True,
        "assets_checked": asset_ids,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "health check has no rollback"}
```

- [ ] **Step 4: Implement `create_snapshot.py`**

```python
import random, string
from datetime import datetime, timezone

def _fake_id(prefix=""):
    return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    snapshots = [
        {"asset_id": a, "snapshot_id": _fake_id("snap-"), "size_gb": random.randint(20, 500), "status": "completed"}
        for a in asset_ids
    ]
    return {
        "action": "create_snapshot",
        "snapshot_tag": parameters.get("snapshot_tag", "nexplane-managed"),
        "snapshots": snapshots,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "snapshot is its own rollback mechanism"}
```

- [ ] **Step 5: Implement `verify_snapshot.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "verify_snapshot",
        "verified": True,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "verification has no rollback"}
```

- [ ] **Step 6: Implement `export_security_group.py`**

```python
import random, string
from datetime import datetime, timezone

def _fake_id(prefix=""):
    return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "export_security_group",
        "group_id": parameters.get("group_id", _fake_id("sg-")),
        "snapshot_id": _fake_id("sgsnap-"),
        "rules_captured": 3,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "export has no rollback"}
```

- [ ] **Step 7: Implement `validate_security_rules.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    rules = parameters.get("rules", [])
    return {
        "action": "validate_security_rules",
        "rules_validated": len(rules),
        "valid": True,
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "validation has no rollback"}
```

- [ ] **Step 8: Implement `update_security_group.py`**

```python
import random, string
from datetime import datetime, timezone

def _fake_id(prefix=""):
    return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    rules = parameters.get("rules", [])
    return {
        "action": "update_security_group",
        "group_id": parameters.get("group_id", _fake_id("sg-")),
        "rules_applied": len(rules),
        "rules_added": [r for r in rules if r.get("action") == "add"],
        "rules_removed": [r for r in rules if r.get("action") == "remove"],
        "pre_change_snapshot_id": _fake_id("sgsnap-"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "action": "security_group_restore",
        "restored_from_snapshot": execution_result.get("pre_change_snapshot_id"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 9: Implement `restore_security_group.py`**

```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "restore_security_group",
        "restored_from_snapshot": parameters.get("pre_change_snapshot_id"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore has no further rollback"}
```

- [ ] **Step 10: Run tests**

```bash
cd backend && pytest app/tests/test_executors_aws.py -v
```
Expected: all 5 PASSED.

- [ ] **Step 11: Commit**

```bash
git add backend/app/connectors/executors/aws_mock/ backend/app/tests/test_executors_aws.py
git commit -m "feat: implement aws_mock executor modules"
```

---

### Task 10: Implement okta_mock, ssh_mock, paloalto_mock executors

**Files:**
- Modify: `backend/app/connectors/executors/okta_mock/*.py`
- Modify: `backend/app/connectors/executors/ssh_mock/*.py`
- Modify: `backend/app/connectors/executors/paloalto_mock/*.py`
- Create: `backend/app/tests/test_executors_others.py`

- [ ] **Step 1: Write failing tests**

`backend/app/tests/test_executors_others.py`:
```python
import pytest
from app.connectors.executors.okta_mock import generate_key, distribute_key, verify_consumers, schedule_revoke, cancel_revoke
from app.connectors.executors.ssh_mock import validate_template, execute_template, collect_output, install_agent, uninstall_agent, check_prerequisites, download_package, start_service
from app.connectors.executors.paloalto_mock import analyze_flows, generate_diff, stage_policy, remove_staged_policy, validate_staged


@pytest.mark.asyncio
async def test_generate_key_returns_new_key_id():
    result = await generate_key.execute({"service": "payment-api", "key_type": "api_key"}, [], None)
    assert result["action"] == "generate_key"
    assert result["new_key_id"].startswith("key-")


@pytest.mark.asyncio
async def test_schedule_revoke_rollback_cancels():
    result = await schedule_revoke.rollback({}, {"old_key_id": "key-old-123"}, None)
    assert result["rolled_back"] is True
    assert result["old_key_id"] == "key-old-123"


@pytest.mark.asyncio
async def test_execute_template_approved_succeeds():
    result = await execute_template.execute(
        {"template_id": "restart_service", "parameters": {"service_name": "nginx"}},
        ["host-1"], None
    )
    assert result["action"] == "execute_template"
    assert result["host_results"][0]["exit_code"] == 0


@pytest.mark.asyncio
async def test_execute_template_unapproved_raises():
    from app.connectors.catalog_service import ActionCatalogService
    with pytest.raises(ValueError, match="not approved"):
        await execute_template.execute(
            {"template_id": "rm_everything", "parameters": {}},
            ["host-1"], None
        )


@pytest.mark.asyncio
async def test_execute_template_freeform_raises():
    with pytest.raises(ValueError, match="freeform"):
        await execute_template.execute(
            {"template_id": "restart_service", "freeform_command": "rm -rf /"},
            ["host-1"], None
        )


@pytest.mark.asyncio
async def test_stage_policy_simulation_mode():
    result = await stage_policy.execute(
        {"policy_rules": [{"src": "web", "dst": "api", "port": 443}], "critical_flows": []},
        ["fw-1"], None
    )
    assert result["mode"] == "simulation"
    assert result["staged_policy_id"].startswith("pol-")


@pytest.mark.asyncio
async def test_stage_policy_rollback_removes():
    result = await stage_policy.rollback({}, {"staged_policy_id": "pol-abc123"}, None)
    assert result["rolled_back"] is True
    assert result["policy_id"] == "pol-abc123"
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd backend && pytest app/tests/test_executors_others.py -v 2>&1 | tail -15
```
Expected: 7 FAILED.

- [ ] **Step 3: Implement okta_mock executors**

`generate_key.py`:
```python
import random, string
from datetime import datetime, timezone

def _fake_id(prefix=""):
    return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "generate_key",
        "new_key_id": _fake_id("key-"),
        "old_key_id": _fake_id("key-old-"),
        "key_type": parameters.get("key_type", "api_key"),
        "service": parameters.get("service"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "key generation has no rollback"}
```

`distribute_key.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    consumers = parameters.get("consumers", [])
    return {
        "action": "distribute_key",
        "consumers_updated": consumers,
        "distributed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "distribution rollback handled by cancel_revoke"}
```

`verify_consumers.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    consumers = parameters.get("consumers", [])
    return {
        "action": "verify_consumers",
        "consumers_verified": consumers,
        "all_healthy": True,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "verification has no rollback"}
```

`schedule_revoke.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "schedule_revoke",
        "grace_period_hours": parameters.get("grace_period_hours", 24),
        "revocation_scheduled_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "action": "revocation_cancelled",
        "old_key_id": execution_result.get("old_key_id"),
        "old_key_status": "active",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
```

`cancel_revoke.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "cancel_revoke",
        "old_key_id": parameters.get("old_key_id"),
        "old_key_status": "active",
        "cancelled_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cancel has no further rollback"}
```

- [ ] **Step 4: Implement ssh_mock executors**

`check_prerequisites.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "check_prerequisites", "prerequisites_met": True, "checked_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "check has no rollback"}
```

`download_package.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "download_package",
        "agent_type": parameters.get("agent_type"),
        "agent_version": parameters.get("agent_version", "8.12.0"),
        "downloaded": True,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "download has no rollback"}
```

`install_agent.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    hosts = [{"asset_id": a, "agent_installed": True, "agent_version": "8.12.0"} for a in asset_ids]
    return {
        "action": "install_agent",
        "agent_type": parameters.get("agent_type"),
        "hosts": hosts,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    hosts = execution_result.get("hosts", [])
    return {
        "rolled_back": True,
        "action": "agent_uninstalled",
        "hosts_cleaned": [h["asset_id"] for h in hosts],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
```

`uninstall_agent.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "uninstall_agent", "hosts_cleaned": asset_ids, "completed_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "uninstall has no rollback"}
```

`start_service.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "start_service",
        "agent_type": parameters.get("agent_type"),
        "service_status": "active",
        "hosts": [{"asset_id": a, "service_status": "active"} for a in asset_ids],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "service start has no automatic rollback"}
```

`validate_template.py`:
```python
from app.services.safety_engine import APPROVED_COMMAND_TEMPLATES
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    template_id = parameters.get("template_id")
    if not template_id or template_id not in APPROVED_COMMAND_TEMPLATES:
        raise ValueError(f"Command template '{template_id}' is not approved. Allowed: {list(APPROVED_COMMAND_TEMPLATES.keys())}")
    return {
        "action": "validate_template",
        "template_id": template_id,
        "valid": True,
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "validation has no rollback"}
```

`execute_template.py`:
```python
import random
from datetime import datetime, timezone
from app.services.safety_engine import APPROVED_COMMAND_TEMPLATES

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    template_id = parameters.get("template_id")
    if not template_id or template_id not in APPROVED_COMMAND_TEMPLATES:
        raise ValueError(f"Command template '{template_id}' is not approved")
    if parameters.get("freeform_command"):
        raise ValueError("Freeform commands are not permitted")
    host_results = [
        {"asset_id": a, "exit_code": 0, "stdout": f"[mock] Executed '{template_id}'", "stderr": "", "duration_ms": random.randint(50, 500)}
        for a in asset_ids
    ]
    return {
        "action": "execute_template",
        "template_id": template_id,
        "parameters": parameters.get("parameters", {}),
        "host_results": host_results,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "command execution rollback is manual"}
```

`collect_output.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "collect_output", "collected": True, "collected_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "output collection has no rollback"}
```

- [ ] **Step 5: Implement paloalto_mock executors**

`analyze_flows.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "analyze_flows", "flows_analyzed": 42, "analyzed_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "analysis has no rollback"}
```

`generate_diff.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    rules = parameters.get("policy_rules", [])
    return {"action": "generate_diff", "rules_in_diff": len(rules), "generated_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "diff generation has no rollback"}
```

`stage_policy.py`:
```python
import random, string
from datetime import datetime, timezone

def _fake_id(prefix=""):
    return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    rules = parameters.get("policy_rules", [])
    critical = parameters.get("critical_flows", [])
    return {
        "action": "stage_policy",
        "mode": "simulation",
        "staged_policy_id": _fake_id("pol-"),
        "rules_staged": len(rules),
        "simulation_result": "no_violations",
        "critical_flows_checked": len(critical),
        "note": "Policy staged in simulation mode only.",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": True,
        "action": "staged_policy_removed",
        "policy_id": execution_result.get("staged_policy_id"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
```

`remove_staged_policy.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {
        "action": "remove_staged_policy",
        "staged_policy_id": parameters.get("staged_policy_id"),
        "removed_at": datetime.now(timezone.utc).isoformat(),
    }

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "removal has no rollback"}
```

`validate_staged.py`:
```python
from datetime import datetime, timezone

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    return {"action": "validate_staged", "no_critical_flows_blocked": True, "validated_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "validation has no rollback"}
```

- [ ] **Step 6: Run tests**

```bash
cd backend && pytest app/tests/test_executors_others.py -v
```
Expected: all 7 PASSED.

- [ ] **Step 7: Run the full catalog resolver test to confirm all executors resolve**

```bash
cd backend && pytest app/tests/test_catalog_service.py::test_all_catalog_executors_resolve -v
```
Expected: PASSED.

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/executors/ backend/app/tests/test_executors_others.py
git commit -m "feat: implement okta_mock, ssh_mock, paloalto_mock executor modules"
```

---

### Task 11: Refactor connector_service.py to thin dispatcher

**Files:**
- Modify: `backend/app/services/connector_service.py`
- Modify: `backend/app/tests/test_connector_service.py`

The existing `execute_change(change_type, desired_outcome, asset_ids, connector_type)` is replaced by `execute_action(connector_type, action_id, parameters, asset_ids, connector=None)`. The old tests are updated to match.

- [ ] **Step 1: Update existing connector service tests to use new interface**

Replace `backend/app/tests/test_connector_service.py` entirely:
```python
import pytest
from app.services.connector_service import execute_action, execute_rollback, run_preflight_checks, run_verification_checks, ConnectorError


@pytest.mark.asyncio
async def test_dns_update_returns_previous_value():
    result = await execute_action(
        "cloudflare_mock", "update_dns_record",
        {"record_name": "api.example.com", "record_type": "A", "new_value": "1.2.3.4", "ttl": 300},
        ["asset-1"],
    )
    assert result["action"] == "update_dns_record"
    assert result["previous_value"] is not None
    assert result["new_value"] == "1.2.3.4"
    assert "propagation_id" in result


@pytest.mark.asyncio
async def test_snapshot_returns_snapshot_ids():
    result = await execute_action(
        "aws_mock", "create_snapshot",
        {"snapshot_tag": "test"},
        ["asset-1", "asset-2"],
    )
    assert result["action"] == "create_snapshot"
    assert len(result["snapshots"]) == 2
    assert all(s["snapshot_id"].startswith("snap-") for s in result["snapshots"])


@pytest.mark.asyncio
async def test_remote_command_approved_template_succeeds():
    result = await execute_action(
        "ssh_mock", "execute_template",
        {"template_id": "restart_service", "parameters": {"service_name": "nginx"}},
        ["host-1"],
    )
    assert result["action"] == "execute_template"
    assert result["host_results"][0]["exit_code"] == 0


@pytest.mark.asyncio
async def test_remote_command_unapproved_template_raises():
    with pytest.raises((ValueError, ConnectorError)):
        await execute_action(
            "ssh_mock", "execute_template",
            {"template_id": "rm_everything", "parameters": {}},
            ["host-1"],
        )


@pytest.mark.asyncio
async def test_microsegmentation_staged_only():
    result = await execute_action(
        "paloalto_mock", "stage_policy",
        {"policy_rules": [{"src": "a", "dst": "b", "port": 443}]},
        ["asset-1"],
    )
    assert result["mode"] == "simulation"
    assert "staged_policy_id" in result


@pytest.mark.asyncio
async def test_unknown_connector_raises():
    with pytest.raises((KeyError, ImportError, ConnectorError)):
        await execute_action("nonexistent_connector", "some_action", {}, [])


@pytest.mark.asyncio
async def test_preflight_all_pass():
    checks = [{"name": "check_1", "description": "test", "check_type": "connectivity", "expected_result": "ok"}]
    result = await run_preflight_checks(checks)
    assert result["all_passed"] is True


@pytest.mark.asyncio
async def test_verification_passes_for_mock():
    plan = {"checks": [{"name": "dns_resolves", "description": "DNS check", "method": "dns_lookup"}], "success_criteria": "DNS resolves"}
    result = await run_verification_checks(plan, {"action": "update_dns_record"})
    assert result["all_passed"] is True


@pytest.mark.asyncio
async def test_rollback_dns_via_execute_action():
    result = await execute_action(
        "cloudflare_mock", "restore_dns_record",
        {"record_name": "api.example.com", "previous_value": "10.0.0.1"},
        [],
    )
    assert result["restored_value"] == "10.0.0.1"
```

- [ ] **Step 2: Run updated tests to confirm they fail (connector_service not yet refactored)**

```bash
cd backend && pytest app/tests/test_connector_service.py -v 2>&1 | tail -15
```
Expected: failures on `execute_action` not found.

- [ ] **Step 3: Rewrite `connector_service.py`**

```python
import asyncio
import random
import string
from datetime import datetime, timezone
from typing import Any

from app.connectors.catalog_service import get_catalog_service


class ConnectorError(Exception):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


async def test_connector(connector_type: str) -> dict:
    await asyncio.sleep(0.1)
    endpoint_map = {
        "aws_mock": "https://mock.aws.nexplane.local",
        "azure_mock": "https://mock.azure.nexplane.local",
        "cloudflare_mock": "https://mock.cloudflare.nexplane.local",
        "okta_mock": "https://mock.okta.nexplane.local",
        "paloalto_mock": "https://mock.paloalto.nexplane.local",
        "ssh_mock": "ssh://mock.runner.nexplane.local",
    }
    return {
        "success": True,
        "latency_ms": random.randint(12, 85),
        "message": f"Mock connector '{connector_type}' is reachable",
        "details": {
            "endpoint": endpoint_map.get(connector_type, "mock://local"),
            "auth_method": "mock_token",
            "permissions_verified": True,
        },
    }


async def execute_action(
    connector_type: str,
    action_id: str,
    parameters: dict,
    asset_ids: list[str],
    connector: Any = None,
) -> dict:
    catalog = get_catalog_service()
    executor = catalog.get_executor(connector_type, action_id)
    return await executor.execute(parameters, asset_ids, connector)


async def run_preflight_checks(preflight_checks: list[dict]) -> dict:
    await asyncio.sleep(0.2)
    results = [{"name": c["name"], "passed": True, "detail": "Check passed"} for c in preflight_checks]
    return {"all_passed": True, "results": results}


async def run_verification_checks(verification_plan: dict, execution_result: dict) -> dict:
    await asyncio.sleep(0.3)
    checks = verification_plan.get("checks", [])
    results = [{"name": c["name"], "passed": True, "detail": f"Mock verification passed: {c['description']}"} for c in checks]
    return {
        "all_passed": True,
        "results": results,
        "success_criteria": verification_plan.get("success_criteria", ""),
    }
```

- [ ] **Step 4: Run tests**

```bash
cd backend && pytest app/tests/test_connector_service.py -v
```
Expected: all 9 tests PASSED.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
cd backend && pytest app/tests/ -v 2>&1 | tail -20
```
Expected: all tests PASSED. If `test_planning_engine.py` references the old `connector_action` step field — note the failures; they will be fixed in Task 12.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/connector_service.py backend/app/tests/test_connector_service.py
git commit -m "refactor: connector_service.py to thin dispatcher using ActionCatalogService"
```

---

### Task 12: Refactor planning_engine.py to catalog-driven

**Files:**
- Modify: `backend/app/services/planning_engine.py`
- Modify: `backend/app/tests/test_planning_engine.py`

- [ ] **Step 1: Update planning engine tests**

Replace `backend/app/tests/test_planning_engine.py`:
```python
import pathlib
import uuid
import pytest
from app.models.asset import Asset, AssetType, Environment, Criticality
from app.models.change_request import ChangeRequest, ChangeType, ChangeRequestStatus
from app.services.planning_engine import generate_plan
from app.services.safety_engine import score_change_request
from app.connectors.catalog_service import init_catalog_service

CATALOG_DIR = pathlib.Path(__file__).parent.parent / "connectors" / "catalog"


def setup_module():
    init_catalog_service(CATALOG_DIR)


def make_asset(asset_type=AssetType.dns_zone, env=Environment.prod, crit=Criticality.high):
    return Asset(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), name="Test Asset",
        asset_type=asset_type, environment=env, criticality=crit, asset_metadata={}
    )


def make_cr(change_type, desired):
    asset = make_asset()
    cr = ChangeRequest(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), requester_id=uuid.uuid4(),
        title="Test", description="", change_type=change_type,
        target_asset_ids=[], desired_outcome=desired, status=ChangeRequestStatus.draft,
    )
    return cr, [asset]


def test_dns_update_generates_four_steps():
    cr, assets = make_cr(ChangeType.dns_update, {"record_name": "test.example", "new_value": "1.2.3.4", "rollback_strategy": "restore_previous_record"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    assert len(plan.generated_steps) == 4
    assert plan.generated_steps[0]["generic_action"] == "capture_dns_record"
    assert plan.generated_steps[2]["generic_action"] == "update_dns_record"


def test_steps_have_connector_type_and_action_id():
    cr, assets = make_cr(ChangeType.dns_update, {"record_name": "x", "new_value": "1.1.1.1"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    for step in plan.generated_steps:
        assert "connector_type" in step
        assert "action_id" in step
        assert "execution_tier" in step
        assert "connector_options" in step


def test_dns_step_3_has_rollback_action():
    cr, assets = make_cr(ChangeType.dns_update, {"record_name": "x", "new_value": "1.1.1.1"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    execute_step = plan.generated_steps[2]  # update_dns_record
    assert execute_step["rollback_action"] == "restore_dns_record"
    assert execute_step["rollback_connector_type"] == "cloudflare_mock"


def test_snapshot_generates_three_steps():
    asset = make_asset(asset_type=AssetType.server)
    cr = ChangeRequest(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), requester_id=uuid.uuid4(),
        title="Test", description="", change_type=ChangeType.snapshot_asset,
        target_asset_ids=[], desired_outcome={"snapshot_tag": "test"}, status=ChangeRequestStatus.draft,
    )
    safety = score_change_request(cr, [asset])
    plan = generate_plan(cr, [asset], safety)
    assert len(plan.generated_steps) == 3


def test_key_rotation_generates_four_steps():
    asset = make_asset(asset_type=AssetType.identity_provider)
    cr = ChangeRequest(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), requester_id=uuid.uuid4(),
        title="Test", description="", change_type=ChangeType.key_rotation,
        target_asset_ids=[], desired_outcome={"rollback_strategy": "cancel_revocation"}, status=ChangeRequestStatus.draft,
    )
    safety = score_change_request(cr, [asset])
    plan = generate_plan(cr, [asset], safety)
    assert len(plan.generated_steps) == 4
    assert any(s["generic_action"] == "schedule_revoke" for s in plan.generated_steps)


def test_remote_command_plan_includes_template_validation():
    asset = make_asset(asset_type=AssetType.server)
    cr = ChangeRequest(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), requester_id=uuid.uuid4(),
        title="Test", description="", change_type=ChangeType.remote_command,
        target_asset_ids=[], desired_outcome={"template_id": "restart_service", "parameters": {}, "rollback_strategy": "manual"},
        status=ChangeRequestStatus.draft,
    )
    safety = score_change_request(cr, [asset])
    plan = generate_plan(cr, [asset], safety)
    assert any(s["generic_action"] == "validate_template" for s in plan.generated_steps)


def test_blast_radius_includes_environments():
    cr, assets = make_cr(ChangeType.dns_update, {"record_name": "x", "new_value": "1.1.1.1"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    assert "affected_environments" in plan.blast_radius
    assert "rollback_available" in plan.blast_radius


def test_dns_rollback_plan_is_automatic():
    cr, assets = make_cr(ChangeType.dns_update, {"rollback_strategy": "restore_previous_record"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    assert plan.rollback_plan["strategy"] == "restore_previous_record"
    assert plan.rollback_plan["automatic"] is True


def test_verification_plan_has_checks():
    cr, assets = make_cr(ChangeType.dns_update, {"record_name": "x", "new_value": "1.1.1.1"})
    safety = score_change_request(cr, assets)
    plan = generate_plan(cr, assets, safety)
    assert "checks" in plan.verification_plan
    assert len(plan.verification_plan["checks"]) >= 1
```

- [ ] **Step 2: Run to confirm failures**

```bash
cd backend && pytest app/tests/test_planning_engine.py -v 2>&1 | tail -15
```
Expected: failures on `generic_action` key missing from steps.

- [ ] **Step 3: Rewrite `planning_engine.py`**

```python
from __future__ import annotations
import json
import pathlib
from dataclasses import dataclass, field
from typing import Any

from app.models.asset import Asset
from app.models.change_request import ChangeRequest, ChangeType
from app.services.safety_engine import score_change_request, SafetyReviewResult

CHANGE_TYPE_DEFS_DIR = pathlib.Path(__file__).parent.parent / "connectors" / "change_type_definitions"


def _load_change_type_def(change_type: ChangeType) -> dict:
    path = CHANGE_TYPE_DEFS_DIR / f"{change_type.value}.json"
    return json.loads(path.read_text())


def _resolve_parameters(generic_action: str, desired: dict, assets: list[Asset]) -> dict:
    asset_ids = [str(a.id) for a in assets]
    resolvers: dict[str, dict] = {
        "capture_dns_record": {"record_name": desired.get("record_name", ""), "record_type": desired.get("record_type", "A")},
        "validate_dns_target": {"new_value": desired.get("new_value", "")},
        "update_dns_record": {"record_name": desired.get("record_name", ""), "record_type": desired.get("record_type", "A"), "new_value": desired.get("new_value", ""), "ttl": desired.get("ttl", 300)},
        "wait_dns_propagation": {"ttl": desired.get("ttl", 300)},
        "restore_dns_record": {"record_name": desired.get("record_name", "")},
        "health_check": {},
        "create_snapshot": {"snapshot_tag": desired.get("snapshot_tag", "nexplane-managed")},
        "verify_snapshot": {},
        "export_security_group": {"group_id": desired.get("group_id", "")},
        "validate_security_rules": {"rules": desired.get("rules", [])},
        "update_security_group": {"group_id": desired.get("group_id", ""), "rules": desired.get("rules", [])},
        "restore_security_group": {},
        "generate_key": {"service": desired.get("service", ""), "key_type": desired.get("key_type", "api_key")},
        "distribute_key": {"consumers": desired.get("consumers", [])},
        "verify_consumers": {"consumers": desired.get("consumers", [])},
        "schedule_revoke": {"grace_period_hours": desired.get("grace_period_hours", 24)},
        "cancel_revoke": {},
        "check_prerequisites": {},
        "download_package": {"agent_type": desired.get("agent_type", ""), "agent_version": desired.get("agent_version", "8.12.0")},
        "install_agent": {"agent_type": desired.get("agent_type", ""), "agent_config": desired.get("agent_config", {})},
        "uninstall_agent": {},
        "start_service": {"agent_type": desired.get("agent_type", "")},
        "validate_template": {"template_id": desired.get("template_id", ""), "parameters": desired.get("parameters", {})},
        "execute_template": {"template_id": desired.get("template_id", ""), "parameters": desired.get("parameters", {})},
        "collect_output": {},
        "analyze_flows": {},
        "generate_diff": {"policy_rules": desired.get("policy_rules", [])},
        "stage_policy": {"policy_rules": desired.get("policy_rules", []), "critical_flows": desired.get("critical_flows", [])},
        "remove_staged_policy": {},
        "validate_staged": {"critical_flows": desired.get("critical_flows", [])},
    }
    return resolvers.get(generic_action, {})


def _resolve_step(
    step_def: dict,
    step_number: int,
    desired: dict,
    assets: list[Asset],
    catalog,
) -> dict:
    generic_action = step_def["generic_action"]
    asset_types = [a.asset_type.value for a in assets]
    options = catalog.get_options_for_action(generic_action, asset_types=asset_types)

    if not options:
        options = catalog.get_options_for_action(generic_action)

    if not options:
        return {
            "step_number": step_number,
            "name": generic_action.replace("_", " ").title(),
            "description": f"No connector found for action '{generic_action}'",
            "generic_action": generic_action,
            "connector_type": "unknown",
            "action_id": generic_action,
            "execution_tier": 99,
            "connector_options": [],
            "parameters": _resolve_parameters(generic_action, desired, assets),
            "rollback_action": None,
            "rollback_connector_type": None,
            "estimated_duration_seconds": 30,
        }

    best = options[0]
    action_def = best.action_def
    rollback_action = action_def.get("rollback_action")
    rollback_connector = best.connector_type if rollback_action else None

    return {
        "step_number": step_number,
        "name": action_def.get("display_name", generic_action.replace("_", " ").title()),
        "description": action_def.get("description", ""),
        "generic_action": generic_action,
        "connector_type": best.connector_type,
        "action_id": best.action_id,
        "execution_tier": best.execution_tier,
        "connector_options": [
            {"connector_type": o.connector_type, "action_id": o.action_id, "execution_tier": o.execution_tier}
            for o in options
        ],
        "parameters": _resolve_parameters(generic_action, desired, assets),
        "rollback_action": rollback_action,
        "rollback_connector_type": rollback_connector,
        "estimated_duration_seconds": action_def.get("estimated_duration_seconds", 30),
        "blast_radius_hint": action_def.get("blast_radius_hint"),
    }


@dataclass
class ChangePlanData:
    generated_steps: list[dict]
    preflight_checks: list[dict]
    blast_radius: dict
    rollback_plan: dict
    verification_plan: dict


def generate_plan(
    change_request: ChangeRequest,
    assets: list[Asset],
    safety_result: SafetyReviewResult,
    catalog=None,
) -> ChangePlanData:
    from app.connectors.catalog_service import get_catalog_service
    if catalog is None:
        catalog = get_catalog_service()

    ct = change_request.change_type
    desired = change_request.desired_outcome or {}

    change_def = _load_change_type_def(ct)
    steps = [
        _resolve_step(step_def, i + 1, desired, assets, catalog)
        for i, step_def in enumerate(change_def["steps"])
    ]

    return ChangePlanData(
        generated_steps=steps,
        preflight_checks=_generate_preflight_checks(change_def, desired, assets),
        blast_radius=_calculate_blast_radius(change_request, assets, safety_result),
        rollback_plan=_generate_rollback_plan(ct, desired, assets),
        verification_plan=_generate_verification_plan(change_def, desired, assets),
    )


def _generate_preflight_checks(change_def: dict, desired: dict, assets: list[Asset]) -> list[dict]:
    checks = []
    for check_name in change_def.get("preflight_checks", []):
        checks.append({
            "name": check_name,
            "description": check_name.replace("_", " ").title(),
            "check_type": "standard",
            "expected_result": "pass",
        })
    return checks


def _calculate_blast_radius(cr: ChangeRequest, assets: list[Asset], safety: SafetyReviewResult) -> dict:
    envs = list({a.environment.value for a in assets})
    return {
        "affected_assets": [{"id": str(a.id), "name": a.name, "type": a.asset_type.value, "env": a.environment.value, "criticality": a.criticality.value} for a in assets],
        "affected_environments": envs,
        "estimated_impact": safety_result_to_impact(safety),
        "affected_services": [],
        "recovery_time_estimate": "5-30 minutes",
        "rollback_available": safety.risk_level.value not in ["critical"],
    }


def safety_result_to_impact(safety: SafetyReviewResult) -> str:
    return f"Risk level: {safety.risk_level.value}. Score: {safety.risk_score}."


def _generate_rollback_plan(ct: ChangeType, desired: dict, assets: list[Asset]) -> dict:
    strategies = {
        ChangeType.dns_update: ("restore_previous_record", True),
        ChangeType.snapshot_asset: ("rollback_unavailable", False),
        ChangeType.security_group_update: ("restore_rule_snapshot", True),
        ChangeType.key_rotation: ("cancel_revocation", True),
        ChangeType.telemetry_agent_deploy: ("uninstall_agent", True),
        ChangeType.remote_command: ("manual", False),
        ChangeType.microsegmentation_policy: ("remove_staged_policy", True),
    }
    strategy, automatic = strategies.get(ct, ("manual", False))
    return {
        "strategy": strategy,
        "description": f"Rollback via {strategy}",
        "estimated_duration_seconds": 30,
        "automatic": automatic,
    }


def _generate_verification_plan(change_def: dict, desired: dict, assets: list[Asset]) -> dict:
    methods = change_def.get("verification_methods", ["api_check"])
    return {
        "checks": [{"name": m, "description": f"Verify via {m}", "method": m} for m in methods],
        "success_criteria": "All verification checks must pass",
    }
```

- [ ] **Step 4: Run planning engine tests**

```bash
cd backend && pytest app/tests/test_planning_engine.py -v
```
Expected: all tests PASSED.

- [ ] **Step 5: Run full test suite**

```bash
cd backend && pytest app/tests/ -v 2>&1 | tail -20
```
Expected: all tests PASSED.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/planning_engine.py backend/app/tests/test_planning_engine.py
git commit -m "refactor: planning_engine.py to catalog-driven step resolution"
```

---

### Task 13: Update activities.py + load_change_request_and_plan

The workflow activities now execute per-step using the catalog, rather than a single bulk `execute_change` call.

**Files:**
- Modify: `backend/app/workflows/activities.py`

- [ ] **Step 1: Update `load_change_request_and_plan` to return generated_steps**

In `activities.py`, change the return dict in `load_change_request_and_plan` to include `generated_steps`:

```python
        return {
            "change_request_id": str(cr.id),
            "organization_id": str(cr.organization_id),
            "change_type": cr.change_type.value,
            "desired_outcome": cr.desired_outcome,
            "target_asset_ids": cr.target_asset_ids,
            "risk_level": cr.risk_level.value,
            "plan_id": str(plan.id) if plan else None,
            "generated_steps": plan.generated_steps if plan else [],
            "preflight_checks": plan.preflight_checks if plan else [],
            "rollback_plan": plan.rollback_plan if plan else {},
            "verification_plan": plan.verification_plan if plan else {},
            "execution_run_id": str(latest_run.id) if latest_run else None,
        }
```

- [ ] **Step 2: Replace `activity_execute_change`**

Replace the existing `activity_execute_change` function:

```python
async def activity_execute_change(
    change_request_id: str,
    generated_steps: list[dict],
    asset_ids: list[str],
) -> dict:
    from app.connectors.catalog_service import get_catalog_service
    from app.services.connector_service import execute_action

    catalog = get_catalog_service()
    step_results = []

    for step in generated_steps:
        connector_type = step.get("connector_type", "")
        action_id = step.get("action_id", "")
        parameters = step.get("parameters", {})

        try:
            result = await execute_action(connector_type, action_id, parameters, asset_ids)
        except Exception as exc:
            logger.error("Step %s failed: %s", step.get("step_number"), exc)
            raise

        step_results.append({
            "step_number": step["step_number"],
            "generic_action": step.get("generic_action"),
            "action_id": action_id,
            "connector_type": connector_type,
            "result": result,
        })
        logger.info("Step %s (%s) completed", step.get("step_number"), action_id)

    logger.info("All steps completed for change request %s", change_request_id)
    return {"steps": step_results}
```

- [ ] **Step 3: Replace `activity_execute_rollback`**

Replace the existing `activity_execute_rollback`:

```python
async def activity_execute_rollback(
    change_request_id: str,
    generated_steps: list[dict],
    execution_result: dict,
) -> dict:
    from app.services.connector_service import execute_action

    step_results = execution_result.get("steps", [])
    rollback_results = []

    for step in reversed(generated_steps):
        rollback_action = step.get("rollback_action")
        rollback_connector = step.get("rollback_connector_type")
        if not rollback_action or not rollback_connector:
            continue

        matching = next(
            (s["result"] for s in step_results if s["step_number"] == step["step_number"]),
            {},
        )
        try:
            result = await execute_action(rollback_connector, rollback_action, {}, [], None)
        except Exception as exc:
            logger.error("Rollback step %s failed: %s", step.get("step_number"), exc)
            result = {"rolled_back": False, "error": str(exc)}

        rollback_results.append({
            "step_number": step["step_number"],
            "rollback_action": rollback_action,
            "result": result,
        })

    logger.info("Rollback complete for %s", change_request_id)
    return {"rollback_steps": rollback_results}
```

- [ ] **Step 4: Update `execute_change_workflow.py` to pass generated_steps**

Open `backend/app/workflows/execute_change_workflow.py` and update the two calls that use `activity_execute_change` and `activity_execute_rollback` to pass `generated_steps` instead of `change_type`/`desired_outcome`:

Find:
```python
    execution_result = await activities.activity_execute_change(
        input.change_request_id,
        workflow_data["change_type"],
        workflow_data["desired_outcome"],
        workflow_data["target_asset_ids"],
    )
```

Replace with:
```python
    execution_result = await activities.activity_execute_change(
        input.change_request_id,
        workflow_data["generated_steps"],
        workflow_data["target_asset_ids"],
    )
```

Find:
```python
    await activities.activity_execute_rollback(
        input.change_request_id,
        workflow_data["rollback_plan"],
        execution_result,
    )
```

Replace with:
```python
    await activities.activity_execute_rollback(
        input.change_request_id,
        workflow_data["generated_steps"],
        execution_result,
    )
```

- [ ] **Step 5: Run full test suite**

```bash
cd backend && pytest app/tests/ -v 2>&1 | tail -20
```
Expected: all tests PASSED.

- [ ] **Step 6: Commit**

```bash
git add backend/app/workflows/activities.py backend/app/workflows/execute_change_workflow.py
git commit -m "refactor: activities use catalog-driven per-step execution"
```

---

### Task 14: Update safety_engine.py for execution_tier + wire up main.py

**Files:**
- Modify: `backend/app/services/safety_engine.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Add a failing test for tier-based safety scoring**

Append to `backend/app/tests/test_safety_engine.py`:
```python
def test_ssh_tier5_steps_add_risk():
    from app.connectors.catalog_service import init_catalog_service
    import pathlib
    init_catalog_service(pathlib.Path(__file__).parent.parent / "connectors" / "catalog")

    from app.services.safety_engine import adjust_for_execution_plan

    tier5_steps = [
        {"generic_action": "execute_template", "execution_tier": 5, "connector_type": "ssh_mock"}
    ]
    tier1_steps = [
        {"generic_action": "update_dns_record", "execution_tier": 1, "connector_type": "cloudflare_mock"}
    ]

    from app.services.safety_engine import RiskLevel
    from dataclasses import replace as dc_replace

    base_result = score_change_request(
        make_cr(ChangeType.remote_command, {"template_id": "restart_service", "parameters": {}, "rollback_strategy": "manual"}),
        [make_asset()],
    )
    adjusted_tier5 = adjust_for_execution_plan(base_result, tier5_steps)
    adjusted_tier1 = adjust_for_execution_plan(base_result, tier1_steps)
    assert adjusted_tier5.risk_score >= adjusted_tier1.risk_score
```

You'll need to add the import for `make_cr` and `make_asset` — check the top of `test_safety_engine.py` and add if not present:
```python
from app.tests.test_planning_engine import make_cr, make_asset
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd backend && pytest app/tests/test_safety_engine.py::test_ssh_tier5_steps_add_risk -v 2>&1 | tail -10
```
Expected: `ImportError` — `adjust_for_execution_plan` does not exist yet.

- [ ] **Step 3: Add `adjust_for_execution_plan` to `safety_engine.py`**

Append to `backend/app/services/safety_engine.py`:
```python
def adjust_for_execution_plan(
    safety_result: SafetyReviewResult,
    generated_steps: list[dict],
) -> SafetyReviewResult:
    """Adjusts risk score upward if any step uses execution tier 5 (raw remote command)."""
    from dataclasses import replace
    max_tier = max((s.get("execution_tier", 1) for s in generated_steps), default=1)
    if max_tier < 5:
        return safety_result

    new_factors = list(safety_result.risk_factors) + [
        RiskFactor(
            name="raw_remote_command_tier",
            description="One or more steps use raw remote command execution (tier 5)",
            score=25,
        )
    ]
    new_score = safety_result.risk_score + 25
    new_level = _score_to_level(new_score)
    return replace(safety_result, risk_score=new_score, risk_level=new_level, risk_factors=new_factors)


def _score_to_level(score: int) -> "RiskLevel":
    if score >= 90:
        return RiskLevel.critical
    if score >= 60:
        return RiskLevel.high
    if score >= 30:
        return RiskLevel.medium
    return RiskLevel.low
```

- [ ] **Step 4: Run test**

```bash
cd backend && pytest app/tests/test_safety_engine.py::test_ssh_tier5_steps_add_risk -v
```
Expected: PASSED.

- [ ] **Step 5: Initialize catalog in `main.py`**

In `backend/app/main.py`, find the startup section (the `@app.on_event("startup")` handler or `lifespan` context). Add catalog initialization:

```python
from app.connectors.catalog_service import init_catalog_service
import pathlib

# Inside startup handler or at module level after app creation:
CATALOG_DIR = pathlib.Path(__file__).parent / "connectors" / "catalog"
init_catalog_service(CATALOG_DIR)
```

If there is no startup handler, add one before the router registrations:
```python
@app.on_event("startup")
async def startup_event():
    CATALOG_DIR = pathlib.Path(__file__).parent / "connectors" / "catalog"
    init_catalog_service(CATALOG_DIR)
```

- [ ] **Step 6: Run full test suite**

```bash
cd backend && pytest app/tests/ -v
```
Expected: all tests PASSED.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/safety_engine.py backend/app/main.py
git commit -m "feat: add execution_tier safety adjustment and catalog startup init"
```

---

### Task 15: Final integration smoke test

- [ ] **Step 1: Run the complete test suite one final time**

```bash
cd backend && pytest app/tests/ -v --tb=short
```
Expected: all tests PASSED. Note the final count.

- [ ] **Step 2: Verify the docker compose stack starts**

```bash
docker compose up --build -d && sleep 10 && curl -s http://localhost:8000/docs | grep -o "Nexplane" | head -1
```
Expected: `Nexplane`

```bash
docker compose down
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "test: confirm full test suite passes after catalog refactor"
```

---

## What's Next

Plan 2 (Project Entity + API) covers:
- `Project` and `ProjectChangeRequest` DB models + Alembic migration
- Project schemas, router, CRUD endpoints
- Project-level AI-assisted plan generation endpoint
- Frontend project list and detail pages
