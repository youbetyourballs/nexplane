# GCP Phases V, W, X Design

## Overview

Implement three GCP smoke phases covering Cloud DNS, Cloud SQL, and Cloud Monitoring. These are entirely new executor domains not previously touched by the platform.

## Phases

### Phase V — Cloud DNS

**Goal:** Smoke test managed zone and DNS record lifecycle.

**New executors:**
- `gcp_dns_zone_create` — creates a Cloud DNS managed zone; returns `{"zone_name": ..., "dns_name": ...}`
- `gcp_dns_zone_delete` — deletes a managed zone (must be empty); idempotent
- `gcp_dns_record_create` — creates a resource record set in a zone; returns `{"name": ..., "type": ..., "ttl": ...}`
- `gcp_dns_record_delete` — deletes a resource record set

**Flow:**
1. `gcp_dns_zone_create` — zone name `nexplane-smoke-{timestamp}`, DNS name `nexplane-smoke-{timestamp}.example.`
2. `gcp_dns_record_create` — A record, name `test.nexplane-smoke-{timestamp}.example.`, TTL 300, rrdatas `["1.2.3.4"]`
3. Verify via `dns_v1` SDK: zone exists (`managedZones().get()`), record set present (`resourceRecordSets().list()`)
4. Rollback stack (LIFO): delete record, delete zone

**Parameters for `gcp_dns_zone_create`:** `zone_name`, `dns_name`, `project_id`, `description`
**Parameters for `gcp_dns_zone_delete`:** `zone_name`, `project_id`
**Parameters for `gcp_dns_record_create`:** `zone_name`, `record_name`, `record_type`, `ttl`, `rrdatas` (list), `project_id`
**Parameters for `gcp_dns_record_delete`:** `zone_name`, `record_name`, `record_type`, `project_id`

**Change types added:** `gcp_dns_zone_create`, `gcp_dns_zone_delete`, `gcp_dns_record_create`, `gcp_dns_record_delete`

---

### Phase W — Cloud SQL

**Goal:** Smoke test Cloud SQL instance provisioning and backup.

**New executors:**
- `gcp_cloudsql_instance_create` — creates a Cloud SQL instance; polls `operations().get()` until DONE; returns `{"instance_name": ..., "connection_name": ...}`
- `gcp_cloudsql_instance_delete` — deletes a Cloud SQL instance; polls until DONE
- `gcp_cloudsql_backup_create` — triggers an on-demand backup of a Cloud SQL instance; polls until complete; returns `{"backup_run_id": ...}`

**Note:** Cloud SQL instance creation takes 5–10 minutes. The phase sets a 900s timeout. Backup has no separate rollback executor — the backup is the verification artifact; rollback deletes the instance which cascades.

**Flow:**
1. `gcp_cloudsql_instance_create` — tier `db-f1-micro`, database version `POSTGRES_14`, region `us-central1`
2. `gcp_cloudsql_backup_create` — trigger backup on the new instance
3. Verify via `sqladmin_v1beta4` SDK: instance in `RUNNABLE` state, backup run in `SUCCESSFUL` state
4. Rollback stack (LIFO): `gcp_cloudsql_instance_delete` (backup is automatically cleaned up with instance)

**Parameters for `gcp_cloudsql_instance_create`:** `instance_name`, `database_version`, `tier`, `region`, `project_id`
**Parameters for `gcp_cloudsql_instance_delete`:** `instance_name`, `project_id`
**Parameters for `gcp_cloudsql_backup_create`:** `instance_name`, `project_id`

**Change types added:** `gcp_cloudsql_instance_create`, `gcp_cloudsql_instance_delete`, `gcp_cloudsql_backup_create`

**Smoke test timeout:** Phase W uses `phase_timeout=900` in the smoke runner config.

---

### Phase X — Cloud Monitoring

**Goal:** Smoke test alert policy and uptime check lifecycle.

