# Impact Simulation Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two live smoke phases (`MCP_IMPACT_GRAPH` and `MCP_IMPACT_PLANNING`) to `test_mcp_server_live.py` that verify asset dependency graph traversal, the `GET /impact-simulation` REST endpoint, MCP blast-radius tools, and the planning engine's `blast_radius` field population.

**Architecture:** Both functions are appended to `backend/tests/smoke/test_mcp_server_live.py` following existing phase patterns — `_invoke_mcp_tool_inprocess` for MCP tools, `NexplaneClient` for REST, `_run_db_check` for DB cross-checks. `MCP_IMPACT_GRAPH` creates a 3-node chain (root ← mid ← leaf) and stores IDs in `_smoke_state`. `MCP_IMPACT_PLANNING` consumes those IDs, creates CRs, plans them, and verifies `blast_radius` is populated correctly. Both phases clean up all created resources.

**Tech Stack:** Python, FastAPI REST (`GET /impact-simulation`, `POST /assets`, `POST /assets/{id}/relationships`, `POST /change-requests`, `POST /change-requests/{id}/plan`), MCP in-process tools (`get_asset_neighbors`, `get_asset_upstream`), SQLAlchemy async DB cross-check.

## Global Constraints

- All assertions run against live EC2 infrastructure — no mocks.
- Run command: `docker exec nexplane-backend-1 python3 tests/smoke/test_mcp_server_live.py --base-url http://172.31.1.233:8000 --email admin@acme.example --password admin123 --phases MCP_IMPACT_GRAPH,MCP_IMPACT_PLANNING`
- Full suite run (21 phases): add `MCP_IMPACT_GRAPH,MCP_IMPACT_PLANNING` to the existing 19-phase comma-separated list.
- `MCP_IMPACT_GRAPH` must precede `MCP_IMPACT_PLANNING` in `ALL_PHASES`.
- `MCP_IMPACT_GRAPH` requires `_smoke_state["api_token"]` (set by `MCP_TOOL_ENUM`).
- `MCP_IMPACT_PLANNING` requires `_smoke_state["impact_root_id"]`, `impact_mid_id`, `impact_leaf_id` (set by `MCP_IMPACT_GRAPH`).
- Asset creation payload requires `name`, `asset_type`, `environment`, `criticality` fields.
- Relationship creation: `POST /assets/{source_id}/relationships` with body `{"target_asset_id": "...", "relationship_type": "depends_on"}` — source depends on target.
- `blast_radius` is nested: `cr_detail["change_plan"]["blast_radius"]` — NOT at top-level `cr_detail["blast_radius"]`.
- `blast_radius` shape: `{"affected_assets": [...], "affected_environments": [...], "estimated_impact": str, "affected_services": [], "recovery_time_estimate": str, "rollback_available": bool}`. Each `affected_assets` entry has `id`, `name`, `type`, `env`, `criticality`.
- `GET /impact-simulation` takes `asset_id` as a query param: `client.get("/impact-simulation", params={"asset_id": str(asset_id)})`.
- `GET /impact-simulation` response shape: `{"asset": {...}, "upstream": [...], "downstream": [...], "downstream_risk": {"critical": int, "high": int, "total": int}, "recent_crs": [...], "open_findings_count": int}`.
- Cleanup order for chain: delete leaf first (it depends on mid), then mid (it depends on root), then root.
- `fail(msg)` raises assertion failure. `log(msg)` prints progress. Both are already defined in the file.

---

### Task 1: MCP_IMPACT_GRAPH phase

Add the `phase_mcp_impact_graph` function and wire it into registration.

**Files:**
- Modify: `backend/tests/smoke/test_mcp_server_live.py`

**Interfaces:**
- Consumes: `_smoke_state["api_token"]`, `_invoke_mcp_tool_inprocess`, `_run_db_check`, `NexplaneClient`, `fail`, `log`
- Produces: `_smoke_state["impact_root_id"]`, `_smoke_state["impact_mid_id"]`, `_smoke_state["impact_leaf_id"]` — consumed by Task 2

