# Catalog Action Rollback Architecture Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the catalog's existing rollback action declarations into the `catalog_action` planning engine, add a `catalog_workflow` change type for multi-step FILO-orchestrated sequences, clean up `_auto_asset` DB records on rollback, and prove the full path with three new smoke phases.

**Architecture:** Four independent concerns addressed together. The catalog JSON already declares `rollback_action` and `rollback_connector_type` on mutating actions — the planning engine just ignores them. Wiring them in is a 4-line fix. `catalog_workflow` generates a normal multi-step `ChangePlanData` from a `desired_outcome.steps` array; the existing execution engine handles sequential execution and FILO unwind without changes. `_auto_asset_id` threads through the execution result so the rollback path can delete orphaned asset records. Smoke uses `aws.create_key_pair` / `aws.delete_key_pair` (already a declared rollback pair in the catalog) as the mutating test action.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x async, boto3, pytest-style smoke runner, httpx

---

## Global Constraints

- `catalog_workflow` is a String-backed enum value (`native_enum=False`) — no DB migration needed, only add to `ChangeType`
- `rollback_connector_type` and `rollback_action_id` are already valid fields in the plan step dict — no schema changes to change_plan model
- Cloud-agnostic: no AWS SDK calls outside the existing executor modules; smoke uses catalog actions not direct boto3
- Smoke follows existing file conventions: `phase_*` naming, `fail()` for assertion failures, `--email`/`--password` CLI args, REST client via httpx
- All new ChangeType values must be added to the enum AND to any frontend ChangeType union types that enumerate them
- SPDX header on every modified file: `# SPDX-License-Identifier: AGPL-3.0-only\n# Copyright (C) 2024-2026 Nexplane, Inc.`
- Key name format for smoke: `"nexplane-smoke-{uuid8}"` where `uuid8` is the first 8 chars of a fresh `uuid.uuid4()`

---

## Section 1: Planning Engine Fix — Single-Step Rollback

`planning_engine.py` currently hardcodes `rollback_connector_type: None` for every `catalog_action` CR. The action def retrieved from the catalog already carries `rollback_action` and `rollback_connector_type` fields for mutating actions.

**Fix** — after validating the action exists, read rollback fields from the def:

```python
if ct == ChangeType.catalog_action:
    connector_type = desired.get("connector_type", "")
    action_id = desired.get("action_id", "")
    params = desired.get("params", {}) or {}
    try:
        action_def = catalog.get_action_def(connector_type, action_id)
    except KeyError:
        raise PlanBlockedError([f"Unknown catalog action: {connector_type}.{action_id}"])

    rollback_action    = action_def.get("rollback_action")
    rollback_ct        = action_def.get("rollback_connector_type")

    step = {
        "step_number": 1,
        "connector_type": connector_type,
        "action_id": action_id,
        "parameters": params,
        "purpose": "execute",
        "options": [{"connector_type": connector_type, "action_id": action_id, "execution_tier": 0}],
        "rollback_connector_type": rollback_ct,
        "rollback_action_id": rollback_action,
    }
    return ChangePlanData(
        generated_steps=[step],
        preflight_checks=[],
        blast_radius=_calculate_blast_radius(change_request, assets, safety_result),
        rollback_plan={},
        verification_plan={},
    )
```

Actions without a declared rollback (read-only discovery actions) get `None` naturally — no behavior change.

---

## Section 2: `catalog_workflow` Change Type

### ChangeType enum

Add `catalog_workflow = "catalog_workflow"` to `ChangeType` in `backend/app/models/change_request.py`.

### desired_outcome schema

```json
{
  "steps": [
    {"connector_type": "aws", "action_id": "create_key_pair", "params": {"key_name": "my-key"}},
    {"connector_type": "aws", "action_id": "create_security_group", "params": {"group_name": "my-sg"}}
  ]
}
```

### Planning engine handler

