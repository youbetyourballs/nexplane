# AI Project Planning — Design Spec (Spec 2b)

**Date:** 2026-04-26  
**Status:** Approved  
**Scope:** Conversational AI planning panel for Projects, per-org Anthropic API key storage, AI chat endpoint, structured change request proposals, and a Settings page for API key management. Builds on Spec 2a (Projects Core).

---

## Problem Statement

Project assembly today is fully manual — operators must know in advance which change requests are needed. For complex initiatives (microsegmentation, large-scale key rotation, multi-environment IP changes), operators lack tooling to reason about scope, sequencing, and affected assets. An AI assistant that asks clarifying questions and proposes a structured change plan dramatically reduces planning time and catches gaps before execution begins.

---

## Design Notes

**Key management forward compatibility:** The `OrganizationSettings` table introduced here stores only the Anthropic API key, but the encryption/decryption path is designed as a `SecretsService` abstraction. Future connector credentials and additional AI provider keys will follow the same interface. This interface is designed to be swappable for external secret stores (HashiCorp Vault, AWS Secrets Manager, HSM, PAM tools) without changing callers — a requirement flagged for future enterprise deployments.

---

## Decisions

- **Synchronous responses** — no streaming. A loading spinner while Claude responds is acceptable for a planning tool.
- **Per-org API key** stored encrypted in a new `OrganizationSettings` table. Never returned to the frontend raw.
- **Encryption:** AES-256 via `cryptography.fernet.Fernet`, key derived from app `SECRET_KEY`. `SecretsService` wraps this so future implementations can delegate to Vault/HSM.
- **Structured output format:** AI writes prose + a `<nexplane-proposal>` JSON block. Backend parses the block separately; frontend renders it as proposal cards.
- **Conversation storage:** `project.ai_context` (new JSON column) holds the full `[{role, content}]` history. Loaded with the project on `GET /projects/{id}`.
- **AI panel placement:** Collapsible right-side panel on the ProjectDetail page, toggled from the project header. Draft projects only.

---

## Section 1: Backend — OrganizationSettings + SecretsService

### 1.1 New DB Model: `OrganizationSettings`

One row per organization, lazy-created on first write.

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID PK | |
| `organization_id` | UUID FK, unique | One-to-one with Organization |
| `anthropic_api_key_encrypted` | Text, nullable | Fernet-encrypted; null = AI not configured |
| `updated_at` | DateTime | |

### 1.2 SecretsService

`backend/app/services/secrets_service.py` — single responsibility: encrypt and decrypt string secrets.

```python
class SecretsService:
    def encrypt(self, value: str) -> str: ...
    def decrypt(self, encrypted: str) -> str: ...
```

Uses `cryptography.fernet.Fernet` with a key derived from `settings.secret_key`. This is the only place encryption logic lives. Future implementations replace this class body with Vault/HSM calls; callers are unchanged.

### 1.3 Pydantic Schemas

```python
class OrgSettingsRead(BaseModel):
    ai_configured: bool
    updated_at: datetime | None

class AIKeyUpdate(BaseModel):
    api_key: str
```

### 1.4 New Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `GET /settings` | get settings | any authenticated user | Returns `{"ai_configured": bool, "updated_at": ...}` |
| `PUT /settings/ai-key` | update key | admin role only | Encrypts and stores the Anthropic API key |

`GET /settings` never returns the raw key — only whether it is configured and when it was last updated.

### 1.5 Migration 004

Single migration file `004_add_ai_settings.py` adds:
- New `organization_settings` table with all columns from 1.1
- `ai_context` JSON column to the existing `projects` table (nullable=False, server_default="[]")

Both changes are in one migration to keep the revision chain clean.

---

## Section 2: Backend — AIService + AI Chat Endpoint

### 2.1 AIService

`backend/app/services/ai_service.py`

```python
class AIService:
    def __init__(self, secrets: SecretsService): ...

    async def chat(
        self,
        api_key: str,
        conversation: list[dict],     # [{role, content}, ...]
        project_goal: str,
        asset_context: list[dict],    # [{name, asset_type, environment, tags}, ...]
    ) -> dict:
        # Returns: {"reply": str, "proposed_crs": list[dict] | None}
```

**System prompt** (constructed per request):

```
You are a planning assistant for Nexplane, a secure infrastructure change management platform.

The operator is planning a project with this goal: {goal}

The following assets are available in their environment:
{asset_context_formatted}

Your job:
1. Ask targeted clarifying questions ONE AT A TIME to understand scope, affected assets, risk tolerance, and sequencing constraints. Reference the available assets by name.
2. When you have enough information to propose a change plan, write your proposal in prose and then append a structured block using this exact format:

<nexplane-proposal>
[
  {
    "title": "Short descriptive title",
    "change_type": "dns_update|snapshot_asset|security_group_update|key_rotation|telemetry_agent_deploy|remote_command|microsegmentation_policy",
    "suggested_assets": ["asset name 1", "asset name 2"],
    "desired_outcome_sketch": {},
    "notes": "Optional sequencing or dependency notes"
  }
]
</nexplane-proposal>

Only include the <nexplane-proposal> block when you are ready to propose the full plan. Do not include it in clarifying question responses.
```

**Response parsing:** After receiving Claude's response, `AIService` scans for `<nexplane-proposal>...</nexplane-proposal>`. If found, it parses the JSON and returns it as `proposed_crs`. The tags and JSON block are stripped from `reply` before returning — the prose reply is clean.

