# AWS EC2 Instance Lifecycle Change Types — Design Spec

**Date:** 2026-05-02
**Status:** Approved
**Scope:** Six new change request types covering EC2 instance lifecycle operations (stop, start, reboot, stop/start, launch, terminate), wired to the AWS connector via eight new executor files.

---

## Background

Nexplane has seven change types today. None cover EC2 instance lifecycle. The AWS connector already has credential support, a working boto3 client, and security group executors — but operators cannot yet raise a governed change request to stop, start, reboot, launch, or terminate an instance. This spec adds those types using the existing change type definition pattern.

The `security_group_update` change type already works with the AWS connector and requires no changes.

---

## Design Decisions

- **One JSON definition per operation** — matches the existing pattern; keeps blast radius, rollback strategy, and step sequence explicit per change type.
- **`ec2_launch` handles three modes** — quick (free-tier defaults), clone (copy existing instance config), spec (full parameters). Mode is a field in `desired_outcome`; the executor handles branching, not the planning engine.
- **`wait_instance_state` as a verify step** — polls `describe_instances` until the target state is reached. Reused across stop, start, stop/start, and launch flows.
- **`ec2_terminate` requires `confirm_terminate: true`** in desired_outcome — executor gate, not a UI gate, so the safety check travels with the change request regardless of UI.
- **No changes to planning engine step resolution logic** — only new entries in the resolver dict and rollback strategy map.

---

## Section 1: Change Type Definitions

Six new JSON files in `backend/app/connectors/change_type_definitions/`:

### `ec2_stop.json`

```json
{
  "change_type": "ec2_stop",
  "display_name": "Stop EC2 Instance",
  "steps": [
    {"generic_action": "capture_instance_state", "purpose": "preflight_capture",  "required": true},
    {"generic_action": "create_snapshot",        "purpose": "preflight_capture",  "required": true},
    {"generic_action": "stop_instance",          "purpose": "execute",            "required": true},
    {"generic_action": "wait_instance_state",    "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["api_check"]
}
```

Rollback: start the instance using the `instance_id` captured in preflight.

### `ec2_start.json`

```json
{
  "change_type": "ec2_start",
  "display_name": "Start EC2 Instance",
  "steps": [
    {"generic_action": "capture_instance_state", "purpose": "preflight_capture", "required": true},
    {"generic_action": "start_instance",         "purpose": "execute",           "required": true},
    {"generic_action": "wait_instance_state",    "purpose": "verify",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["api_check"]
}
```

Rollback: stop the instance.

### `ec2_reboot.json`

```json
{
  "change_type": "ec2_reboot",
  "display_name": "Reboot EC2 Instance",
  "steps": [
    {"generic_action": "capture_instance_state", "purpose": "preflight_capture", "required": true},
    {"generic_action": "reboot_instance",        "purpose": "execute",           "required": true},
    {"generic_action": "wait_instance_state",    "purpose": "verify",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["api_check"]
}
```

Rollback: none needed — a soft reboot is self-contained.

### `ec2_stop_start.json`

```json
{
  "change_type": "ec2_stop_start",
  "display_name": "Restart EC2 Instance (Stop/Start)",
  "steps": [
    {"generic_action": "capture_instance_state", "purpose": "preflight_capture", "required": true},
    {"generic_action": "stop_instance",          "purpose": "execute",           "required": true},
    {"generic_action": "wait_instance_state",    "purpose": "verify",            "required": true},
    {"generic_action": "start_instance",         "purpose": "execute",           "required": true},
    {"generic_action": "wait_instance_state",    "purpose": "verify",            "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["api_check"]
}
```

Rollback: stop if instance reached running, manual otherwise. Note: instance may receive a new public IP after stop/start if not using an Elastic IP.

### `ec2_launch.json`

```json
{
  "change_type": "ec2_launch",
  "display_name": "Launch EC2 Instance",
  "steps": [
    {"generic_action": "resolve_launch_config", "purpose": "preflight_validate", "required": true},
    {"generic_action": "launch_instance",       "purpose": "execute",            "required": true},
    {"generic_action": "wait_instance_state",   "purpose": "verify",             "required": true}
  ],
  "preflight_checks": ["connector_reachable", "no_concurrent_changes"],
  "verification_methods": ["api_check"]
}
```

Rollback: terminate the launched instance using the `instance_id` returned in the execution result.

**Launch modes** (set via `mode` in `desired_outcome`):

| Mode | Required fields | Behaviour |
|------|-----------------|-----------|
| `quick` | `name`, `os` (`amazon_linux` or `ubuntu`) | t2.micro, latest free-tier eligible AMI, default VPC, default security group |
| `clone` | `source_instance_id`, `name` | Reads AMI, instance type, subnet, and security groups from the source instance |
| `spec` | `ami_id`, `instance_type`, `subnet_id`, `security_group_ids`, `name` | Full specification, operator-provided |

### `ec2_terminate.json`

