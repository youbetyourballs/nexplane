# Design: Projects, Action Catalog & Multi-Connector Resolution

**Date:** 2026-04-25  
**Status:** Approved  
**Scope:** New Project entity, per-connector JSON action catalog, catalog-driven planning engine, multi-connector resolution with execution tier ranking, executor module pattern for workflow activities.

---

## Problem Statement

The current system treats each `ChangeRequest` as a standalone atomic change with hardcoded connector logic inside `planning_engine.py` and `connector_service.py`. This creates three gaps:

1. **No project-level coordination** — complex, multi-step initiatives (e.g., microsegmentation: change IPs, update DNS, modify firewall policies, scan for hardcoded values) have no grouping, sequencing, or shared context.
2. **Actions are connector-specific** — a step like "update DNS record" is hardcoded to Cloudflare. There is no concept that the same logical action could be accomplished via multiple connectors.
3. **Connectors are not extensible without code changes** — adding a new connector or action requires modifying Python logic throughout the planning and execution layers.

---

## Out of Scope

- Ingest execution pipeline (scheduling, running discovery/scan connectors, storing results into assets). The action catalog is designed to *describe* ingest actions so the model is future-ready, but no ingest execution is implemented in this spec.
- Real (non-mock) connector implementations.
- Nexplane agent (execution tier 3 — future).

---

## Section 1: Project Entity

`Project` is a new first-class entity that groups and sequences `ChangeRequest`s toward a shared goal. It is a planning and tracking container — each member `ChangeRequest` still goes through its own independent approval and execution workflow.

### New DB Models

**`Project`**

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID PK | |
| `organization_id` | UUID FK | |
| `created_by` | UUID FK → User | |
| `name` | String 500 | |
| `description` | Text | |
| `goal` | Text | Human-readable objective; also the AI prompt seed for AI-assisted generation |
| `status` | Enum | `draft \| planning \| in_progress \| completed \| cancelled` |
| `generation_method` | Enum | `manual \| ai_assisted` |
| `ai_context` | JSON | Stores goal/params used if AI-generated, for auditability |
| `created_at` | DateTime | |
| `updated_at` | DateTime | |

**`ProjectChangeRequest`** (join table with ordering)

| Field | Type | Notes |
|-------|------|-------|
| `project_id` | UUID FK | |
| `change_request_id` | UUID FK | |
| `sequence_order` | Integer | Execution order within the project |
| `depends_on` | JSON | List of `change_request_id`s that must complete before this one starts |

### Project Status Derivation

Project `status` is manually set (draft → planning → in_progress → completed/cancelled). Progress is *derived* from member change request statuses and displayed in the UI — the project itself has no single approval gate.

### Assembly Paths

Two valid paths to build a project's change request list:

- **Manual** — operator creates and adds change requests one by one
- **AI-assisted** — operator provides a `goal`, AI enumerates affected assets and generates the full set of change requests with pre-populated plans. Human reviews and edits before any submission.

---

## Section 2: Action Catalog

### File Layout

```
backend/app/connectors/
  catalog/
    aws.json
    azure.json
    cloudflare.json
    okta.json
    paloalto.json
    ssh.json
  change_type_definitions/
    dns_update.json
    snapshot_asset.json
    security_group_update.json
    key_rotation.json
    telemetry_agent_deploy.json
    remote_command.json
    microsegmentation_policy.json
  executors/
    aws_mock/
      __init__.py
      update_ip_address.py
      create_snapshot.py
      ...
    cloudflare_mock/
      __init__.py
      update_dns_record.py
      restore_dns_record.py
      ...
    paloalto_mock/
      __init__.py
      stage_microseg_policy.py
      ...
    ssh_mock/
      __init__.py
      execute_template.py
      ...
  catalog_service.py
```

### Connector Catalog File Schema

Each `{connector_type}.json` is the authoritative definition of everything that connector can do.

