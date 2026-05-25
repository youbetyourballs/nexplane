# Nexplane MCP Server Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Expose Nexplane's full platform capability surface as an MCP server so that AI assistants, human operators, and upstream software can read platform state and drive the CR lifecycle — all within the platform's existing trust model.

**Architecture:** The MCP server is embedded in the existing FastAPI backend process, mounted at `/mcp` via SSE transport. Tools call the same `app/services/` layer the REST routers use. API tokens are the auth mechanism; each token is role-mapped to its generating user so all existing RBAC and org-scoping applies automatically. Write operations that touch external systems always produce Change Requests — the MCP server never reaches past the platform to a connector directly.

**Tech Stack:** FastAPI + `mcp` Python SDK (Anthropic), SQLAlchemy async, SHA-256 token hashing, React (token management UI)

---

## Core Design Principles

1. **MCP server is a transport, not a bypass.** Every write operation that would touch external infrastructure produces a CR in draft state. The CR must be approved and executed through the normal platform lifecycle. The MCP server cannot skip approval gates, maintenance windows, or org scoping.

2. **Tool descriptions are the capability manifest.** When an MCP client connects, it calls `tools/list` and receives every tool's name, description, and parameter schema. These descriptions are what AI models read to understand when and how to use each tool. They must be precise action statements, not developer docs.

3. **Context bundling for planning decisions.** Any tool that could lead an AI to initiate a change automatically includes an asset context bundle in its response. The AI must have enough information to determine the correct change type and affected assets without a second round-trip.

4. **Role-mapped tokens.** API tokens inherit the role of the user who generated them. An analyst's token cannot approve CRs. An admin's token can. No new permission concept — existing RBAC enforces the boundary.

---

## Finding Lifecycle States Reminder

The platform lifecycle that MCP tools operate within:

```
open → exploitability_pending → actionable → remediating → verifying → resolved
                                     ↓
                             risk_accepted | false_positive
```

MCP write tools operate on this lifecycle; they don't bypass it.

---

## Authentication: API Tokens

### Model

```python
class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: UUID
    organization_id: UUID          # FK → organizations
    user_id: UUID                  # FK → users; role inherited from this user
    name: str                      # human label: "Claude Code — dev laptop"
    token_hash: str                # SHA-256 of raw token; raw shown once on creation
    last_used_at: Optional[datetime]
    expires_at: Optional[datetime] # None = non-expiring
    revoked: bool
    created_at: datetime
```

### Token lifecycle

1. User generates token in Nexplane UI (Settings → API Tokens → Generate)
2. Raw token shown **once** — format: `nxp_<32 random hex bytes>`
3. Backend stores SHA-256 hash only; raw token is never persisted
4. MCP client configured with: URL `https://<host>/mcp/sse`, header `Authorization: Bearer nxp_<token>`
5. On each MCP request: hash the incoming token, look up `ApiToken` where `token_hash = hash AND revoked = false AND (expires_at IS NULL OR expires_at > now())`; load linked `User`; inject into tool call context

### Exclusions

Token management itself is **not** exposed via MCP tools — you cannot generate, list, or revoke tokens through the MCP server (circular trust problem). Token CRUD is REST-only (`/api/v1/tokens`).

---

## MCP Server Mounting

```python
# backend/app/mcp_server.py
from mcp.server.fastapi import create_mcp_router

mcp_router = create_mcp_router()  # mounts SSE at /mcp/sse, handles tools/list

# backend/app/main.py
app.include_router(mcp_router, prefix="/mcp")
```

Transport: SSE (server-sent events) at `/mcp/sse`. Compatible with Claude Code, Claude Desktop, and any MCP-compliant client. No new port required.

---

## Tool Domains

### Domain 1: Findings (12 tools)