- [ ] **Step 1: Add three new keys to `_smoke_state`**

Find the `_smoke_state` dict initializer (line ~238). It currently ends with `"runbook_id": "",`. Add three new keys after it:

```python
_smoke_state: dict = {
    "api_token": "",
    "asset_id": "",
    "cr_id": "",
    "approver_id": "",
    "cr_created_at": "",
    "dependent_asset_id": "",
    "seeded_finding_id": "",
    "finding_id": "",
    "connector_id": "",
    "seeded_runbook_id": "",
    "runbook_id": "",
    "impact_root_id": "",   # NEW
    "impact_mid_id": "",    # NEW
    "impact_leaf_id": "",   # NEW
}
```

- [ ] **Step 2: Write the phase function**

Add this function immediately before the `# ---------------------------------------------------------------------------` comment that precedes the `main()` function registration block. Place it after `phase_mcp_infra_deletion_check`:

```python
# ---------------------------------------------------------------------------
# Phase: MCP_IMPACT_GRAPH
# ---------------------------------------------------------------------------

def phase_mcp_impact_graph(client: NexplaneClient, base_url: str) -> None:
    print("\n[MCP_IMPACT_GRAPH] Asset dependency graph traversal + GET /impact-simulation", flush=True)

    api_token = _smoke_state["api_token"]
    assert api_token, "api_token not set — run MCP_TOOL_ENUM first"

    # ── Setup: create 3-node chain root ← mid ← leaf ─────────────────────────
    # "leaf depends on mid, mid depends on root"
    # source_id depends on target_id (source → target = depends_on)
    root = client.post("/assets", json={
        "name": f"smoke-impact-root-{uuid.uuid4().hex[:6]}",
        "asset_type": "server",
        "environment": "staging",
        "criticality": "high",
    })
    mid = client.post("/assets", json={
        "name": f"smoke-impact-mid-{uuid.uuid4().hex[:6]}",
        "asset_type": "server",
        "environment": "staging",
        "criticality": "medium",
    })
    leaf = client.post("/assets", json={
        "name": f"smoke-impact-leaf-{uuid.uuid4().hex[:6]}",
        "asset_type": "server",
        "environment": "staging",
        "criticality": "low",
    })
    root_id = root["id"]
    mid_id  = mid["id"]
    leaf_id = leaf["id"]

    # mid depends on root
    client.post(f"/assets/{mid_id}/relationships", json={
        "target_asset_id": root_id,
        "relationship_type": "depends_on",
    })
    # leaf depends on mid
    client.post(f"/assets/{leaf_id}/relationships", json={
        "target_asset_id": mid_id,
        "relationship_type": "depends_on",
    })

    # Store for Task 2
    _smoke_state["impact_root_id"] = root_id
    _smoke_state["impact_mid_id"]  = mid_id
    _smoke_state["impact_leaf_id"] = leaf_id
    log(f"Chain created: root={root_id}, mid={mid_id}, leaf={leaf_id}")

    # ── Part A: GET /impact-simulation from root ───────────────────────────────
    # root has no upstream; mid and leaf are downstream (depth 1 and 2)
    resp_root = client.get("/impact-simulation", params={"asset_id": root_id})
    if not isinstance(resp_root, dict):
        fail(f"GET /impact-simulation (root) returned non-dict: {type(resp_root)}")
    for key in ("asset", "upstream", "downstream", "downstream_risk"):
        if key not in resp_root:
            fail(f"/impact-simulation (root) missing key {key!r}. Keys: {list(resp_root.keys())}")
    if resp_root["upstream"]:
        fail(f"root should have no upstream, got: {resp_root['upstream']}")
    downstream_ids = {d["id"] for d in resp_root["downstream"]}
    if mid_id not in downstream_ids:
        fail(f"mid_id {mid_id} not in root downstream. Got: {list(downstream_ids)}")
    if leaf_id not in downstream_ids:
        fail(f"leaf_id {leaf_id} not in root downstream (transitive). Got: {list(downstream_ids)}")
    if resp_root["downstream_risk"]["total"] < 2:
        fail(f"downstream_risk.total should be >= 2 from root, got {resp_root['downstream_risk']['total']}")
    log(f"Part A: root downstream={len(resp_root['downstream'])}, risk_total={resp_root['downstream_risk']['total']}")

    # ── Part B: GET /impact-simulation from mid ────────────────────────────────
    resp_mid = client.get("/impact-simulation", params={"asset_id": mid_id})
    downstream_mid_ids = {d["id"] for d in resp_mid["downstream"]}
    upstream_mid_ids   = {u["id"] for u in resp_mid["upstream"]}
    if leaf_id not in downstream_mid_ids:
        fail(f"leaf_id {leaf_id} not in mid downstream. Got: {list(downstream_mid_ids)}")
    if root_id not in upstream_mid_ids:
        fail(f"root_id {root_id} not in mid upstream. Got: {list(upstream_mid_ids)}")
    log(f"Part B: mid upstream={len(resp_mid['upstream'])}, downstream={len(resp_mid['downstream'])}")

    # ── Part C: GET /impact-simulation from leaf ───────────────────────────────
    resp_leaf = client.get("/impact-simulation", params={"asset_id": leaf_id})
    if resp_leaf["downstream"]:
        fail(f"leaf should have no downstream, got: {resp_leaf['downstream']}")
    upstream_leaf_ids = {u["id"] for u in resp_leaf["upstream"]}
    if mid_id not in upstream_leaf_ids:
        fail(f"mid_id {mid_id} not in leaf upstream. Got: {list(upstream_leaf_ids)}")
    if root_id not in upstream_leaf_ids:
        fail(f"root_id {root_id} not in leaf upstream (transitive depth=2). Got: {list(upstream_leaf_ids)}")
    log(f"Part C: leaf upstream={len(resp_leaf['upstream'])}, downstream=0")

    # ── Part D: 404 for non-existent asset ─────────────────────────────────────
    try:
        client.get("/impact-simulation", params={"asset_id": "00000000-0000-0000-0000-000000000000"})
        fail("Expected 404 for non-existent asset, got success")
    except Exception as exc:
        if "404" not in str(exc):
            fail(f"Expected 404 for non-existent asset, got: {exc}")
    log("Part D: 404 for non-existent asset confirmed")

    # ── Part E: isolated asset has empty upstream + downstream ─────────────────
    iso = client.post("/assets", json={
        "name": f"smoke-impact-iso-{uuid.uuid4().hex[:6]}",
        "asset_type": "server",
        "environment": "dev",
        "criticality": "low",
    })
    iso_id = iso["id"]
    try:
        resp_iso = client.get("/impact-simulation", params={"asset_id": iso_id})
        if resp_iso["upstream"]:
            fail(f"isolated asset should have no upstream, got: {resp_iso['upstream']}")
        if resp_iso["downstream"]:
            fail(f"isolated asset should have no downstream, got: {resp_iso['downstream']}")
        if resp_iso["downstream_risk"]["total"] != 0:
            fail(f"isolated downstream_risk.total should be 0, got {resp_iso['downstream_risk']['total']}")
        log("Part E: isolated asset shows empty upstream/downstream/risk")
    finally:
        client.delete(f"/assets/{iso_id}")

    # ── Part F: MCP get_asset_neighbors on root ────────────────────────────────
    neighbors = _invoke_mcp_tool_inprocess("get_asset_neighbors", {
        "token": api_token,
        "asset_id": root_id,
    })
    assert "error" not in neighbors, f"get_asset_neighbors error: {neighbors}"
    downstream_nbr = neighbors.get("downstream", [])
    nbr_ids = {e.get("id") or e.get("asset_id") for e in downstream_nbr}
    if mid_id not in nbr_ids:
        fail(f"get_asset_neighbors: mid_id {mid_id} not in root downstream. Got: {nbr_ids}")
    upstream_nbr = neighbors.get("upstream", [])
    if upstream_nbr:
        fail(f"get_asset_neighbors: root should have no upstream, got: {upstream_nbr}")
    log(f"Part F: get_asset_neighbors root downstream contains mid")

    # ── Part G: MCP get_asset_upstream on leaf + DB cross-check ───────────────
    upstream_chain = _invoke_mcp_tool_inprocess("get_asset_upstream", {
        "token": api_token,
        "asset_id": leaf_id,
        "max_depth": 3,
    })
    assert isinstance(upstream_chain, list), f"get_asset_upstream returned non-list: {upstream_chain}"
    chain_ids = {e.get("id") or e.get("asset_id") for e in upstream_chain}
    if mid_id not in chain_ids:
        fail(f"get_asset_upstream: mid_id {mid_id} not in leaf upstream chain. Got: {chain_ids}")
    if root_id not in chain_ids:
        fail(f"get_asset_upstream: root_id {root_id} not in leaf upstream chain (depth=2). Got: {chain_ids}")

    # DB cross-check: leaf should have exactly 1 direct relationship row (leaf → mid)
    async def _check_leaf_rels(SessionLocal):
        from sqlalchemy import text
        async with SessionLocal() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM asset_relationships WHERE source_asset_id = :lid"),
                {"lid": leaf_id},
            )
            return result.scalar()

    db_rel_count = _run_db_check(_check_leaf_rels)
    if db_rel_count != 1:
        fail(f"Expected 1 direct relationship for leaf in DB, got {db_rel_count}")
    log(f"Part G: get_asset_upstream leaf chain={list(chain_ids)}, DB rel_count={db_rel_count}")

    print("[MCP_IMPACT_GRAPH] PASSED", flush=True)
```