```json
{
  "connector_type": "cloudflare",
  "display_name": "Cloudflare",
  "actions": [
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
        {"name": "new_value",   "type": "string", "required": true},
        {"name": "ttl",         "type": "integer", "required": false, "default": 300}
      ],
      "executor": "cloudflare_mock.update_dns_record",
      "rollback_action": "restore_dns_record",
      "estimated_duration_seconds": 10,
      "blast_radius_hint": "dns_propagation_delay",
      "safety_notes": ["TTL propagation may take up to 5 minutes"]
    },
    {
      "action_id": "restore_dns_record",
      "generic_action": "restore_dns_record",
      "action_type": "change",
      "execution_tier": 1,
      "display_name": "Restore DNS Record",
      "description": "Restores a DNS record to its previous value",
      "applicable_asset_types": ["dns_zone"],
      "parameters": [
        {"name": "record_name",     "type": "string", "required": true},
        {"name": "previous_value",  "type": "string", "required": true}
      ],
      "executor": "cloudflare_mock.restore_dns_record",
      "estimated_duration_seconds": 10
    },
    {
      "action_id": "discover_dns_zones",
      "generic_action": "discover_dns_zones",
      "action_type": "ingest",
      "execution_tier": 1,
      "display_name": "Discover DNS Zones",
      "description": "Lists all DNS zones and records (ingest only — not yet executed)",
      "applicable_asset_types": ["dns_zone"],
      "parameters": [],
      "executor": "cloudflare_mock.discover_dns_zones"
    }
  ]
}
```

### Key Field Definitions

| Field | Description |
|-------|-------------|
| `generic_action` | Universal action name shared across connectors. This is what the planning engine works with; the connector is a resolution detail. |
| `action_type` | `change` or `ingest`. Ingest actions are catalogued but not executed in this version. |
| `execution_tier` | Priority rank for connector selection (see Section 3). |
| `executor` | Dot-notation reference: `"{connector_type}.{module_name}"`, resolved to `executors/{connector_type}/{module_name}.py`. |
| `rollback_action` | References another `action_id` in the same connector file. |
| `blast_radius_hint` | Tag used by the safety engine for risk scoring, replacing hardcoded logic. |
| `applicable_asset_types` | Filters which actions are shown for a given asset. |

### Change Type Definition File Schema

Maps each `change_type` to its required sequence of `generic_action`s:

```json
{
  "change_type": "dns_update",
  "display_name": "DNS Record Update",
  "steps": [
    {"generic_action": "capture_dns_record",   "purpose": "preflight_capture",  "required": true},
    {"generic_action": "validate_dns_target",  "purpose": "preflight_validate", "required": true},
    {"generic_action": "update_dns_record",    "purpose": "execute",            "required": true},
    {"generic_action": "wait_dns_propagation", "purpose": "verify",             "required": false}
  ],
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "verification_methods": ["dns_lookup", "http_probe"]
}
```

---

## Section 3: Action Resolution & Connector Selection

### ActionCatalogService

New service at `backend/app/connectors/catalog_service.py`. Loads all connector JSON files at startup and builds two in-memory indexes:

1. `connector_type → [action_def]` — direct lookup for execution
2. `generic_action → [{connector_type, action_def}]` — cross-reference for planning

**Key methods:**

```python
get_options_for_action(generic_action: str, asset: Asset, active_connectors: list[Connector]) -> list[ActionOption]
# Returns all connector-action pairs applicable to this asset type,
# filtered to connectors active in the org, sorted by execution_tier ascending.

get_executor(connector_type: str, action_id: str) -> Callable
# Resolves dot-notation executor reference to a callable module.

get_action_def(connector_type: str, action_id: str) -> dict
# Returns a specific action definition.

list_generic_actions(action_type: str | None = None) -> list[str]
# Full generic action listing for UI / planning.
```

### Execution Tier Priority

When multiple connectors can perform the same `generic_action` on a given asset, the AI selects the lowest-numbered tier available. The human can override to any available tier before submitting for approval.

| Tier | Label | Examples |
|------|-------|---------|
| 1 | `direct_api` | AWS API, Cloudflare API, PAN-OS API, Okta API |
| 2 | `managed_channel` | Ansible, Chef, Puppet |
| 3 | `agent` | Nexplane agent *(future)* |
| 4 | `declarative_push` | SCCM, GPO, cloud-init |
| 5 | `raw_remote_command` | SSH, WinRM |

Tier 5 (`raw_remote_command`) automatically adds risk weight in the safety engine, consistent with how the current system treats SSH as higher risk.

### Updated `GeneratedStep` Structure