| Tool | Description | Write? |
|---|---|---|
| `list_findings` | List vulnerability findings for the org. Filter by status, severity, CVE ID, asset ID. Returns summary fields; use get_finding for depth. | No |
| `get_finding` | Get full detail for a single finding including PoC result, verification status, linked CRs, and SLA countdown. | No |
| `update_finding_status` | Update a finding's lifecycle status (e.g. open → accepted_risk). Does not affect external systems. | Nexplane metadata only |
| `assign_finding` | Assign a finding to a user by user ID or email. | Nexplane metadata only |
| `accept_risk` | Mark a finding as risk-accepted with a reason and expiry date. Finding reappears on SLA dashboard at expiry. | Nexplane metadata only |
| `mark_false_positive` | Close a finding as a false positive. No SLA credit. Irreversible without re-ingest. | Nexplane metadata only |
| `trigger_poc_validation` | Trigger a PoC validation run against an asset for this finding. Creates a vuln_poc_validate CR. Returns asset context bundle. | Produces CR |
| `get_poc_result` | Get the current PoC validation result for a finding (exploited / not_exploited / inconclusive / no_poc_available). | No |
| `challenge_exploitability` | Submit an exploitability challenge with a stated reason. Disabled for CISA KEV findings. | Nexplane metadata only |
| `trigger_verification` | Trigger a scanner re-probe to verify remediation. Returns asset context bundle. | Nexplane metadata only |
| `get_verification_result` | Get the latest verification probe result for a finding. | No |
| `list_finding_change_requests` | List all CRs linked to a finding (patch, mitigation, poc_validate, verify) with current status. | No |

---

### Domain 2: Change Requests (10 tools)

All infrastructure-touching write operations produce CRs in `draft` state. The caller must separately approve and execute.

| Tool | Description | Write? |
|---|---|---|
| `list_change_requests` | List CRs for the org. Filter by status, change_type, asset_id. Returns summary fields. | No |
| `get_change_request` | Get full CR detail including plan steps, parameters, approval history, and execution log. Includes asset context bundle. | No |
| `create_change_request` | Create a draft CR for a specific change_type against a target asset. Returns the draft CR and asset context bundle so the AI can validate the plan is appropriate before approving. | Produces CR |
| `get_change_request_plan` | Get the AI-generated plan for a CR — steps, estimated impact, rollback path. Includes asset context bundle. | No |
| `approve_change_request` | Approve a CR. Respects the user's role — a token cannot approve CRs if the linked user lacks approval permission. Cannot approve a CR you created (platform-enforced). | CR lifecycle |
| `reject_change_request` | Reject a CR with a reason. | CR lifecycle |
| `execute_change_request` | Execute an approved CR. Triggers the executor against the target connector. | CR lifecycle |
| `rollback_change_request` | Roll back an executed CR. Uses the CR's stored rollback snapshot. | CR lifecycle |
| `list_change_types` | List all available change types with descriptions and required parameters. Use this to discover what CRs can be created. | No |
| `get_change_type` | Get full schema for a specific change type including all parameters and their types. | No |

---

### Domain 3: Assets (5 tools)

| Tool | Description | Write? |
|---|---|---|
| `list_assets` | List assets for the org. Filter by type, environment, criticality, connector. | No |
| `get_asset` | Get asset record including metadata, tags, and linked connector. | No |
| `get_asset_context` | Get a full planning context bundle for an asset: asset record, installed software (from last discovery), open findings, recent CRs (last 10), recent timeline events (last 20), connected connectors. Use this before creating a CR to ensure the plan is appropriate. | No |
| `list_asset_findings` | List all findings for a specific asset with severity and status. | No |
| `get_asset_timeline` | Get recent change and event history for an asset. | No |

---

### Domain 4: Connectors (5 tools)

| Tool | Description | Write? |
|---|---|---|
| `list_connectors` | List all connectors configured for the org with type and enabled status. | No |
| `get_connector` | Get connector detail including type, last sync time, and enabled status. Credentials are never returned. | No |
| `test_connector` | Test connectivity for a connector. Returns success/failure and latency. | No |
| `get_connector_status` | Get the current operational status of a connector (connected, degraded, unreachable). | No |
| `list_connector_change_types` | List change types available for a specific connector. | No |

---

### Domain 5: Identity (6 tools)

| Tool | Description | Write? |
|---|---|---|
| `list_identities` | List human and non-human identities for the org. Filter by type, source, risk level. | No |
| `get_identity` | Get full identity detail including risk score, linked assets, and active findings. | No |
| `list_identity_findings` | List security findings associated with an identity (stale accounts, over-privilege, orphaned credentials). | No |
| `get_identity_graph` | Get the identity graph for an identity — linked accounts, group memberships, privilege paths. | No |
| `list_access_reviews` | List access review campaigns with status and completion rate. | No |
| `get_access_review` | Get full access review detail including pending decisions. | No |

---

### Domain 6: Runbooks (4 tools)

| Tool | Description | Write? |
|---|---|---|
| `list_runbooks` | List runbooks for the org with trigger type and last execution status. | No |
| `get_runbook` | Get runbook detail including steps and auto-execute setting. | No |
| `execute_runbook` | Execute a runbook against specified targets. Returns execution ID and asset context bundle for the targets. | Produces CRs |
| `get_runbook_execution_status` | Get the current status of a runbook execution including per-step results. | No |

