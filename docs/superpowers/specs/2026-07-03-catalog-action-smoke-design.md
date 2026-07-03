# Catalog Action Smoke & Commercial Load Gate Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Live-verify the `catalog_action` CR lifecycle against real infrastructure and enforce that commercial mutating actions cannot load without passing smoke verification.

**Architecture:** Two independent deliverables. A new smoke file (`test_catalog_action_live.py`) with 3 phases proves the planning engine's `catalog_action` branch works end-to-end. A load-time gate in `catalog_service.py` enforces `"smoke_verified": true` on commercial domain non-read-only actions, preventing unverified commercial executors from reaching the API or UI.

**Tech Stack:** Python 3.12, asyncio, SQLAlchemy 2.x async, httpx, FastAPI, boto3 (EC2 discover), pytest-style smoke runner pattern

---

## Global Constraints

- All assertions run against live EC2 infrastructure — no mocks, no stubs
- Smoke follows existing file conventions: `phase_*` naming, `fail()` for assertion failures, `--email`/`--password` CLI args, REST client pattern from `test_aws_live.py`
- Load gate applies only to `domain=commercial` + `read_only=false` actions — core domain actions are exempt
- Gate behavior is hard exclusion (action skipped + startup error logged), not a soft warning
- The `catalog_action` exhaustive write path (`tag_resource`) is deferred to a follow-on task after the provisioning smoke passes — land the read-only lifecycle smoke first
- Skipped commercial actions do not appear in `/catalog/actions` — no new frontend field needed

---

## Deliverable 1: CATALOG_ACTION_SMOKE

**File:** `backend/tests/smoke/test_catalog_action_live.py`

Same structure as `test_aws_live.py`: argparse `--email`/`--password`/`--phases`, REST client via `httpx`, phase functions registered in `ALL_PHASES`.

### Phase CATALOG_DISCOVERY

Formalizes the read path already partially verified live.

**Assertions:**
1. `GET /capabilities` → response has `edition` (str) and `domains` (list); no exception
2. `GET /catalog/actions` → non-empty list; each entry has keys: `connector_type`, `action_id`, `read_only`, `destructive`, `domain`, `display_name`, `description`
3. `GET /catalog/actions?domain=core` → subset of full list; all entries have `domain == "core"`

---

### Phase CATALOG_CR_LIFECYCLE

Proves the planning engine's `catalog_action` branch end-to-end using `aws.discover_ec2_instances` — read-only, zero blast radius, no state change, no rollback needed.

**Sequence:**

1. `POST /change-requests` body:
   ```json
   {
     "change_type": "catalog_action",
     "title": "smoke-catalog-discover-ec2",
     "desired_outcome": {
       "connector_type": "aws",
       "action_id": "discover_ec2_instances",
       "params": {}
     },
     "target_asset_ids": []
   }
   ```
   Assert: `201`, response has `id` and `status`

2. `POST /change-requests/{id}/plan`
   Assert: status becomes `planned`; `plan.steps` has exactly 1 step with `connector_type=aws`, `action_id=discover_ec2_instances`

3. Approval: `POST /change-requests/{id}/approve` (self-approve, same token — allowed for admin)

4. `POST /change-requests/{id}/execute`
   Assert: status becomes `executed`; `execution_result` is present; result contains a list (may be empty if no EC2 instances visible, but key must exist)

5. DB cross-check: query `ChangeRequest` by id directly → assert `change_type == "catalog_action"`, `status == "executed"`

**Cleanup:** Leave the CR in place (read-only action, no state to clean up).

---

### Phase CATALOG_ACTION_ERRORS

Verifies the planning engine rejects invalid `catalog_action` CRs.

**Assertions:**

1. Create CR with `desired_outcome = {connector_type: "aws", action_id: "does_not_exist_xyz", params: {}}` → `POST /change-requests/{id}/plan` returns 400/422 or CR status becomes `plan_blocked`; error message references the unknown action

2. Create CR with `desired_outcome = {}` (missing `connector_type` and `action_id`) → plan returns 400/422 or `plan_blocked`; does not 500

---

## Deliverable 2: Commercial Load Gate

**File modified:** `backend/app/connectors/catalog_service.py`

### Contract

For commercial overlay catalog JSONs, each action definition with `read_only=false` must include:
```json
{
  "action_id": "provision_instance",
  "read_only": false,
  "smoke_verified": true,
  ...
}
```

Actions where `smoke_verified` is absent or `false` are excluded from `_catalog` and `_generic_index` at load time.

### Gate Logic

In `catalog_service.py`, during `_load()` (or the commercial overlay merge loop), for each action:

```python
if action.get("domain") == "commercial" and not action.get("read_only", True):
    if not action.get("smoke_verified", False):
        logger.error(
            "Commercial mutating action %s.%s is not smoke-verified — skipping",
            connector_type, action.get("action_id", "?"),
        )
        continue  # exclude from catalog
```

### Scope

- `domain=commercial` + `read_only=false`: gate applies
- `domain=core`: exempt (covered by per-connector smoke suites)
- `read_only=true` (any domain): exempt (zero blast radius)
- `smoke_verified=true` + `domain=commercial` + `read_only=false`: loaded normally

### Behavior on nexplane-ops today

The nexplane-deploy overlay's mutating commercial actions (`provision_instance`, `terminate_instance`, `generate_setup_token`, `create_customer`) will be skipped at startup — which is correct, since they are not verified. The fix path: pass the provisioning smoke, then add `"smoke_verified": true` to those action definitions in the overlay JSON. No core code change required after this gate is merged.

---

## Run Command

```bash
docker compose exec -T backend python tests/smoke/test_catalog_action_live.py \
    --email admin@acme.example --password admin123 \
    --phases CATALOG_DISCOVERY,CATALOG_CR_LIFECYCLE,CATALOG_ACTION_ERRORS
```

---

## Follow-on (out of scope for this task)

- **CATALOG_ACTION_WRITE phase** — exhaustive `tag_resource` lifecycle (create → plan → execute → verify tag in AWS → cleanup via second CR). Schedule after the provisioning smoke verifies the write path pattern.
- **Provisioning smoke** — `create_customer → provision_instance → generate_setup_token → terminate_instance` + rollback against real infra. Blocked on nexplane-deploy §2 fix (silent `active` on RunInstances returning).
