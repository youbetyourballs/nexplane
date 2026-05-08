# AI Asset Context + Prompt Preview — Design Spec

## Goal

Inject the full org asset inventory into the AI planning assistant's context, improve the structured output template so AI proposals contain enough data to create real CRs, and add a side-by-side prompt preview panel so operators can see exactly what is being sent to the AI.

---

## Current State (Gaps)

| Gap | Current behaviour | Target |
|-----|-------------------|--------|
| Asset scope | All org assets are already queried — ✅ | Keep, but improve formatting |
| Asset grouping | Flat list, one line per asset | Grouped by type + environment, condensed |
| Output template | `suggested_assets` (names), `desired_outcome_sketch` (vague) | `target_assets` (names, resolved to IDs by backend), `desired_outcome` (matches executor contract), `seq`, `depends_on` |
| Asset name → ID | Frontend passes `target_asset_ids: []` | Backend resolves names to UUIDs before returning proposed CRs |
| Change type list | Hardcoded short list in prompt | Full list of all valid `ChangeType` enum values |
| Prompt preview | None | Side-by-side panel in AIPanel showing full assembled prompt |

---

## Architecture

Three focused changes:

1. **`ai_service.py`** — rewrite `_build_system_prompt()` to produce a grouped asset context block and an improved output template.
2. **`projects.py`** — after the AI responds, resolve `target_assets` names to UUIDs using the in-memory asset list; add a `GET /projects/{id}/ai/prompt-preview` endpoint that returns the full assembled prompt string without calling the AI.
3. **`AIPanel.tsx`** — add a toggle button that opens a side-by-side preview panel showing the prompt, and update the proposed CR display to show `seq` and `depends_on`.

---

## Backend Changes

### Modified: `backend/app/services/ai_service.py`

#### `_build_system_prompt()` — grouped asset context

Replace the flat asset list with a section grouped by asset type and environment:

```
## Available Assets (N total)

**Servers — Production (6)**
• prod-web-01   server   critical   aws-prod   [web, nginx]
• prod-db-01    server   critical   aws-prod   [db, postgres]
...

**Servers — Staging (3)**
• staging-web-01   server   medium   aws-staging

**Cloud Accounts (2)**
• AWS Production   cloud_account   aws-prod
• AWS Staging      cloud_account   aws-staging

**Other (8)**
• prod-alb-01 (load_balancer, prod) • nexplane-zone.internal (dns_zone) • ...
```

Groups: servers by environment (prod first, then staging, then dev), then cloud_accounts, then "Other" (everything else as a compact comma list).

#### `_SYSTEM_PROMPT_TEMPLATE` — improved output template

Replace the existing `<nexplane-proposal>` format definition with:

```
When you are ready to propose a complete plan, write your explanation as prose first,
then append a structured block in this EXACT format:

<nexplane-proposal>
[
  {
    "seq": 1,
    "title": "Short descriptive title",
    "change_type": "<one of the change types listed above>",
    "target_assets": ["exact asset name 1", "exact asset name 2"],
    "desired_outcome": {
      "dry_run": false
    },
    "depends_on": [],
    "notes": "Optional: sequencing rationale, caveats, rollback notes"
  }
]
</nexplane-proposal>

Rules:
- target_assets must use exact asset names from the Available Assets list above.
- desired_outcome fields depend on change_type — use only fields relevant to the executor.
- seq is a 1-based integer ordering; depends_on lists seq values this CR must follow.
- Only emit <nexplane-proposal> once, when the full plan is ready.
- Include the change type list inline in the prompt (all ChangeType enum values).
```

#### `_parse_proposal()` — updated field names

Change the parsed dict key from `suggested_assets` / `desired_outcome_sketch` to `target_assets` / `desired_outcome`. Keep backward compatibility: fall back to old keys if new ones absent.

#### New method: `build_prompt_preview()`

Extract the system prompt construction into a public method that returns the fully assembled prompt string (system prompt + conversation history + draft message) without calling any AI API. Used by the preview endpoint.

---

### Modified: `backend/app/routers/projects.py`

#### Asset name → UUID resolution

After the AI returns `proposed_crs`, resolve each CR's `target_assets` to UUIDs using the already-loaded `assets` list:

```python
name_to_id = {a.name: str(a.id) for a in assets}
for cr in proposed_crs or []:
    cr["target_asset_ids"] = [
        name_to_id[n] for n in cr.get("target_assets", []) if n in name_to_id
    ]
```

Include the resolved `target_asset_ids` in the returned `proposed_crs` list so the frontend can pass them directly to `changeRequestsApi.create()`.

