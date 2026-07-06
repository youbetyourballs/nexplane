# GCP Phases S, T, U Design

## Overview

Implement three GCP smoke phases that exercise advanced firewall rules, GCS bucket lifecycle, and service account + IAM binding management. These phases complete the GCP smoke coverage gap in `test_gcp_live.py`.

## Phases

### Phase S — Advanced Firewall Rules

**Goal:** Smoke test multi-rule firewall scenarios: different priorities, protocols, and target tags.

**Executors:** Reuses existing `gcp_firewall_create` and `gcp_firewall_delete`. No new executors needed.

**Flow:**
1. Create firewall rule 1 — TCP port 8080, priority 900, target tag `nexplane-smoke-web`
2. Create firewall rule 2 — UDP port 5353, priority 800, target tag `nexplane-smoke-dns`
3. Verify both rules via `compute_v1` SDK (`firewalls().get()`)
4. Rollback stack (LIFO): delete rule 2, delete rule 1

**Change types used:** `gcp_firewall_create` (×2), `gcp_firewall_delete` (×2) — all exist.

**Catalog entries:** None needed (existing catalog entries cover these).

---

### Phase T — GCS Bucket Lifecycle

**Goal:** Smoke test bucket creation and public-access blocking.

**New executors:**
- `gcp_bucket_create` — creates a GCS bucket in a specified region; returns `{"bucket_name": ..., "location": ...}`
- `gcp_bucket_delete` — deletes a GCS bucket; idempotent (no error if already gone)

**Existing executor reused:** `block_public_bucket_access` — already exists for GCS.

**Flow:**
1. `gcp_bucket_create` — region `us-central1`, name `nexplane-smoke-{timestamp}`
2. `block_public_bucket_access` — apply uniform bucket-level access
3. Verify via `storage_v1` SDK: bucket exists, `uniformBucketLevelAccess.enabled == true`
4. Rollback stack (LIFO): delete bucket

**Parameters for `gcp_bucket_create`:** `bucket_name`, `location`, `project_id`
**Parameters for `gcp_bucket_delete`:** `bucket_name`, `project_id`

**Change types added:** `gcp_bucket_create`, `gcp_bucket_delete`

**Catalog entries:** Two new entries in `backend/app/change_type_definitions/`:
- `gcp_bucket_create.json` — fields: `bucket_name`, `location`, `project_id`
- `gcp_bucket_delete.json` — fields: `bucket_name`, `project_id`

---

### Phase U — Service Account + IAM Binding

**Goal:** Smoke test service account lifecycle and project-level IAM role bindings.

**New executors:**
- `gcp_service_account_create` — creates a SA; returns `{"email": ..., "unique_id": ...}`
- `gcp_service_account_delete` — deletes a SA by email; idempotent
- `gcp_iam_binding_add` — adds `member: serviceAccount:{email}` to a project IAM role; returns `{"role": ..., "member": ...}`
- `gcp_iam_binding_remove` — removes the binding; uses read-modify-write on `cloudresourcemanager_v1`

**Flow:**
1. `gcp_service_account_create` — display name `nexplane-smoke`, account ID `nexplane-smoke-{timestamp}`
2. Poll `get_service_account()` for up to 30s (GCP SA eventual consistency)
3. `gcp_iam_binding_add` — bind role `roles/viewer` to the new SA
4. Verify via IAM SDK: SA exists, binding present in project IAM policy
5. Rollback stack (LIFO): `gcp_iam_binding_remove`, `gcp_service_account_delete`

**Parameters for `gcp_service_account_create`:** `account_id`, `display_name`, `project_id`
**Parameters for `gcp_service_account_delete`:** `email`, `project_id`
**Parameters for `gcp_iam_binding_add`:** `project_id`, `role`, `member` (full `serviceAccount:...` form)
**Parameters for `gcp_iam_binding_remove`:** `project_id`, `role`, `member`

**Change types added:** `gcp_service_account_create`, `gcp_service_account_delete`, `gcp_iam_binding_add`, `gcp_iam_binding_remove`

**Catalog entries:** Four new entries in `backend/app/change_type_definitions/`.

---

## Executor Location

All new executors go in `backend/app/executors/gcp/` following the existing file-per-executor pattern.

## Smoke Test Pattern

Each phase follows the established GCP smoke pattern:
```python
async def run_phase_X(client, rollback_stack):
    # create CRs via client.run_cr()
    # push rollback CR IDs onto rollback_stack
    # verify via GCP SDK
    return _phase_result("X", ...)
```

Rollback unwind is LIFO via the shared `rollback_stack` passed through all phases.

## File Map

**New files:**
- `backend/app/executors/gcp/bucket_create.py`
- `backend/app/executors/gcp/bucket_delete.py`
- `backend/app/executors/gcp/service_account_create.py`
- `backend/app/executors/gcp/service_account_delete.py`
- `backend/app/executors/gcp/iam_binding_add.py`
- `backend/app/executors/gcp/iam_binding_remove.py`
- `backend/app/change_type_definitions/gcp_bucket_create.json`
- `backend/app/change_type_definitions/gcp_bucket_delete.json`
- `backend/app/change_type_definitions/gcp_service_account_create.json`
- `backend/app/change_type_definitions/gcp_service_account_delete.json`
- `backend/app/change_type_definitions/gcp_iam_binding_add.json`
- `backend/app/change_type_definitions/gcp_iam_binding_remove.json`

**Modified files:**
- `backend/app/models/change_request.py` — add 6 new ChangeType enum values
- `frontend/src/types/api.ts` — add 6 new ChangeType union values
- `backend/tests/smoke/test_gcp_live.py` — replace phase S, T, U stubs with real implementations
- `backend/app/executors/gcp/__init__.py` — register new executors

## Constraints

- SPDX header on every new/modified file: `# SPDX-License-Identifier: AGPL-3.0-only\n# Copyright (C) 2024-2026 Nexplane, Inc.`
- All smoke phases must run against live GCP infrastructure via platform CR lifecycle (create→plan→approve→execute→rollback)
- No mocks
- GCP SA eventual consistency: poll 30s after create before key/binding operations
- Smoke tests run from EC2 cloud runner, not laptop
