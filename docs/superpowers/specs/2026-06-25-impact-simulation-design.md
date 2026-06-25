# Impact Simulation Design Spec
**Sub-project 5 — Impact Simulation + Lifecycle Nav**
Date: 2026-06-25

---

## Problem Statement

The `/impact-simulation` page is a marketing stub. Users have no way to answer "what will happen if I change X?" before creating a change request. The asset graph and relationship data (sub-project 3) exist but are not surfaced as a blast-radius query tool.

Additionally, the sidebar nav groups items into flat sections that do not reflect Nexplane's repositioned lifecycle framing (Discover → Plan → Execute → Observe → Recover).

---

## Goals

1. Make Impact Simulation functional: asset search → blast-radius analysis in one page.
2. Reorganize sidebar nav into lifecycle section headers without changing routing.

---

## Non-Goals

- Change simulation (modeling a hypothetical change type — e.g., "what if I rotate this cert"). That is a future feature. This spec covers blast-radius *discovery*, not pre-change modeling.
- Findings integration: there is no `Finding` model in the codebase. The `open_findings_count` field is returned as `0` and documented as a future field.
- Graph visualization (force-directed). Text lists with criticality badges are sufficient.

---

## Part A: Impact Simulation

### API Design

**Endpoint:** `GET /impact-simulation?asset_id={uuid}`

**Auth:** Same `current_user` dependency as all other routers.

**Response:**
```json
{
  "asset": {
    "id": "uuid",
    "name": "string",
    "asset_type": "string",
    "environment": "string",
    "criticality": "string",
    "owner": "string | null",
    "why_exists": "string | null"
  },
  "upstream": [
    { "id": "uuid", "name": "string", "asset_type": "string", "relationship_type": "string", "depth": 1 }
  ],
  "downstream": [
    { "id": "uuid", "name": "string", "asset_type": "string", "relationship_type": "string", "depth": 1, "criticality": "string" }
  ],
  "downstream_risk": { "critical": 2, "high": 3, "total": 5 },
  "recent_crs": [
    { "id": "uuid", "title": "string", "status": "string", "created_at": "iso8601" }
  ],
  "open_findings_count": 0
}
```

**Error cases:**
- `asset_id` missing → 422
- Asset not found or belongs to different org → 404

### Backend Implementation

**New file:** `backend/app/routers/impact_simulation.py`

Registered on the main app as `/impact-simulation`.

Logic:
1. Fetch asset by `asset_id` + `organization_id`. Return 404 if not found.
2. Call `get_upstream(db, asset_id, org_id, max_depth=3)` from `asset_graph` service.
3. Call `get_downstream(db, asset_id, org_id, max_depth=3)` from `asset_graph` service. For each downstream node, fetch its `criticality` from the Asset table (batch query by IDs).
4. Query `ChangeRequest` table: `target_asset_ids` contains `asset_id` string, order by `created_at DESC`, limit 5.
5. Compute `downstream_risk` by counting criticality values in the downstream list.
6. Return assembled response. `open_findings_count` is always `0` for now.

**Key decision — criticality on downstream nodes:** `get_downstream` does not return criticality (it was built before this need). Rather than modify the service (which would be a breaking change for the asset graph page), the router fetches criticality in a single batch `SELECT id, criticality FROM assets WHERE id IN (...)` after the BFS completes.

**Key decision — CR search by asset_id:** `target_asset_ids` is a JSON array of UUID strings. SQLAlchemy cast + JSON contains is DB-specific. Use a simpler approach: `cast(ChangeRequest.target_asset_ids, Text).contains(str(asset_id))`. This is a substring match which is safe since UUIDs are long and unique. If this proves fragile, use a proper JSON-contains expression for Postgres (`@>` operator).

### Frontend Implementation

**File:** `frontend/src/pages/ImpactSimulationPage.tsx` — full replacement.

**Layout:**
```
[Page header: "Impact Simulation" — no Preview badge]
[Asset search combobox: text input → calls GET /assets?q={query} → dropdown of results]
─────────────────────────────────────────────────────
[When asset selected:]
  [Asset summary card: name, type, env, criticality, owner, why_exists]
  [Risk summary badge: "N critical, N high downstream"]
  
  [Grid: 3 cards]
  ┌─────────────────────┬──────────────────────┬────────────────────┐
  │ Downstream Impact   │ Upstream Dependencies │ Recent Changes     │
  │ (grouped by crit.)  │ (flat list)           │ (last 5 CRs)       │
  └─────────────────────┴──────────────────────┴────────────────────┘

[Empty state: "Select an asset to see its blast radius" with Zap icon]
```