- [ ] **Step 3: Register the phase**

Find the `ALL_PHASES` list and add `"MCP_IMPACT_GRAPH"` after `"MCP_INFRA_DELETION_CHECK"`:

```python
ALL_PHASES = [
    "MCP_TOOL_ENUM",
    "MCP_AGENT_TOKEN_AUTH",
    "MCP_CR_ROUNDTRIP",
    "MCP_PROVENANCE",
    "MCP_WHO_APPROVED",
    "MCP_DEPENDENCY",
    "MCP_SAFETY_QUERY",
    "MCP_TIMELINE",
    "MCP_FINDINGS",
    "MCP_CONNECTORS",
    "MCP_IDENTITY",
    "MCP_RUNBOOKS",
    "MCP_HOST_INTEL",
    "MCP_PLANNING_CTX",
    "MCP_MEMORY_ACCURACY",
    "MCP_INFRA_PROVENANCE",
    "MCP_INFRA_TIMELINE",
    "MCP_INFRA_QUERY",
    "MCP_INFRA_DELETION_CHECK",
    "MCP_IMPACT_GRAPH",      # NEW
]
```

Find the `phase_fns` dict and add:
```python
"MCP_IMPACT_GRAPH":    lambda: phase_mcp_impact_graph(client, base_url),
```

