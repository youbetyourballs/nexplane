# MCP Agent-Scoped Tokens — Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a dedicated `AgentToken` model scoped to specific connector types, asset tags, and CR types, so that AI agents and automation can be granted narrow authorization without inheriting a full user's permissions. Add the two missing MCP lifecycle tools (`submit_for_approval`, `get_execution_progress`).

**Architecture:** `AgentToken` is a new model alongside `ApiToken`. The MCP auth layer checks token type: `ApiToken` → full user RBAC; `AgentToken` → scope-intersection enforcement (operation must be within all declared scope constraints). The existing `resolve_mcp_token` function is extended to resolve either type. New endpoint `POST /auth/agent-tokens` (admin only) issues agent tokens. Two new MCP tools added to `change_requests.py`.

**Tech Stack:** FastAPI, SQLAlchemy async, SHA-256 hashing (same as ApiToken), React (Settings page extension)

---

## AgentToken Model

```python
class AgentToken(Base):
    __tablename__ = "agent_tokens"

    id: UUID PK
    organization_id: UUID FK → organizations
    created_by_user_id: UUID FK → users   # admin who issued it
    name: str                              # "Claude Code — infra-readonly"
    token_hash: str                        # SHA-256; raw shown once
    expires_at: Optional[datetime]         # None = non-expiring
    revoked: bool
    last_used_at: Optional[datetime]
    created_at: datetime

    # Scope constraints — empty list = unrestricted for that dimension
    allowed_connector_types: list[str]     # e.g. ["aws", "ssh"] — JSONB column
    allowed_asset_tags: list[str]          # e.g. ["env:staging"] — JSONB column
    allowed_cr_types: list[str]            # e.g. ["patch_packages", "ssm_command"] — JSONB column
    allowed_roles: list[str]               # e.g. ["read"] | ["read", "write"] | ["read", "write", "approve"]
```

### Scope enforcement rules

An MCP tool call is allowed if ALL of the following are true:

1. **connector_type**: if `allowed_connector_types` is non-empty, the target connector's type must be in the list.
2. **asset_tags**: if `allowed_asset_tags` is non-empty, the target asset must have at least one matching tag.
3. **cr_type**: if `allowed_cr_types` is non-empty, the CR type must be in the list.
4. **role**: `allowed_roles` determines which tool categories are accessible:
   - `"read"` — list/get tools only
   - `"write"` — create CR, create finding actions
   - `"approve"` — approve/reject CR tools

Empty constraint list = unconstrained for that dimension. All four dimensions must pass.

---

## API Endpoints

### `POST /auth/agent-tokens` (admin only)

```json
{
  "name": "Claude Code — staging patch only",
  "expires_in_days": 90,
  "allowed_connector_types": ["aws", "ssh"],
  "allowed_asset_tags": ["env:staging"],
  "allowed_cr_types": ["patch_packages"],
  "allowed_roles": ["read", "write"]
}
```

Response includes `token` (raw, shown once) and the `AgentToken` record.

### `GET /auth/agent-tokens` (admin only)

List all agent tokens for the org. Never returns raw token — only hash prefix.

### `DELETE /auth/agent-tokens/{id}` (admin only)

Revoke an agent token.

---

## MCP Auth Extension

Extend `resolve_mcp_token` in `mcp_server.py`:

```python
async def resolve_mcp_token(raw_token: str, db) -> tuple[User | None, AgentToken | None]:
    """
    Returns (user, agent_token). Exactly one is non-None.
    Raises 401 if token is invalid/expired/revoked.
    """
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    # Try ApiToken first
    api_token = await _lookup_api_token(db, token_hash)
    if api_token:
        user = await _get_user(db, api_token.user_id)
        return user, None

    # Try AgentToken
    agent_token = await _lookup_agent_token(db, token_hash)
    if agent_token:
        return None, agent_token

    raise HTTPException(status_code=401, detail="Invalid or revoked token")
```

Each MCP tool that performs a write operation calls `_enforce_agent_scope(agent_token, connector_type=..., asset_tags=..., cr_type=...)` before proceeding. Read-only tools only check `allowed_roles`.