#### New endpoint: `GET /projects/{project_id}/ai/prompt-preview`

Returns the full assembled prompt string for the current project state. Takes an optional `draft_message` query parameter to include in the preview.

```python
@router.get("/{project_id}/ai/prompt-preview")
async def get_prompt_preview(
    project_id: uuid.UUID,
    draft_message: str | None = Query(None),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Load project, assets, org settings (no AI call)
    # Build conversation with optional draft_message appended
    # Return {"prompt": "<full assembled prompt string>"}
```

#### `AIChatResponse` schema update

Add `target_asset_ids` to each proposed CR dict so the frontend receives resolved UUIDs:

```python
class AIChatResponse(BaseModel):
    reply: str
    proposed_crs: list[dict] | None = None  # each dict now includes target_asset_ids
```

---

## Frontend Changes

### Modified: `frontend/src/components/AIPanel.tsx`

#### Preview toggle button

Add a `showPreview` boolean state. Add a "Prompt" toggle button in the panel header (icon: `FileText` or `Eye`). When active, switches the layout to side-by-side.

#### Side-by-side layout

When `showPreview` is true, the panel renders as a flex row:

```
┌──────────────────────────┬───────────────────────┐
│  Conversation (flex:1)   │  Full Prompt (45%)    │
│                          │  ┌─────────────────┐  │
│  [messages...]           │  │ ## Role         │  │
│                          │  │ ## Assets (24)  │  │
│  [input + Send]          │  │ ## Change Types │  │
│                          │  │ ## Output Fmt   │  │
│                          │  │ ## Conversation │  │
│                          │  │ ## Current msg  │  │
│                          │  └─────────────────┘  │
└──────────────────────────┴───────────────────────┘
```

The preview panel fetches from `GET /projects/{id}/ai/prompt-preview` when first opened and re-fetches when the draft message changes (debounced 500ms). Displayed as a pre-formatted scrollable block with syntax-coloured section headers (`##` lines highlighted).

#### Proposed CR display — seq and depends_on

When `proposed_crs` is non-null, render each CR with its `seq` number and a `depends_on` indicator:

```
┌─────────────────────────────────────────┐
│  1  Harden SSH — prod servers           │
│     agent_ossecurity · prod-web-01 +1   │
│     [+ Add to project]                  │
├─────────────────────────────────────────┤
│  2  Patch prod servers  ← after 1       │
│     agent_linux_patch · prod-web-01 +1  │
│     [+ Add to project]                  │
└─────────────────────────────────────────┘
```

#### target_asset_ids — use resolved UUIDs

Replace `target_asset_ids: []` with the resolved `cr.target_asset_ids` from the API response:

```typescript
const newCr = await changeRequestsApi.create({
  title: cr.title,
  change_type: cr.change_type as ChangeType,
  target_asset_ids: cr.target_asset_ids ?? [],
  desired_outcome: cr.desired_outcome ?? cr.desired_outcome_sketch ?? {},
});
```

### Modified: `frontend/src/api/endpoints.ts`

Add `projectsApi.getPromptPreview()`:

```typescript
getPromptPreview: (projectId: string, draftMessage?: string): Promise<{ prompt: string }> =>
  apiClient
    .get(`/projects/${projectId}/ai/prompt-preview`, {
      params: draftMessage ? { draft_message: draftMessage } : {},
    })
    .then((r) => r.data),
```

---

## Files Affected

| File | Change |
|------|--------|
| `backend/app/services/ai_service.py` | Grouped asset context, improved output template, `build_prompt_preview()` method |
| `backend/app/routers/projects.py` | Asset name→UUID resolution, `GET /ai/prompt-preview` endpoint |
| `frontend/src/components/AIPanel.tsx` | Side-by-side preview panel, seq/depends_on display, resolved target_asset_ids |
| `frontend/src/api/endpoints.ts` | Add `projectsApi.getPromptPreview()` |

---

## What Is Not Changed

- The AI provider resolution, conversation persistence, or error handling — unchanged.
- The `agent_compliance` / `agent_linux_patch` desired_outcome field shapes — the AI is instructed to use executor-appropriate fields; we don't enumerate them all in the template (too verbose).
- Project membership — assets are not tied to projects; the full org inventory is used for context (confirmed in design session).

---

## Future Iteration Notes

- The grouped asset context format and output template will need tuning as we observe real AI responses. The spec is intentionally lean — iterate based on usage.
- `depends_on` is informational in v1 (displayed to the user). Automated ordering of CR creation in the UI is a future enhancement.
- If org asset count exceeds ~200, consider summarising the "Other" group further or adding a per-type count summary line.