```python
if ct == ChangeType.catalog_workflow:
    steps_spec = desired.get("steps", [])
    if not steps_spec:
        raise PlanBlockedError(["catalog_workflow requires at least one step"])
    generated_steps = []
    for i, spec in enumerate(steps_spec, start=1):
        conn_t = spec.get("connector_type", "")
        act_id = spec.get("action_id", "")
        try:
            action_def = catalog.get_action_def(conn_t, act_id)
        except KeyError:
            raise PlanBlockedError([f"Unknown catalog action at step {i}: {conn_t}.{act_id}"])
        generated_steps.append({
            "step_number": i,
            "connector_type": conn_t,
            "action_id": act_id,
            "parameters": spec.get("params", {}),
            "purpose": "execute",
            "options": [{"connector_type": conn_t, "action_id": act_id, "execution_tier": 0}],
            "rollback_connector_type": action_def.get("rollback_connector_type"),
            "rollback_action_id": action_def.get("rollback_action"),
        })
    return ChangePlanData(
        generated_steps=generated_steps,
        preflight_checks=[],
        blast_radius=_calculate_blast_radius(change_request, assets, safety_result),
        rollback_plan={},
        verification_plan={},
    )
```

The existing execution engine runs steps sequentially and unwinds in FILO order on failure — no changes to `activities.py` execution path needed.

---

## Section 3: `_auto_asset` Cleanup on Rollback

### Problem

When an executor returns `_auto_asset` in its result, `connector_service.py` creates an asset DB record. The asset ID is never stored where the rollback path can reach it, leaving orphaned asset records when rollback succeeds externally.

### Fix — `connector_service.py`

After upserting the auto asset, include its ID in the returned result:

```python
if "_auto_asset" in result and connector is not None and db is not None:
    asset = await _upsert_auto_asset(result.pop("_auto_asset"), connector.organization_id, db, connector_id=connector.id)
    if asset is not None:
        result["_auto_asset_id"] = str(asset.id)
```

`_upsert_auto_asset` currently returns `None` — change its return type to `Asset | None` and add `return asset` at the end of the function body.

### Fix — `activities.py` rollback path

In the rollback execution path, before calling the rollback executor for a step, check if the step's prior execution result contains `_auto_asset_id` and delete the asset:

```python
prior_result = step.get("execution_result") or {}
auto_asset_id = prior_result.get("_auto_asset_id")
if auto_asset_id:
    await _delete_auto_asset(auto_asset_id, db)
```

### `_delete_auto_asset` helper

Add to `connector_service.py`:

```python
async def _delete_auto_asset(asset_id: str, db: AsyncSession) -> None:
    from app.models.asset import Asset
    import uuid as _uuid
    result = await db.execute(
        select(Asset).where(Asset.id == _uuid.UUID(asset_id))
    )
    asset = result.scalar_one_or_none()
    if asset:
        await db.delete(asset)
        await db.commit()
```

If the rollback executor fails (external infra not cleaned up), the asset record is preserved — correct, since the infra still exists. The two cleanup paths are independent.

---

## Section 4: Smoke Test — Three New Phases

Add to `backend/tests/smoke/test_catalog_action_live.py`. Run order: existing 3 phases first, then these 3.

### Phase: `CATALOG_ROLLBACK`

Single-step mutating catalog_action CR with rollback verified.

```
key_name = f"nexplane-smoke-{uuid4_8()}"

1. POST /change-requests  body={change_type: "catalog_action", desired_outcome: {connector_type: "aws", action_id: "create_key_pair", params: {key_name: key_name}}}
   → assert status=draft

2. POST /change-requests/{id}/plan  → poll until planned

3. GET /change-requests/{id}
   → assert generated_steps[0].rollback_connector_type == "aws"
   → assert generated_steps[0].rollback_action_id == "delete_key_pair"

4. POST /change-requests/{id}/submit-for-approval
5. POST /change-requests/{id}/approve  body={decision: "approved", comment: "smoke"}
6. POST /change-requests/{id}/execute  → poll until completed

7. GET execution result
   → record auto_asset_id = result.get("_auto_asset_id")
   → if auto_asset_id: GET /assets/{auto_asset_id} → assert 200

8. POST /change-requests/{id}/rollback  → poll until rolled_back

9. if auto_asset_id: GET /assets/{auto_asset_id} → assert 404  (asset deleted)

10. assert rollback execution result present
```