**New executors:**
- `gcp_alert_policy_create` — creates a Cloud Monitoring alert policy with a CPU utilization condition; returns `{"policy_name": ..., "display_name": ...}`
- `gcp_alert_policy_delete` — deletes an alert policy by resource name
- `gcp_uptime_check_create` — creates an uptime check targeting a public HTTP endpoint; returns `{"check_id": ..., "display_name": ...}`
- `gcp_uptime_check_delete` — deletes an uptime check by resource name

**Flow:**
1. `gcp_alert_policy_create` — display name `nexplane-smoke-alert-{timestamp}`, condition: CPU utilization > 0.9 for 60s on any GCE instance
2. `gcp_uptime_check_create` — display name `nexplane-smoke-uptime-{timestamp}`, target host `google.com`, path `/`, period `60s`
3. Verify via `monitoring_v3` SDK: policy found in `alert_policies().list()`, uptime check found in `uptime_check_configs().list()`
4. Rollback stack (LIFO): delete uptime check, delete alert policy

**Parameters for `gcp_alert_policy_create`:** `display_name`, `project_id`, `condition_threshold` (float, 0–1), `duration_seconds`
**Parameters for `gcp_alert_policy_delete`:** `policy_name` (full resource name), `project_id`
**Parameters for `gcp_uptime_check_create`:** `display_name`, `project_id`, `host`, `path`, `period_seconds`
**Parameters for `gcp_uptime_check_delete`:** `check_id` (full resource name), `project_id`

**Change types added:** `gcp_alert_policy_create`, `gcp_alert_policy_delete`, `gcp_uptime_check_create`, `gcp_uptime_check_delete`

---

## Executor Location

All new executors go in `backend/app/executors/gcp/` following the existing file-per-executor pattern.

## File Map

**New executor files:**
- `backend/app/executors/gcp/dns_zone_create.py`
- `backend/app/executors/gcp/dns_zone_delete.py`
- `backend/app/executors/gcp/dns_record_create.py`
- `backend/app/executors/gcp/dns_record_delete.py`
- `backend/app/executors/gcp/cloudsql_instance_create.py`
- `backend/app/executors/gcp/cloudsql_instance_delete.py`
- `backend/app/executors/gcp/cloudsql_backup_create.py`
- `backend/app/executors/gcp/alert_policy_create.py`
- `backend/app/executors/gcp/alert_policy_delete.py`
- `backend/app/executors/gcp/uptime_check_create.py`
- `backend/app/executors/gcp/uptime_check_delete.py`

**New catalog entries:**
- `backend/app/change_type_definitions/gcp_dns_zone_create.json`
- `backend/app/change_type_definitions/gcp_dns_zone_delete.json`
- `backend/app/change_type_definitions/gcp_dns_record_create.json`
- `backend/app/change_type_definitions/gcp_dns_record_delete.json`
- `backend/app/change_type_definitions/gcp_cloudsql_instance_create.json`
- `backend/app/change_type_definitions/gcp_cloudsql_instance_delete.json`
- `backend/app/change_type_definitions/gcp_cloudsql_backup_create.json`
- `backend/app/change_type_definitions/gcp_alert_policy_create.json`
- `backend/app/change_type_definitions/gcp_alert_policy_delete.json`
- `backend/app/change_type_definitions/gcp_uptime_check_create.json`
- `backend/app/change_type_definitions/gcp_uptime_check_delete.json`

**Modified files:**
- `backend/app/models/change_request.py` — add 11 new ChangeType enum values
- `frontend/src/types/api.ts` — add 11 new ChangeType union values
- `backend/tests/smoke/test_gcp_live.py` — replace phase V, W, X stubs with real implementations
- `backend/app/executors/gcp/__init__.py` — register new executors

## Constraints

- SPDX header on every new/modified file: `# SPDX-License-Identifier: AGPL-3.0-only\n# Copyright (C) 2024-2026 Nexplane, Inc.`
- All smoke phases must run against live GCP infrastructure via platform CR lifecycle
- No mocks
- Cloud SQL phase timeout: 900s
- Smoke tests run from EC2 cloud runner, not laptop
- Rollback is LIFO via shared rollback_stack across all phases