---

## Asset Context Bundle

Returned automatically by: `create_change_request`, `get_change_request`, `get_change_request_plan`, `trigger_poc_validation`, `trigger_verification`, `execute_runbook`.

Also available on demand via `get_asset_context`.

```json
{
  "asset": {
    "id": "...", "hostname": "web-prod-01", "ip_address": "10.0.1.50",
    "os": "Ubuntu 22.04", "environment": "prod", "criticality": "critical",
    "asset_type": "server", "connector_id": "..."
  },
  "installed_software": [
    {"name": "log4j-core", "version": "2.14.1", "package_manager": "maven"}
  ],
  "open_findings": [
    {"id": "...", "cve_id": "CVE-2021-44228", "severity": "critical", "status": "open"}
  ],
  "recent_change_requests": [
    {"id": "...", "change_type": "patch_packages", "status": "executed", "executed_at": "..."}
  ],
  "recent_timeline_events": [
    {"event_type": "cr_executed", "summary": "patch_packages applied", "timestamp": "..."}
  ],
  "connected_connectors": [
    {"id": "...", "connector_type": "nessus", "name": "Nessus prod scanner"}
  ]
}
```

---

## Explicit Exclusions

| Excluded | Reason |
|---|---|
| Token management (`/api/v1/tokens`) | Circular trust — cannot create tokens via MCP |
| Smoke test triggers | Internal testing infrastructure, not platform capability |
| Org provisioning / superuser ops | Cross-org operations are outside MCP trust boundary |
| Alembic migrations / seed data | Infrastructure management, not platform operations |
| Approving your own CRs | Already enforced by platform; MCP inherits the constraint |
| Bypassing maintenance windows | Already enforced by platform; MCP inherits the constraint |
| Connector credentials (read or write) | Credentials never leave the platform |

---

## Files Created or Modified

| File | Change |
|---|---|
| `backend/app/models/api_token.py` | **New** — `ApiToken` model |
| `backend/alembic/versions/056_api_tokens.py` | **New** — migration |
| `backend/app/routers/api_tokens.py` | **New** — token CRUD (generate, list, revoke) |
| `backend/app/mcp_server.py` | **New** — MCP server instance, SSE mounting, token auth dependency |
| `backend/app/mcp_tools/__init__.py` | **New** (empty) |
| `backend/app/mcp_tools/findings.py` | **New** — 12 finding tools |
| `backend/app/mcp_tools/change_requests.py` | **New** — 10 CR tools |
| `backend/app/mcp_tools/assets.py` | **New** — 5 asset tools |
| `backend/app/mcp_tools/connectors.py` | **New** — 5 connector tools |
| `backend/app/mcp_tools/identity.py` | **New** — 6 identity tools |
| `backend/app/mcp_tools/runbooks.py` | **New** — 4 runbook tools |
| `backend/app/main.py` | Mount MCP router at `/mcp` |
| `backend/app/schemas/api_token.py` | **New** — Pydantic schemas for token CRUD |
| `backend/tests/unit/test_mcp_tools.py` | **New** — unit tests for tool auth, context bundling, write→CR enforcement |
| `frontend/src/components/ApiTokenManager.tsx` | **New** — token generate/list/revoke UI |
| `frontend/src/pages/Settings.tsx` | Add API Tokens tab |

---

## Token Management UI

Settings → API Tokens tab:

- **Generate token** — name field + optional expiry date → shows raw token once in a modal with copy button; warns it won't be shown again
- **Token list** — name, created date, last used, expiry, revoke button
- **Revoke** — immediate; any in-flight MCP requests using that token fail with 401

---

## Testing

**Unit tests (`test_mcp_tools.py`):**
- Token auth: valid token → user injected; revoked token → 401; expired token → 401; wrong org → 403
- Write→CR enforcement: `create_change_request` returns a CR in draft state, never executes directly
- Context bundling: `create_change_request` response includes `asset_context` key with all required fields
- Role enforcement: analyst-role token cannot call `approve_change_request`

**Smoke test (no new phase needed):** The existing CR lifecycle smoke phases exercise the same service layer the MCP tools call. MCP-specific integration can be validated by running a smoke script that connects via MCP client and exercises list → get → create CR → approve → execute → rollback against the EC2 platform instance.