### Phase: `CATALOG_WORKFLOW`

Multi-step happy path with FILO rollback.

```
key_a = f"nexplane-smoke-wfa-{uuid4_8()}"
key_b = f"nexplane-smoke-wfb-{uuid4_8()}"

1. POST /change-requests  body={change_type: "catalog_workflow", desired_outcome: {steps: [
     {connector_type: "aws", action_id: "create_key_pair", params: {key_name: key_a}},
     {connector_type: "aws", action_id: "create_key_pair", params: {key_name: key_b}},
   ]}}

2. POST /change-requests/{id}/plan  → poll until planned
   GET /change-requests/{id}
   → assert len(generated_steps) == 2
   → assert both steps have rollback_connector_type="aws", rollback_action_id="delete_key_pair"

3. Approve → execute → poll until completed

4. POST /change-requests/{id}/rollback  → poll until rolled_back

5. GET rollback execution run
   → assert 2 rollback steps executed
   → assert step 2 rollback ran before step 1 rollback (FILO — verify by step_number ordering in rollback run steps)
```

### Phase: `CATALOG_WORKFLOW_PARTIAL_FAILURE`

Step 2 fails (duplicate key name) → step 1 auto-rolled back.

```
key_name = f"nexplane-smoke-pf-{uuid4_8()}"

1. POST /change-requests  body={change_type: "catalog_workflow", desired_outcome: {steps: [
     {connector_type: "aws", action_id: "create_key_pair", params: {key_name: key_name}},
     {connector_type: "aws", action_id: "create_key_pair", params: {key_name: key_name}},  # same name → fails
   ]}}

2. Approve → execute → poll until failed (or execution_failed)

3. GET execution run
   → assert step 1 status=completed
   → assert step 2 status=failed

4. GET /change-requests/{id}
   → assert rollback was auto-triggered for step 1
   → assert step 1 rollback execution result present (delete_key_pair ran)
```

---

## Section 5: Commercial Provisioning Rollback (Deferred — nexplane-deploy)

The onboarding wizard (`create_customer → provision_instance → generate_setup_token`) maps directly onto `catalog_workflow` once core ships. Required changes in the commercial overlay:

| Action | Rollback action | Internal state |
|---|---|---|
| `create_customer` | `delete_customer` | Customer DB row |
| `provision_instance` | `terminate_instance` | Asset record (`_auto_asset_id`) |
| `generate_setup_token` | `revoke_setup_token` | `setup_tokens` row |

The frontend onboard wizard submits one `catalog_workflow` CR with three steps instead of three separate CRs. Prerequisite: fix known-broken provisioning path in `nexplane-deploy` (silently marks instances `active` without verifying `RunInstances` success) before the commercial smoke can pass.

**This section is out of scope for this repo.** Design doc to be filed in `nexplane-deploy` backlog.

---

## File Map

**Backend — modified:**
- `backend/app/models/change_request.py` — add `catalog_workflow` to `ChangeType` enum
- `backend/app/services/planning_engine.py` — wire rollback fields for `catalog_action`; add `catalog_workflow` handler
- `backend/app/services/connector_service.py` — thread `_auto_asset_id` through result; add `_delete_auto_asset`
- `backend/app/workflows/activities.py` — call `_delete_auto_asset` in rollback path when `_auto_asset_id` present

**Smoke — modified:**
- `backend/tests/smoke/test_catalog_action_live.py` — add `CATALOG_ROLLBACK`, `CATALOG_WORKFLOW`, `CATALOG_WORKFLOW_PARTIAL_FAILURE` phases

**Frontend — modified (minimal):**
- `frontend/src/api/endpoints.ts` or `frontend/src/types/api.ts` — add `"catalog_workflow"` to ChangeType union if one exists
