# Spec: Repositioning — Infrastructure Change Control

**Date:** 2026-06-24
**Sub-project:** 1 of 6 (Repositioning series)
**Status:** Approved for implementation

---

## Summary

Reposition Nexplane from "security orchestration / SOAR" to **"the control plane for infrastructure change."** Security remains the wedge, but the product language, README, UI navigation, and MCP tool descriptions are updated to speak primarily to platform and infrastructure engineers.

Scope: copy + UI nav restructure (existing items reframed, stub items added for future capabilities). No new backend features. Lifecycle nav reorganization deferred to sub-project 5 (when Impact Simulation and Recommendations land and can fill those sections).

---

## Positioning Language

### Canonical tagline
> **Nexplane — The control plane for infrastructure change.**

### Sub-tagline / one-liner
> Eliminate uncertainty before every infrastructure change.

### The four questions (used in README hero, onboarding, stub pages, MCP descriptions)
1. What do I have?
2. What will happen if I change it?
3. Who needs to approve it?
4. Can I safely undo it?

### Persona primary → secondary
- **Primary:** Platform engineers, infrastructure engineers, cloud engineers
- **Secondary:** SRE / production engineering, network engineering, security architecture, operations risk, technology operations

### Language rules
| Old | New |
|-----|-----|
| "security teams" | "infrastructure, platform, and security teams" |
| "security orchestration" | "infrastructure change control" |
| SOAR references | Remove entirely |
| "execution layer" | "control plane" |
| "rollback guarantee" | Keep — it's the differentiator |

---

## README Rewrite

### Structure (full rewrite of root `README.md`)

1. **Hero block** — tagline + four questions, one-sentence answer each
2. **What is Nexplane?** — 2 paragraphs. Frame: infrastructure change control platform for teams that need auditability, approval gates, and rollback. Mention security as the wedge use case without leading with it.
3. **Who it's for** — bullet list of 8 personas with one-line description of how each uses the platform:
   - Platform engineering — standardize how infra changes are proposed and executed
   - Infrastructure engineering — eliminate change anxiety with blast radius analysis and rollback
   - Cloud engineering — manage cloud config changes with approval gates and audit trail
   - SRE / production engineering — tie every change to a rollback plan before execution
   - Network engineering — execute and document network changes with pre/post verification
   - Security architecture — harden infrastructure and track remediation through the change lifecycle
   - Technology operations — coordinate and approve cross-team infrastructure changes
   - Operational risk — full audit trail, approval evidence, and rollback records for every change
4. **The change lifecycle** — text diagram of the 8 stages with one-line descriptions:
   ```
   Discover → Understand → Plan → Approve → Execute → Observe → Rollback → Document
   ```
5. **Key capabilities** — bullet list: asset inventory, asset graph (coming soon), impact simulation (coming soon), AI-assisted planning, approval gates, rollback center, infrastructure memory (coming soon), MCP / LLM access, 70+ connectors, full audit trail
6. **MCP / LLM access** — short section: "Nexplane exposes its full change lifecycle via MCP. Connect Claude or any MCP-compatible LLM to plan, approve, execute, and roll back infrastructure changes." Include 3 example prompts.
7. **Quick start** — existing Docker/compose instructions, unchanged
8. **Architecture overview** — existing content, retitled; keep stack table

---

## UI Navigation Changes

### File: `frontend/src/components/Sidebar.tsx`

#### Existing item renames
| Current label | New label | Notes |
|---------------|-----------|-------|
| "Asset Inventory" | "Assets" | Shorter, consistent with product language |
| "Smoke Tests" | — | Moved to Settings; removed from main nav |

#### Existing item reorders
Main nav order:
1. Dashboard
2. Change Requests (keep pending-approval badge)
3. Projects
4. Assets
5. Connectors

#### New stub nav items (Preview section)
Add a new collapsible section below Operations called **"Preview"** with three stub items:
- **Impact Simulation** — icon: `Zap`, route: `/impact-simulation`
- **Recommendations** — icon: `Lightbulb`, route: `/recommendations`  
- **Infrastructure Memory** — icon: `Brain`, route: `/infrastructure-memory`

Each stub item renders a `PreviewBadge` (small amber "Preview" chip) instead of a number badge.

#### Section grouping (visual only, no routing changes)
Rename the "Operations" collapsible section header to stay as "Operations." Add section labels:

- *(unlabeled top)* — Dashboard, Change Requests, Projects, Assets, Connectors
- **Operations** *(collapsible, existing)* — Runbooks, Scheduled Ops, Maintenance Windows, Backup & Recovery, Incident Response
- **Compliance & Identity** *(new collapsible section)* — Access Reviews, Compliance; move Vulnerability → "Findings & Remediation" here
- **Preview** *(new collapsible section, default open)* — Impact Simulation, Recommendations, Infrastructure Memory
- *(bottom strip)* — Notifications, Settings

Smoke Tests removed from bottom strip; accessible via `/settings` or direct URL.