```json
{
  "step_number": 1,
  "name": "Update DNS Record",
  "generic_action": "update_dns_record",
  "connector_type": "cloudflare_mock",
  "action_id": "update_dns_record",
  "execution_tier": 1,
  "connector_options": [
    {"connector_type": "cloudflare_mock", "action_id": "update_dns_record", "execution_tier": 1}
  ],
  "parameters": {
    "record_name": "api.acme.example",
    "record_type": "A",
    "new_value": "10.0.1.5",
    "ttl": 300
  },
  "rollback_action": "restore_dns_record",
  "estimated_duration_seconds": 10,
  "blast_radius_hint": "dns_propagation_delay"
}
```

---

## Section 4: Planning Engine Refactor

`planning_engine.py` is refactored in-place — same external interface (`generate_plan(change_request, assets)`) — but the internals shift from hardcoded per-change-type Python blocks to catalog-driven resolution.

### New Planning Flow

1. Load `change_type_definitions/{change_type}.json`
2. For each step's `generic_action`, call `ActionCatalogService.get_options_for_action(generic_action, assets, active_connectors)`
3. Auto-select the best connector per step: pick the option with the lowest available `execution_tier` among active connectors (MVP rule — no LLM call required at this stage)
4. Assemble `ChangePlan` with catalog-resolved steps including `connector_options` for human review

### Project-Level Planning

When generating a plan for a `Project` (AI-assisted path):

1. Receive `Project.goal` as input
2. Enumerate affected assets from inventory
3. Determine which `change_type`s are required and in what sequence
4. Generate one `ChangeRequest` per logical change unit, each with a pre-populated catalog-driven plan
5. Link all change requests to the project via `ProjectChangeRequest` with `sequence_order` and `depends_on`

The operator reviews the full generated set before any change request is submitted for approval.

---

## Section 5: Workflow Execution Layer

The `ExecuteChangeWorkflow` structure remains intact (preflight → execute → verify → complete/rollback). The execution layer is updated to resolve actions through the catalog instead of hardcoded branches.

### Executor Module Interface

Each executor is a focused Python module with a standard async interface:

```python
# backend/app/connectors/executors/cloudflare_mock/update_dns_record.py

async def execute(parameters: dict, asset_ids: list[str], connector: Connector) -> dict:
    """Executes the action. Returns a result dict stored on ExecutionRun."""
    ...

async def rollback(parameters: dict, execution_result: dict, connector: Connector) -> dict:
    """Reverses the action using the original execution result."""
    ...
```

### Updated Activity Flow

`activity_execute_change` in `workflows/activities.py`:

1. Reads `connector_type` + `action_id` from the plan step (instead of `change_type`)
2. Calls `ActionCatalogService.get_executor(connector_type, action_id)`
3. Calls `executor.execute(parameters, asset_ids, connector)`

Rollback follows the same pattern using `rollback_action` from the plan step.

### Migration of Existing Mock Logic

The current `connector_service.py` execution branches (`_execute_dns_update`, `_execute_snapshot`, etc.) are migrated into individual executor modules under `executors/aws_mock/`, `executors/cloudflare_mock/`, etc. Behavior is preserved; structure changes to fit the new pattern. `connector_service.py` becomes a thin dispatcher.

---

## Summary of New Files

| Path | Purpose |
|------|---------|
| `backend/app/models/project.py` | Project + ProjectChangeRequest ORM models |
| `backend/app/schemas/project.py` | Pydantic schemas for Project API |
| `backend/app/routers/projects.py` | Project CRUD + AI-assisted plan generation endpoints |
| `backend/app/connectors/catalog_service.py` | ActionCatalogService — loads JSON, builds indexes, resolves executors |
| `backend/app/connectors/catalog/*.json` | Per-connector action definitions (one file per connector type) |
| `backend/app/connectors/change_type_definitions/*.json` | Generic action sequences per change type |
| `backend/app/connectors/executors/**/*.py` | Individual executor modules (migrated from connector_service.py + new) |

## Summary of Modified Files

| Path | Change |
|------|--------|
| `backend/app/services/planning_engine.py` | Refactored to read from catalog + change_type_definitions |
| `backend/app/services/connector_service.py` | Becomes thin dispatcher; execution logic moves to executors/ |
| `backend/app/services/safety_engine.py` | Risk scoring reads `execution_tier` and `blast_radius_hint` from catalog |
| `backend/alembic/` | New migration for Project + ProjectChangeRequest tables |
| `backend/app/main.py` | Register projects router; init ActionCatalogService on startup |
