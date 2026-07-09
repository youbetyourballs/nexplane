# Impact Simulation Smoke Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add two live smoke phases (`MCP_IMPACT_GRAPH` and `MCP_IMPACT_PLANNING`) that verify the asset dependency graph traversal, the `GET /impact-simulation` REST endpoint, MCP blast-radius tools, and the planning engine's `blast_radius` field population against real infrastructure.

**Architecture:** Both phases are appended to `backend/tests/smoke/test_mcp_server_live.py` and registered in `ALL_PHASES` / `phase_fns`. They use `_smoke_state` for shared asset IDs, follow the existing in-process MCP tool invocation pattern (`_invoke_mcp_tool_inprocess`), and the REST client pattern (`NexplaneClient`). `MCP_IMPACT_GRAPH` creates a fresh 3-node chain and stores IDs in `_smoke_state` for `MCP_IMPACT_PLANNING` to consume.

**Tech Stack:** Python, FastAPI REST endpoints, in-process MCP tool invocation, SQLAlchemy async DB cross-checks via `_run_db_check`.

---

## Global Constraints

- All assertions run against live EC2 infrastructure — no mocks.
- Both phases run in the full 19-phase suite order; `MCP_IMPACT_GRAPH` must precede `MCP_IMPACT_PLANNING`.
- `MCP_IMPACT_GRAPH` depends on `_smoke_state["api_token"]` (set by `MCP_TOOL_ENUM`) and `_smoke_state["asset_id"]` (set by `MCP_CR_ROUNDTRIP`).
- `MCP_IMPACT_PLANNING` depends on `impact_root_id`, `impact_mid_id`, `impact_leaf_id` set by `MCP_IMPACT_GRAPH`.
- All created assets and CRs are cleaned up at the end of the phase that owns them.
- CR lifecycle in smoke: create → plan (poll for `"planned"` status) → submit-for-approval → approve → no execute needed for blast_radius check.
- `fail(msg)` is the assertion helper; `log(msg)` is the progress helper.
- Run command: `docker exec nexplane-backend-1 python3 tests/smoke/test_mcp_server_live.py --base-url http://172.31.1.233:8000 --email admin@acme.example --password admin123 --phases MCP_IMPACT_GRAPH,MCP_IMPACT_PLANNING`

---

## Shared State Keys Added

| Key | Set by | Consumed by |
|-----|--------|-------------|
| `impact_root_id` | `MCP_IMPACT_GRAPH` | `MCP_IMPACT_PLANNING` |
| `impact_mid_id` | `MCP_IMPACT_GRAPH` | `MCP_IMPACT_PLANNING` |
| `impact_leaf_id` | `MCP_IMPACT_GRAPH` | `MCP_IMPACT_PLANNING` |

Add to `_smoke_state` initializer: `"impact_root_id": "", "impact_mid_id": "", "impact_leaf_id": ""`.

---

## Phase 1: MCP_IMPACT_GRAPH

**Purpose:** Verify the asset dependency graph traversal is correct — `GET /impact-simulation` REST endpoint returns accurate upstream/downstream at each node, MCP tools agree, DB cross-check confirms.

### Setup

Create three fresh assets via `POST /assets`:

```python
root_asset = client.post("/assets", json={"name": "smoke-impact-root", "asset_type": "server", "environment": "staging"})
mid_asset  = client.post("/assets", json={"name": "smoke-impact-mid",  "asset_type": "server", "environment": "staging"})
leaf_asset = client.post("/assets", json={"name": "smoke-impact-leaf", "asset_type": "server", "environment": "staging"})
root_id = root_asset["id"]; mid_id = mid_asset["id"]; leaf_id = leaf_asset["id"]
```

Create relationships (leaf depends on mid, mid depends on root):

```python
client.post(f"/assets/{leaf_id}/relationships", json={"target_asset_id": mid_id,  "relationship_type": "depends_on"})
client.post(f"/assets/{mid_id}/relationships",  json={"target_asset_id": root_id, "relationship_type": "depends_on"})
```

Store in `_smoke_state`: `impact_root_id = root_id`, `impact_mid_id = mid_id`, `impact_leaf_id = leaf_id`.

### Assertions

**Part A — REST traversal from root:**
```
GET /impact-simulation?asset_id=root_id
```
- `response["asset"]["id"] == root_id`
- `response["upstream"] == []`
- downstream IDs contain both mid_id and leaf_id (transitive)
- `response["downstream_risk"]["total"] >= 2`

**Part B — REST traversal from mid:**
```
GET /impact-simulation?asset_id=mid_id
```
- downstream IDs contain leaf_id
- upstream IDs contain root_id

**Part C — REST traversal from leaf:**
```
GET /impact-simulation?asset_id=leaf_id
```
- `response["downstream"] == []`
- upstream IDs contain both mid_id and root_id (transitive depth=2)

**Part D — 404 for non-existent asset:**
```
GET /impact-simulation?asset_id=00000000-0000-0000-0000-000000000000
```
- HTTP 404

**Part E — isolated asset:**
Create a temporary asset with no relationships. Run `GET /impact-simulation` on it.
- `response["upstream"] == []`
- `response["downstream"] == []`
- `response["downstream_risk"]["total"] == 0`
Delete the isolated asset immediately after.