**State management:**
- `searchQuery` string — debounced 300ms before API call
- `selectedAsset` — the asset object chosen from dropdown
- `blastRadius` — result of GET /impact-simulation?asset_id=

**API calls:**
- Asset search: `GET /assets?q={query}` (existing endpoint, limit display to 8 results)
- Blast radius: `GET /impact-simulation?asset_id={id}` (new endpoint)

**Criticality colors** (reuse pattern from existing pages):
- critical → red-500
- high → orange-500
- medium → yellow-500
- low → slate-400

---

## Part B: Lifecycle Nav Reorganization

### Current Structure

The sidebar has:
- **Flat top group:** Dashboard, Change Requests, Projects, Assets, Connectors
- **Operations section (collapsible):** Runbooks, Scheduled Ops, Maintenance Windows, Backup & Recovery
- **Compliance & Identity section (collapsible):** Findings & Remediation, Access Reviews, Compliance
- **Preview section (collapsible):** Impact Simulation, Recommendations, Infrastructure Memory

### New Structure

The repositioning frames Nexplane around 5 lifecycle phases. The sidebar reorganization adds section labels to reflect this without removing or reordering items beyond what the mapping requires.

**Mapping decisions:**
- Impact Simulation moves out of Preview (it's now real) → Observe
- Infrastructure Memory moves out of Preview → Discover
- Recommendations stays in Preview (still forward-looking)
- The old flat top group is split: Assets + Infrastructure Memory → Discover; Change Requests + Projects → Plan & Approve; Connectors → Execute
- Dashboard stays at top outside any section (it's a meta view)
- Incident Response is not in the nav yet; placeholder in Execute section comment

**New nav grouping:**

```
Dashboard                          (no section, always top)
─────────────────────────────────
DISCOVER
  Assets
  Infrastructure Memory
─────────────────────────────────
PLAN & APPROVE
  Change Requests  [badge]
  Projects
─────────────────────────────────
EXECUTE
  Connectors
  Runbooks
  Scheduled Ops
─────────────────────────────────
OBSERVE
  Maintenance Windows
  Impact Simulation
─────────────────────────────────
RECOVER
  Backup & Recovery
─────────────────────────────────
COMPLIANCE
  Findings & Remediation
  Access Reviews
  Compliance
─────────────────────────────────
PREVIEW
  Recommendations  [Preview badge]
─────────────────────────────────
  [DemoOrgSwitcher]
─────────────────────────────────
  Notifications  [badge]
  Settings
```

**Implementation approach:**
- Remove the existing `topNavItems`, `operationsNavItems`, `complianceNavItems` arrays.
- Introduce per-section arrays that match the new grouping.
- Add `useState` for each new collapsible section: `discoverOpen`, `planOpen`, `executeOpen`, `observeOpen`, `recoverOpen`, `complianceOpen`, `previewOpen` — all default `true`.
- Section headers use the existing `SectionHeader` component unchanged.
- Icon choices: Discover→`Search`, Plan→`GitBranch`, Execute→`Play`, Observe→`Eye`, Recover→`RotateCcw`, Compliance→`Lock` (existing), Preview→`Zap` (existing).

---

## Testing Strategy

**Backend — unit tests:**
- `test_impact_simulation.py` using `pytest-asyncio` + `httpx.AsyncClient`
- Test: 200 response with valid asset_id
- Test: 404 for unknown asset
- Test: 422 for missing asset_id
- Test: downstream_risk counts correctly
- Test: recent_crs limited to 5

**Frontend — manual smoke:**
- Search for an asset in demo org → confirm dropdown populates
- Select asset → confirm cards render
- Asset with no relationships → confirm empty upstream/downstream cards
- Asset with CRs → confirm recent_crs card shows correct titles

**No live infrastructure required** — impact simulation is read-only against existing DB data (asset graph edges already seeded by sub-project 2/3).

---

## Open Questions / Deferred

- `open_findings_count`: Return 0 for now. Wire up when a Finding model is added.
- Graph visualization: Deferred. Text list cards are the MVP.
- "What-if" simulation (model a specific change type): Deferred to sub-project 6 or later.
- Incident Response nav item: Not in the nav yet; add when the IR page is built.