**Claude model:** `claude-sonnet-4-6` (matches the system's current model, configurable via env var `AI_MODEL`).

### 2.2 AI Chat Endpoint

`POST /projects/{id}/ai/chat`

Body: `{"message": "string"}`

Steps:
1. Load project (must belong to caller's org)
2. Check `OrganizationSettings` — if no key configured, return `402` with `{"detail": "AI not configured — add an Anthropic API key in Settings"}`
3. Decrypt API key via `SecretsService`
4. Load org assets for context (name, asset_type, environment, tags)
5. Append user message to `project.ai_context`
6. Call `AIService.chat(api_key, conversation, project.goal, asset_context)`
7. Append AI reply to `project.ai_context`
8. Save updated `ai_context` to DB
9. Return `{"reply": str, "proposed_crs": list | null}`

Returns `{"reply": str, "proposed_crs": list | null}`.

### 2.3 Project Model Update

Add `ai_context` column to `Project`:

```python
ai_context: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
```

`GET /projects/{id}` already returns the full project — add `ai_context: list[dict]` to `ProjectDetailRead` schema so the frontend loads existing conversation on page load.

---

## Section 3: Frontend — Settings Page + AI Panel

### 3.1 Settings Page

**Route:** `/settings` → `Settings.tsx`  
**Sidebar:** Add `Settings` link (gear icon) at the bottom of the nav, above Sign out.  
**Access:** All authenticated users can view; only admins can update the API key.

**Layout:**

```
PageHeader: "Settings"

┌─ AI Configuration ────────────────────────────────┐
│  Anthropic API Key                                 │
│  Status: Configured ✓  Last updated: Apr 26        │
│  [Update Key]                                      │
│                                                    │
│  // When "Update Key" is clicked:                  │
│  [sk-ant-...________________] [Save]  [Cancel]     │
│  Key is encrypted at rest and never displayed.     │
└────────────────────────────────────────────────────┘
```

`GET /settings` on mount. `PUT /settings/ai-key` on Save. Admins only see the Update button. Non-admins see the status only.

### 3.2 AI Panel on ProjectDetail

**Trigger:** An **✦ AI Assistant** button appears in the project header for draft projects. Clicking it toggles the panel open/closed.

**Layout when open:** The page becomes a two-column layout:
- Left (~60%): existing member list
- Right (~40%): AI panel

**AI panel structure:**

```
[✦ AI Assistant]                        [×]
─────────────────────────────────────────
Conversation

  You: Microsegmentation for the
  payments subnet

  AI: To build an effective plan, I need
  a few details. Which assets are in
  scope? I see you have assets tagged
  "payments" — should those all be
  included?

  You: Yes, all assets tagged payments
  and infra

  AI: Got it — 8 assets in scope...

─────────────────────────────────────────
[Type a message…]              [Send →]

// If proposed_crs is present:
─────────────────────────────────────────
Proposed Changes

  ① Update firewall policy             [+]
    security_group_update · payments-fw
    "Must complete before IP changes"

  ② Change IP on payments-api-01       [+]
    security_group_update

  [Add all to Project]
─────────────────────────────────────────
```

**Conversation loading:** On `GET /projects/{id}`, `ai_context` is returned and rendered immediately — conversation history persists across page loads.

**Sending a message:**
1. User types and clicks Send
2. User message appended optimistically to the displayed conversation
3. Loading indicator shown in the panel
4. `POST /projects/{id}/ai/chat` called with `{"message": "..."}`
5. AI reply appended; if `proposed_crs` is non-null, the Proposed Changes section renders

**Adding a proposed CR:**
Clicking `[+]` on a proposal card:
1. Calls `POST /change-requests` with title, change_type, and `desired_outcome_sketch` as the desired_outcome (operator can refine on the CR detail page)
2. Calls `POST /projects/{id}/members` to add the new CR to the project
3. Card shows a "✓ Added" state; the member list on the left updates

**"Add all to Project":** Runs the above in sequence for all proposals not yet added.

**Error handling:** If `POST /projects/{id}/ai/chat` returns `402`, the panel shows an inline message: *"AI not configured. Ask an admin to add an Anthropic API key in [Settings]."* (Settings is a link.)

---

## Section 4: Summary of New Files

| File | Purpose |
|------|---------|
| `backend/app/models/org_settings.py` | OrganizationSettings ORM model |
| `backend/app/schemas/org_settings.py` | OrgSettingsRead, AIKeyUpdate schemas |
| `backend/app/services/secrets_service.py` | SecretsService — encrypt/decrypt abstraction |
| `backend/app/services/ai_service.py` | AIService — Claude API integration, prompt construction, response parsing |
| `backend/app/routers/settings.py` | GET /settings, PUT /settings/ai-key |
| `backend/alembic/versions/004_add_ai_settings.py` | Migration: org_settings table + ai_context column on projects |
| `frontend/src/types/api.ts` | Add OrgSettings, AIChatResponse, AIProposedCR types |
| `frontend/src/api/endpoints.ts` | Add settingsApi (get, updateAIKey), add aiChat to projectsApi |
| `frontend/src/pages/Settings.tsx` | Settings page with AI key management |
| `frontend/src/pages/ProjectDetail.tsx` | Modified: add AI panel toggle, panel component, proposal cards |
| `frontend/src/routes/index.tsx` | Add /settings route |
| `frontend/src/components/Sidebar.tsx` | Add Settings nav link |

## Section 5: Modified Files

| File | Change |
|------|--------|
| `backend/app/models/project.py` | Add `ai_context` JSON column |
| `backend/app/schemas/project.py` | Add `ai_context: list[dict]` to `ProjectDetailRead` |
| `backend/app/main.py` | Register settings router |
| `backend/app/routers/projects.py` | Add `POST /projects/{id}/ai/chat` endpoint |
| `backend/requirements.txt` | Add `anthropic` and `cryptography` packages |