```json
{
  "change_type": "ec2_terminate",
  "display_name": "Terminate EC2 Instance",
  "steps": [
    {"generic_action": "capture_instance_state", "purpose": "preflight_capture", "required": true},
    {"generic_action": "create_snapshot",        "purpose": "preflight_capture", "required": true},
    {"generic_action": "terminate_instance",     "purpose": "execute",           "required": true}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["api_check"]
}
```

Rollback: none — termination is irreversible. The mandatory pre-snapshot is the safety net. The executor gates on `confirm_terminate: true` in parameters.

---

## Section 2: New AWS Executor Files

Eight new files in `backend/app/connectors/executors/aws/`. All follow real-if-credentials/mock-if-not pattern. `_client.py` already provides `get_boto3_client(creds, service)`.

New catalog entries added to `backend/app/connectors/catalog/aws.json` for each.

### `capture_instance_state.py`
- **generic_action:** `capture_instance_state`
- **action_type:** `change` (read-only semantically, but gated as change to require approval)
- **execution_tier:** 1
- Calls `ec2.describe_instances(InstanceIds=[instance_id])`
- Returns: `instance_id`, `state`, `public_ip`, `private_ip`, `ami_id`, `instance_type`, `security_groups`, `subnet_id`
- Mock returns a plausible stopped/running instance dict
- Rollback: no-op

### `stop_instance.py`
- **generic_action:** `stop_instance`
- **action_type:** `change`
- **execution_tier:** 2
- Calls `ec2.stop_instances(InstanceIds=[instance_id])`
- Returns: `instance_id`, `previous_state`, `current_state`
- Rollback: delegates to `start_instance.execute`

### `start_instance.py`
- **generic_action:** `start_instance`
- **action_type:** `change`
- **execution_tier:** 2
- Calls `ec2.start_instances(InstanceIds=[instance_id])`
- Returns: `instance_id`, `previous_state`, `current_state`
- Rollback: delegates to `stop_instance.execute`

### `reboot_instance.py`
- **generic_action:** `reboot_instance`
- **action_type:** `change`
- **execution_tier:** 2
- Calls `ec2.reboot_instances(InstanceIds=[instance_id])`
- Returns: `instance_id`, `rebooted: true`
- Rollback: no-op

### `wait_instance_state.py`
- **generic_action:** `wait_instance_state`
- **action_type:** `change` (verify step)
- **execution_tier:** 1
- Parameters: `instance_id`, `target_state` (`running` or `stopped`)
- Polls `ec2.describe_instances` up to 20 times with 15s sleep between polls (5 min total)
- Returns: `instance_id`, `reached_state`, `elapsed_seconds`
- Raises on timeout
- Mock returns immediately with `reached_state = target_state`
- Rollback: no-op

### `resolve_launch_config.py`
- **generic_action:** `resolve_launch_config`
- **action_type:** `change` (preflight)
- **execution_tier:** 1
- Reads `mode` from parameters; branches:
  - **quick:** calls `ec2.describe_images` to find latest Amazon Linux 2 or Ubuntu 22.04 LTS free-tier AMI for the configured region; picks t2.micro; calls `ec2.describe_vpcs(Filters=[{Name: "isDefault", Values: ["true"]}])` for default VPC + subnet; uses default security group
  - **clone:** calls `ec2.describe_instances(InstanceIds=[source_instance_id])` and extracts AMI, instance type, subnet, security groups
  - **spec:** validates that all required fields are present; returns them as-is
- Returns a resolved config dict that `launch_instance` consumes: `ami_id`, `instance_type`, `subnet_id`, `security_group_ids`, `name`
- Mock returns a plausible t2.micro config
- Rollback: no-op

### `launch_instance.py`
- **generic_action:** `launch_instance`
- **action_type:** `change`
- **execution_tier:** 2
- Expects resolved config from `resolve_launch_config` in parameters
- Calls `ec2.run_instances(ImageId, InstanceType, SubnetId, SecurityGroupIds, MinCount=1, MaxCount=1, TagSpecifications=[{Name: name}])`
- Returns: `instance_id`, `state`, `private_ip`
- Rollback: calls `terminate_instance.execute` with the returned `instance_id`

### `terminate_instance.py`
- **generic_action:** `terminate_instance`
- **action_type:** `change`
- **execution_tier:** 3
- Gates on `parameters.get("confirm_terminate") == True` — returns error dict if not set
- Calls `ec2.terminate_instances(InstanceIds=[instance_id])`
- Returns: `instance_id`, `previous_state`, `current_state: "shutting-down"`
- Rollback: no-op (irreversible)

---

## Section 3: Backend Wiring

### Migration 014

File: `backend/alembic/versions/014_add_ec2_change_types.py`

```sql
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ec2_stop';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ec2_start';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ec2_reboot';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ec2_stop_start';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ec2_launch';
ALTER TYPE change_type ADD VALUE IF NOT EXISTS 'ec2_terminate';
```

### ChangeType Enum

File: `backend/app/models/change_request.py` — add 6 values to `ChangeType`.

### Planning Engine

File: `backend/app/services/planning_engine.py`

**New resolver entries** (added to the `resolvers` dict):