**Part F — MCP tool: get_asset_neighbors:**
```python
neighbors = _invoke_mcp_tool_inprocess("get_asset_neighbors", {"token": api_token, "asset_id": root_id})
```
- Result contains mid_id in downstream neighbors
- Upstream neighbors empty for root

**Part G — MCP tool: get_asset_upstream + DB cross-check:**
```python
upstream = _invoke_mcp_tool_inprocess("get_asset_upstream", {"token": api_token, "asset_id": leaf_id, "max_depth": 3})
```
- Returns list containing mid_id (depth=1) and root_id (depth=2)
- DB cross-check: query `asset_relationships` table for leaf_id source — count == 1 (direct); for transitive count via mid_id source — total chain length == 2.

### No cleanup here — assets retained for MCP_IMPACT_PLANNING.

---

## Phase 2: MCP_IMPACT_PLANNING

**Purpose:** Verify the planning engine populates `blast_radius` correctly when a CR targets an asset with dependents, and returns empty blast_radius when the target has none.

**Prerequisite:** `_smoke_state["impact_root_id"]`, `impact_mid_id`, `impact_leaf_id` all set.

### Part A — blast_radius populated for CR targeting mid (has leaf as dependent)

1. Create a `tag_resource` CR targeting `mid_id`:
```python
cr = client.post("/change-requests", json={
    "change_type": "tag_resource",
    "title": "smoke-impact-planning-mid",
    "desired_outcome": {"tag_key": "smoke-impact", "tag_value": "true"},
    "target_asset_ids": [mid_id],
})
cr_id = cr["id"]
```

2. Plan the CR and poll for `"planned"` status (up to 15s):
```python
client.post(f"/change-requests/{cr_id}/plan")
# poll until status not in ("planning", "pending", "draft")
```

3. Fetch the CR with its plan:
```python
cr_detail = client.get(f"/change-requests/{cr_id}")
blast_radius = cr_detail.get("blast_radius") or cr_detail.get("change_plan", {}).get("blast_radius")
```

4. Assert:
- `blast_radius` is not None and not empty dict
- `blast_radius` contains a downstream reference (key `"downstream"` or `"affected_asset_count"` > 0)
- DB cross-check: query `change_plans` table for this `cr_id` — `blast_radius` column is not null

5. Cross-tool consistency — MCP `get_asset_neighbors` on `mid_id` returns leaf in downstream; count matches `blast_radius` downstream count.

### Part B — blast_radius empty for CR targeting isolated asset

1. Create a temporary isolated asset (no relationships):
```python
iso = client.post("/assets", json={"name": "smoke-impact-iso", "asset_type": "server", "environment": "staging"})
iso_id = iso["id"]
```

2. Create and plan a CR targeting `iso_id`:
```python
iso_cr = client.post("/change-requests", json={
    "change_type": "tag_resource",
    "title": "smoke-impact-planning-iso",
    "desired_outcome": {"tag_key": "smoke-impact", "tag_value": "true"},
    "target_asset_ids": [iso_id],
})
iso_cr_id = iso_cr["id"]
client.post(f"/change-requests/{iso_cr_id}/plan")
# poll for planned status
```

3. Assert blast_radius is empty or zero:
```python
iso_cr_detail = client.get(f"/change-requests/{iso_cr_id}")
iso_blast = iso_cr_detail.get("blast_radius") or iso_cr_detail.get("change_plan", {}).get("blast_radius", {})
affected = iso_blast.get("affected_asset_count", 0) if iso_blast else 0
# assert affected == 0 OR blast_radius is None/empty
```

### Cleanup

```python
client.delete(f"/change-requests/{cr_id}")
client.delete(f"/change-requests/{iso_cr_id}")
client.delete(f"/assets/{iso_id}")
client.delete(f"/assets/{leaf_id}")   # leaf first (depends on mid)
client.delete(f"/assets/{mid_id}")    # mid second (depends on root)
client.delete(f"/assets/{root_id}")   # root last
```

---

## Registration

In `test_mcp_server_live.py`:

1. Add to `_smoke_state` initializer:
```python
"impact_root_id": "", "impact_mid_id": "", "impact_leaf_id": "",
```

2. Add to `ALL_PHASES` (after `MCP_INFRA_DELETION_CHECK`):
```python
"MCP_IMPACT_GRAPH", "MCP_IMPACT_PLANNING",
```

3. Add to `phase_fns`:
```python
"MCP_IMPACT_GRAPH":    lambda: phase_mcp_impact_graph(client, base_url),
"MCP_IMPACT_PLANNING": lambda: phase_mcp_impact_planning(client, base_url),
```

4. Add to `MCP_SERVER_PHASES` in `run_on_ec2.py`:
```python
"MCP_IMPACT_GRAPH", "MCP_IMPACT_PLANNING",
```

---

## Success Criteria

- `MCP_IMPACT_GRAPH` PASSED: all 7 assertions green, no assets leaked
- `MCP_IMPACT_PLANNING` PASSED: blast_radius populated for mid CR, empty for isolated CR, all cleanup complete
- Full 21-phase run: `21/21 MCP_SERVER_SMOKE PHASES PASSED`