Find `MCP_SERVER_PHASES` in `backend/tests/smoke/run_on_ec2.py` and add `"MCP_IMPACT_GRAPH"` to the set.

- [ ] **Step 4: SCP to EC2 and run just the new phase**

```bash
# From Windows PowerShell:
scp -i "$HOME\.ssh\id_ed25519" "backend/tests/smoke/test_mcp_server_live.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/test_mcp_server_live.py"

# Run only graph phase with prerequisites:
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 'docker exec nexplane-backend-1 python3 tests/smoke/test_mcp_server_live.py --base-url http://172.31.1.233:8000 --email admin@acme.example --password admin123 --phases MCP_TOOL_ENUM,MCP_AGENT_TOKEN_AUTH,MCP_CR_ROUNDTRIP,MCP_DEPENDENCY,MCP_IMPACT_GRAPH 2>&1'
```

Expected: `MCP_IMPACT_GRAPH PASSED`

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_mcp_server_live.py backend/tests/smoke/run_on_ec2.py
git commit -m "feat: add MCP_IMPACT_GRAPH smoke phase — asset dependency graph traversal"
```

---

### Task 2: MCP_IMPACT_PLANNING phase

Add the `phase_mcp_impact_planning` function and wire it into registration. Verify planning engine populates `blast_radius` and cross-reference with `GET /impact-simulation`.

**Files:**
- Modify: `backend/tests/smoke/test_mcp_server_live.py`
- Modify: `backend/tests/smoke/run_on_ec2.py`

**Interfaces:**
- Consumes: `_smoke_state["api_token"]`, `_smoke_state["impact_root_id"]`, `_smoke_state["impact_mid_id"]`, `_smoke_state["impact_leaf_id"]` — all set by Task 1
- Produces: nothing (terminal phase); cleans up `impact_root_id`, `impact_mid_id`, `impact_leaf_id` assets

- [ ] **Step 1: Write the phase function**

Add immediately after `phase_mcp_impact_graph`:

```python
# ---------------------------------------------------------------------------
# Phase: MCP_IMPACT_PLANNING
# ---------------------------------------------------------------------------