```python
"capture_instance_state": lambda d: {"instance_id": d.get("instance_id")},
"stop_instance":          lambda d: {"instance_id": d.get("instance_id")},
"start_instance":         lambda d: {"instance_id": d.get("instance_id")},
"reboot_instance":        lambda d: {"instance_id": d.get("instance_id")},
"wait_instance_state":    lambda d: {"instance_id": d.get("instance_id"), "target_state": d.get("target_state", "running")},
"resolve_launch_config":  lambda d: {"mode": d.get("mode", "quick"), "source_instance_id": d.get("source_instance_id"), "ami_id": d.get("ami_id"), "instance_type": d.get("instance_type"), "subnet_id": d.get("subnet_id"), "security_group_ids": d.get("security_group_ids"), "name": d.get("name"), "os": d.get("os", "amazon_linux")},
"launch_instance":        lambda d: {"ami_id": d.get("ami_id"), "instance_type": d.get("instance_type"), "subnet_id": d.get("subnet_id"), "security_group_ids": d.get("security_group_ids"), "name": d.get("name")},
"terminate_instance":     lambda d: {"instance_id": d.get("instance_id"), "confirm_terminate": d.get("confirm_terminate", False)},
```

**New rollback strategy entries:**

```python
"ec2_stop":       {"strategy": "start_instance",     "automatic": True},
"ec2_start":      {"strategy": "stop_instance",      "automatic": True},
"ec2_reboot":     {"strategy": "none",               "automatic": False},
"ec2_stop_start": {"strategy": "stop_if_running",    "automatic": True},
"ec2_launch":     {"strategy": "terminate_instance", "automatic": True},
"ec2_terminate":  {"strategy": "manual",             "automatic": False},
```

---

## Section 4: Frontend

### `frontend/src/types/api.ts`

Add to `ChangeType` union:
```typescript
| 'ec2_stop' | 'ec2_start' | 'ec2_reboot' | 'ec2_stop_start' | 'ec2_launch' | 'ec2_terminate'
```

### `frontend/src/pages/CreateChangeRequest.tsx`

Add to `CHANGE_TYPE_META`:

```typescript
ec2_stop: {
  label: "Stop EC2 Instance",
  description: "Gracefully stop a running EC2 instance. Takes an EBS snapshot first.",
  outcomeTemplate: { instance_id: "i-0123456789abcdef0" },
},
ec2_start: {
  label: "Start EC2 Instance",
  description: "Start a stopped EC2 instance.",
  outcomeTemplate: { instance_id: "i-0123456789abcdef0" },
},
ec2_reboot: {
  label: "Reboot EC2 Instance",
  description: "Soft reboot. Instance stays on the same host and keeps its public IP.",
  outcomeTemplate: { instance_id: "i-0123456789abcdef0" },
},
ec2_stop_start: {
  label: "Restart EC2 Instance",
  description: "Full power cycle (stop then start). Instance may get a new public IP.",
  outcomeTemplate: { instance_id: "i-0123456789abcdef0" },
},
ec2_launch: {
  label: "Launch EC2 Instance",
  description: "Launch a new EC2 instance. Modes: quick (free-tier defaults), clone, or spec.",
  outcomeTemplate: {
    mode: "quick",
    name: "my-new-instance",
    os: "amazon_linux",
  },
},
ec2_terminate: {
  label: "Terminate EC2 Instance",
  description: "Permanently terminate an EC2 instance. Takes a snapshot first. Irreversible.",
  outcomeTemplate: {
    instance_id: "i-0123456789abcdef0",
    confirm_terminate: true,
  },
},
```

---

## Files Created / Modified

| File | Change |
|------|--------|
| `backend/alembic/versions/014_add_ec2_change_types.py` | New migration |
| `backend/app/models/change_request.py` | Add 6 ChangeType values |
| `backend/app/connectors/change_type_definitions/ec2_stop.json` | New |
| `backend/app/connectors/change_type_definitions/ec2_start.json` | New |
| `backend/app/connectors/change_type_definitions/ec2_reboot.json` | New |
| `backend/app/connectors/change_type_definitions/ec2_stop_start.json` | New |
| `backend/app/connectors/change_type_definitions/ec2_launch.json` | New |
| `backend/app/connectors/change_type_definitions/ec2_terminate.json` | New |
| `backend/app/connectors/catalog/aws.json` | Add 8 new action entries |
| `backend/app/connectors/executors/aws/capture_instance_state.py` | New |
| `backend/app/connectors/executors/aws/stop_instance.py` | New |
| `backend/app/connectors/executors/aws/start_instance.py` | New |
| `backend/app/connectors/executors/aws/reboot_instance.py` | New |
| `backend/app/connectors/executors/aws/wait_instance_state.py` | New |
| `backend/app/connectors/executors/aws/resolve_launch_config.py` | New |
| `backend/app/connectors/executors/aws/launch_instance.py` | New |
| `backend/app/connectors/executors/aws/terminate_instance.py` | New |
| `backend/app/services/planning_engine.py` | Add resolvers + rollback strategies |
| `frontend/src/types/api.ts` | Add 6 ChangeType values |
| `frontend/src/pages/CreateChangeRequest.tsx` | Add 6 CHANGE_TYPE_META entries |