#### Stub pages
Create three minimal stub page components:
- `frontend/src/pages/ImpactSimulationPage.tsx`
- `frontend/src/pages/RecommendationsPage.tsx`
- `frontend/src/pages/InfrastructureMemoryPage.tsx`

Each stub page renders:
- Page title + the relevant question from the four questions
- One-paragraph description of what the feature will do
- "This capability is coming soon" notice with amber badge
- Link back to the relevant existing alternative (e.g., Impact Simulation → "In the meantime, use Asset details to explore blast radius")

Register routes in `frontend/src/routes/index.tsx`.

---

## MCP Tool Description Updates

### File: `backend/app/mcp_tools/assets.py`

| Tool | New docstring (first line) |
|------|---------------------------|
| `list_assets` | "Enumerate infrastructure assets across connected systems. Use to answer: what do we have?" |
| `get_asset` | "Get full context for an asset including owner, connector source, and recent changes. Use before creating a CR to understand what you're touching." |
| `get_asset_context` | "Get a full planning context bundle for an asset: recent CRs, open findings, timeline, and connector. Use before creating a CR to ensure the plan is appropriate." |
| `list_asset_findings` | "List open security findings for an asset. Use to identify what needs remediation before or after a change." |
| `get_asset_timeline` | "Get ordered change and event history for an asset. Use to answer: what changed recently and who approved it?" |

### File: `backend/app/mcp_tools/change_requests.py`

| Tool | New docstring (first line) |
|------|---------------------------|
| `list_change_types` | "Discover what infrastructure changes are available. Use this first to find the right change_type before creating a CR." |
| `get_change_type` | "Get the full parameter schema for a change type. Use to understand what's required before creating a CR." |
| `list_change_requests` | "Query change history for the org. Use to answer: what changed recently, who approved it, and what's in flight?" |
| `get_change_request` | "Get full CR detail: plan steps, parameters, approval history, and execution log." |
| `get_change_request_plan` | "Get the AI-generated execution plan for a CR — steps, estimated impact, rollback path. Review before approving." |
| `create_change_request` | "Create a draft Change Request for an infrastructure change. The CR is created in draft state — it must be reviewed, approved, and executed separately. This tool NEVER executes a change directly." |
| `approve_change_request` | "Approve a Change Request. Respects role-based permissions — tokens without approval permission are rejected. A user cannot approve a CR they created." |
| `execute_change_request` | "Execute an approved Change Request. Triggers the executor against the target connector. Poll get_change_request for status." |
| `rollback_change_request` | "Roll back an executed Change Request using the stored rollback snapshot. Returns rollback feasibility and steps." |
| `submit_for_approval` | "Move a CR from Draft to Awaiting Approval so approvers are notified. Call after reviewing get_change_request_plan." |

### New tools to add: `backend/app/mcp_tools/assets.py`

**`search_assets(token, query, limit=20)`**
- Query assets by name substring or tag match
- Returns same schema as `list_assets`
- Docstring: "Search assets by name or tag. Use to find assets when you don't know the exact ID."

**`explain_change_request(token, cr_id)`**
- Returns: title, change_type, objective (from description), affected asset names, risk level, lifecycle stage, rollback available, approved_by
- Wraps existing `get_change_request` + plan data into a compact LLM-friendly summary
- Docstring: "Get a compact human-readable summary of a CR suitable for LLM reasoning: what it does, what it touches, the risk level, who approved it, and whether rollback is available."

### MCP server README section update
Add to README section 6 (MCP / LLM access):

Example LLM prompts:
- "List all production assets and identify which have open critical findings"
- "Create a change request to rotate the SSH key on host web-prod-01"
- "What changed on the payroll server in the last 30 days and who approved each change?"
- "Roll back the DNS change from yesterday"
- "What is the rollback plan for CR abc-123?"

---

## Out of Scope for This Sub-Project

- Lifecycle-based nav reorganization (Discover / Plan / Execute / Observe / Rollback sections) — deferred to sub-project 5
- Asset graph UI — sub-project 3
- Infrastructure Memory backend — sub-project 4
- Impact Simulation backend — sub-project 5
- Recommendations backend — sub-project 6
- Incident Response, Vulnerability page renames — minor, included here
- "Findings & Remediation" rename for the Vulnerability nav item — included here

---

## Acceptance Criteria

- [ ] Root README updated with new hero, lifecycle, persona list, and MCP section
- [ ] Sidebar has new grouping: unlabeled top, Operations, Compliance & Identity, Preview
- [ ] "Smoke Tests" removed from main nav
- [ ] "Asset Inventory" renamed to "Assets"
- [ ] "Vulnerability" renamed to "Findings & Remediation"
- [ ] Three stub pages exist with routes registered and descriptions written
- [ ] Preview section renders with amber "Preview" badge on each item
- [ ] All MCP tool docstrings updated per table above
- [ ] `search_assets` and `explain_change_request` MCP tools added and registered
- [ ] Frontend restarts cleanly after changes