def phase_mcp_impact_planning(client: NexplaneClient, base_url: str) -> None:
    print("\n[MCP_IMPACT_PLANNING] Planning engine blast_radius population", flush=True)

    api_token = _smoke_state["api_token"]
    root_id = _smoke_state.get("impact_root_id", "")
    mid_id  = _smoke_state.get("impact_mid_id", "")
    leaf_id = _smoke_state.get("impact_leaf_id", "")

    if not root_id or not mid_id or not leaf_id:
        fail("impact chain IDs not set — run MCP_IMPACT_GRAPH first")

    # ── Part A: CR targeting mid (has leaf as downstream dependent) ────────────
    cr_mid = client.post("/change-requests", json={
        "change_type": "tag_resource",
        "title": "smoke-impact-planning-mid",
        "desired_outcome": {"tag_key": "smoke-impact", "tag_value": "true"},
        "target_asset_ids": [mid_id],
    })
    cr_mid_id = cr_mid["id"]

    # Plan the CR and poll until it leaves planning state
    client.post(f"/change-requests/{cr_mid_id}/plan")
    cr_detail = {}
    for _ in range(15):
        time.sleep(1)
        cr_detail = client.get(f"/change-requests/{cr_mid_id}")
        if cr_detail.get("status") not in ("planning", "pending", "draft"):
            break
    post_plan_status = cr_detail.get("status")
    log(f"Part A: CR targeting mid reached status: {post_plan_status}")

    # Verify change_plan is present and blast_radius is populated
    change_plan = cr_detail.get("change_plan")
    if change_plan is None:
        fail(f"change_plan is None after planning — status was {post_plan_status}")

    blast_radius = change_plan.get("blast_radius")
    if not blast_radius:
        fail(f"blast_radius is empty after planning CR targeting mid. change_plan keys: {list(change_plan.keys())}")

    # Verify blast_radius shape
    for key in ("affected_assets", "affected_environments", "estimated_impact", "rollback_available"):
        if key not in blast_radius:
            fail(f"blast_radius missing key {key!r}. Keys: {list(blast_radius.keys())}")

    affected = blast_radius["affected_assets"]
    if not isinstance(affected, list) or len(affected) == 0:
        fail(f"blast_radius.affected_assets should be non-empty list, got: {affected!r}")

    affected_ids = {a["id"] for a in affected}
    if mid_id not in affected_ids:
        fail(f"blast_radius.affected_assets should contain mid_id {mid_id}. Got: {affected_ids}")

    log(f"Part A: blast_radius populated, affected_assets={[a['id'] for a in affected]}")

    # DB cross-check: blast_radius column in change_plans must be non-null
    async def _check_blast_radius(SessionLocal):
        from sqlalchemy import text
        async with SessionLocal() as session:
            result = await session.execute(
                text("SELECT blast_radius FROM change_plans WHERE change_request_id = :crid"),
                {"crid": cr_mid_id},
            )
            row = result.fetchone()
            return row[0] if row else None

    db_blast = _run_db_check(_check_blast_radius)
    if db_blast is None:
        fail(f"DB: change_plans.blast_radius is None for cr_mid_id {cr_mid_id}")
    if not db_blast.get("affected_assets"):
        fail(f"DB: blast_radius.affected_assets is empty. Got: {db_blast}")
    log(f"Part A: DB blast_radius confirmed non-null, affected_assets count={len(db_blast.get('affected_assets', []))}")

    # ── Part B: GET /impact-simulation cross-check ─────────────────────────────
    # mid has leaf as downstream — planning + impact-simulation must agree on the asset
    resp_mid = client.get("/impact-simulation", params={"asset_id": mid_id})
    if resp_mid.get("downstream_risk", {}).get("total", 0) == 0:
        fail(
            f"GET /impact-simulation for mid should show downstream total > 0 "
            f"(leaf is downstream). Got downstream_risk={resp_mid.get('downstream_risk')}"
        )
    # The planned blast_radius should reference mid; impact-simulation shows its downstream
    # together they prove: planning captures the target, impact-sim shows the ripple effect
    log(
        f"Part B: impact-simulation cross-check — mid downstream_risk.total="
        f"{resp_mid['downstream_risk']['total']}, blast_radius affected_assets={len(affected)}"
    )

    # MCP tool agrees with REST: get_asset_neighbors on mid returns leaf downstream
    neighbors = _invoke_mcp_tool_inprocess("get_asset_neighbors", {
        "token": api_token,
        "asset_id": mid_id,
    })
    assert "error" not in neighbors, f"get_asset_neighbors error: {neighbors}"
    nbr_downstream_ids = {e.get("id") or e.get("asset_id") for e in neighbors.get("downstream", [])}
    if leaf_id not in nbr_downstream_ids:
        fail(f"get_asset_neighbors mid: leaf_id {leaf_id} not in downstream. Got: {nbr_downstream_ids}")
    log(f"Part B: MCP get_asset_neighbors agrees — leaf in mid downstream")

    # ── Part C: isolated asset — blast_radius still populated, downstream empty ─
    iso = client.post("/assets", json={
        "name": f"smoke-impact-iso-{uuid.uuid4().hex[:6]}",
        "asset_type": "server",
        "environment": "dev",
        "criticality": "low",
    })
    iso_id = iso["id"]

    cr_iso = client.post("/change-requests", json={
        "change_type": "tag_resource",
        "title": "smoke-impact-planning-iso",
        "desired_outcome": {"tag_key": "smoke-impact", "tag_value": "true"},
        "target_asset_ids": [iso_id],
    })
    cr_iso_id = cr_iso["id"]
    client.post(f"/change-requests/{cr_iso_id}/plan")
    iso_detail = {}
    for _ in range(15):
        time.sleep(1)
        iso_detail = client.get(f"/change-requests/{cr_iso_id}")
        if iso_detail.get("status") not in ("planning", "pending", "draft"):
            break

    iso_plan = iso_detail.get("change_plan")
    if iso_plan is None:
        fail(f"change_plan is None for isolated asset CR after planning")
    iso_blast = iso_plan.get("blast_radius", {})
    if not iso_blast:
        fail(f"blast_radius should be populated even for isolated asset CR (the target asset itself). Got: {iso_blast!r}")

    # Impact simulation for isolated asset must show empty downstream
    resp_iso = client.get("/impact-simulation", params={"asset_id": iso_id})
    if resp_iso.get("downstream_risk", {}).get("total", -1) != 0:
        fail(
            f"isolated asset GET /impact-simulation downstream_risk.total should be 0. "
            f"Got: {resp_iso.get('downstream_risk')}"
        )
    log(f"Part C: isolated asset blast_radius populated, impact-sim downstream=0")

    # ── Cleanup ────────────────────────────────────────────────────────────────
    for cr_id in (cr_mid_id, cr_iso_id):
        try:
            client.delete(f"/change-requests/{cr_id}")
        except Exception as e:
            log(f"Could not delete CR {cr_id}: {e}", ok=False)

    client.delete(f"/assets/{iso_id}")

    # Delete chain in dependency order: leaf first, then mid, then root
    for aid in (leaf_id, mid_id, root_id):
        try:
            client.delete(f"/assets/{aid}")
        except Exception as e:
            log(f"Could not delete asset {aid}: {e}", ok=False)

    log("Cleanup complete — chain and CRs deleted")
    print("[MCP_IMPACT_PLANNING] PASSED", flush=True)