### Scope enforcement helper

```python
def _enforce_agent_scope(
    agent_token: AgentToken | None,
    *,
    connector_type: str | None = None,
    asset_tags: list[str] | None = None,
    cr_type: str | None = None,
    required_role: str = "read",
) -> None:
    if agent_token is None:
        return  # ApiToken — full user RBAC applies elsewhere
    if required_role not in agent_token.allowed_roles:
        raise HTTPException(403, f"Agent token does not have '{required_role}' permission")
    if connector_type and agent_token.allowed_connector_types:
        if connector_type not in agent_token.allowed_connector_types:
            raise HTTPException(403, f"Agent token not authorized for connector type '{connector_type}'")
    if asset_tags and agent_token.allowed_asset_tags:
        if not any(t in agent_token.allowed_asset_tags for t in asset_tags):
            raise HTTPException(403, "Agent token not authorized for any of the asset's tags")
    if cr_type and agent_token.allowed_cr_types:
        if cr_type not in agent_token.allowed_cr_types:
            raise HTTPException(403, f"Agent token not authorized for CR type '{cr_type}'")
```

---

## New MCP Tools

### `submit_for_approval`

```python
@mcp.tool()
async def submit_for_approval(token: str, cr_id: str) -> dict[str, Any]:
    """
    Move a CR from Draft to Awaiting Approval so approvers are notified.
    Use after create_change_request and reviewing get_change_request_plan.
    Returns updated CR status.
    """
```

Maps to `POST /change-requests/{id}/submit` (already exists in the REST router).

### `get_execution_progress`

```python
@mcp.tool()
async def get_execution_progress(token: str, cr_id: str) -> dict[str, Any]:
    """
    Poll execution progress for a CR currently in Executing state.
    Returns completed_steps, total_steps, current_step, percent_complete, and any error messages.
    Call repeatedly until status is 'completed' or 'failed'.
    """
```

Maps to `GET /change-requests/{id}/progress` (already exists).

---

## UI: Settings → Agent Tokens Tab

New tab next to "API Tokens" in Settings. Displays:
- Token name, created by, created at, expires at, last used
- Scope summary: connector types / asset tags / CR types chips
- Revoke button
- "Generate Agent Token" button → modal with scope selectors (multi-select dropdowns from live connector types, asset tags, CR types)

Component: `frontend/src/components/settings/AgentTokenManager.tsx` (mirrors existing `ApiTokenManager.tsx` pattern).

---

## Files to Create/Modify

| File | Action |
|------|--------|
| `backend/app/models/agent_token.py` | New AgentToken model |
| `backend/alembic/versions/0XX_agent_tokens.py` | Migration |
| `backend/app/schemas/agent_token.py` | Pydantic schemas |
| `backend/app/routers/auth.py` | Add POST/GET/DELETE `/auth/agent-tokens` |
| `backend/app/mcp_server.py` | Extend `resolve_mcp_token` to handle AgentToken |
| `backend/app/mcp_tools/change_requests.py` | Add `submit_for_approval`, `get_execution_progress` |
| `backend/app/mcp_tools/context.py` | Add `_enforce_agent_scope` helper |
| `frontend/src/components/settings/AgentTokenManager.tsx` | New UI component |
| `frontend/src/pages/Settings.tsx` | Add Agent Tokens tab |
| `backend/tests/test_agent_tokens.py` | Unit tests |

---

## Testing Strategy

Unit tests: token creation returns raw token once, subsequent lookups use hash, scope enforcement blocks out-of-scope calls, in-scope calls pass, revoked tokens are rejected, `submit_for_approval` maps to correct endpoint, `get_execution_progress` returns step data.

Smoke: no new smoke phase needed — add an agent token step to the existing MCP smoke validation: create an agent token scoped to `["patch_packages"]` only, verify it can call `list_change_types` (read) and `create_change_request` with patch_packages (write), verify it cannot call `create_change_request` with `ssm_command` (blocked by cr_type scope).