```

- [ ] **Step 2: Register the phase**

Add `"MCP_IMPACT_PLANNING"` to `ALL_PHASES` after `"MCP_IMPACT_GRAPH"`:

```python
ALL_PHASES = [
    # ... existing 19 phases ...
    "MCP_IMPACT_GRAPH",
    "MCP_IMPACT_PLANNING",   # NEW
]
```

Add to `phase_fns`:
```python
"MCP_IMPACT_GRAPH":    lambda: phase_mcp_impact_graph(client, base_url),
"MCP_IMPACT_PLANNING": lambda: phase_mcp_impact_planning(client, base_url),   # NEW
```

Add `"MCP_IMPACT_PLANNING"` to `MCP_SERVER_PHASES` in `backend/tests/smoke/run_on_ec2.py`.

- [ ] **Step 3: SCP both files to EC2 and run full 21-phase suite**

```bash
# From Windows PowerShell:
scp -i "$HOME\.ssh\id_ed25519" "backend/tests/smoke/test_mcp_server_live.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/test_mcp_server_live.py"
scp -i "$HOME\.ssh\id_ed25519" "backend/tests/smoke/run_on_ec2.py" "ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/run_on_ec2.py"

# Run full 21-phase suite:
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 'docker exec nexplane-backend-1 python3 tests/smoke/test_mcp_server_live.py --base-url http://172.31.1.233:8000 --email admin@acme.example --password admin123 --phases MCP_TOOL_ENUM,MCP_AGENT_TOKEN_AUTH,MCP_CR_ROUNDTRIP,MCP_PROVENANCE,MCP_WHO_APPROVED,MCP_DEPENDENCY,MCP_SAFETY_QUERY,MCP_TIMELINE,MCP_FINDINGS,MCP_CONNECTORS,MCP_IDENTITY,MCP_RUNBOOKS,MCP_HOST_INTEL,MCP_PLANNING_CTX,MCP_MEMORY_ACCURACY,MCP_INFRA_PROVENANCE,MCP_INFRA_TIMELINE,MCP_INFRA_QUERY,MCP_INFRA_DELETION_CHECK,MCP_IMPACT_GRAPH,MCP_IMPACT_PLANNING 2>&1'
```

Expected output:
```
MCP_SERVER_SMOKE summary: 21/21 passed
ALL MCP_SERVER_SMOKE PHASES PASSED
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/smoke/test_mcp_server_live.py backend/tests/smoke/run_on_ec2.py
git commit -m "feat: add MCP_IMPACT_PLANNING smoke phase — blast_radius + impact-simulation cross-check"
```
