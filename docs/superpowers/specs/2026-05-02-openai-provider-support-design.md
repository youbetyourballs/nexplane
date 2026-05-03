# OpenAI Provider Support — Design Spec

**Date:** 2026-05-02
**Status:** Approved
**Scope:** Add OpenAI as a fully functional AI provider alongside Anthropic. Make the model configurable per provider via the Settings UI. Update the AI planning system prompt to include EC2 change types.

---

## Background

The Settings page already stores Anthropic and OpenAI API keys in the encrypted `ai_providers_encrypted` blob, and the UI can set either as the default. However, `ai_service.py` always uses the Anthropic SDK regardless of which provider is configured — OpenAI keys are accepted but silently ignored. This spec wires up the OpenAI path.

---

## Design Decisions

- **Model stored per-provider in the encrypted blob** — no new DB columns. The existing `ai_providers_encrypted` JSON gains an optional `"model"` field per provider entry. Falls back to hardcoded defaults (`claude-sonnet-4-6` / `gpt-4o`) when not set.
- **`_resolve_api_key` replaced by `_resolve_provider_config`** — returns `(provider, api_key, model)` so the router can pass all three to `chat()` without re-reading the settings.
- **`chat()` branches on provider** — Anthropic and OpenAI have different SDK clients and message formats (OpenAI's system prompt goes in the messages array; Anthropic takes it as a separate parameter).
- **System prompt updated** — the `<nexplane-proposal>` change_type list is stale; add the 6 EC2 types added in the recent connector expansion.
- **No migration needed** — `ai_providers_encrypted` is a freeform encrypted JSON text field.

---

## Section 1: Data Shape

The encrypted providers blob gains an optional `model` field per provider:

```json
{
  "default": "openai",
  "providers": {
    "anthropic": {"api_key": "sk-ant-...", "model": "claude-sonnet-4-6"},
    "openai":    {"api_key": "sk-...",     "model": "gpt-4o"}
  }
}
```

`model` is optional — omitting it falls back to the provider default.

**Default models:**
- `anthropic` → `claude-sonnet-4-6`
- `openai` → `gpt-4o`

---

## Section 2: Backend Changes

### `backend/app/schemas/credential.py`

`AIProviderInfo` gains an optional `model` field:
```python
class AIProviderInfo(BaseModel):
    configured: bool
    model: str | None = None
```

`AIProviderWrite` gains an optional `model` field:
```python
class AIProviderWrite(BaseModel):
    api_key: str
    model: str | None = None
```

### `backend/app/routers/settings.py`

`GET /ai-providers` — include `model` in each provider's `AIProviderInfo` response (read from the decrypted blob, `None` if not set).

`PUT /ai-providers/{provider}` — accept and store the optional `model` field alongside `api_key` in the encrypted blob.

### `backend/app/services/ai_service.py`

Replace `_resolve_api_key` with `_resolve_provider_config`:

```python
def _resolve_provider_config(settings, secrets_svc: SecretsService) -> tuple[str, str, str]:
    """Returns (provider, api_key, model)."""
    _DEFAULTS = {"anthropic": "claude-sonnet-4-6", "openai": "gpt-4o"}
    if settings.ai_providers_encrypted:
        data = secrets_svc.decrypt_json(settings.ai_providers_encrypted)
        provider = data.get("default", "anthropic")
        providers = data.get("providers", {})
        if provider in providers and providers[provider].get("api_key"):
            api_key = providers[provider]["api_key"]
            model = providers[provider].get("model") or _DEFAULTS.get(provider, "gpt-4o")
            return provider, api_key, model
    if settings.anthropic_api_key_encrypted:
        return "anthropic", secrets_svc.decrypt(settings.anthropic_api_key_encrypted), _DEFAULTS["anthropic"]
    raise ValueError("No AI provider configured")
```

Update `chat()` signature to accept `provider` and `model`:

```python
async def chat(
    self,
    provider: str,
    api_key: str,
    model: str,
    conversation: list[dict],
    project_goal: str,
    asset_context: list[dict],
) -> dict:
```

Branch on `provider` inside `chat()`:

**Anthropic path** (unchanged except model param):
```python
if provider == "anthropic":
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model=model,
        max_tokens=2048,
        system=system_prompt,
        messages=messages,
    )
    reply_text = response.content[0].text
```

**OpenAI path** (new):
```python
elif provider == "openai":
    import openai
    client = openai.AsyncOpenAI(api_key=api_key)
    oai_messages = [{"role": "system", "content": system_prompt}] + messages
    response = await client.chat.completions.create(
        model=model,
        max_tokens=2048,
        messages=oai_messages,
    )
    reply_text = response.choices[0].message.content
```

### `backend/app/routers/projects.py`

Update the AI chat route to use `_resolve_provider_config` and pass `provider` + `model` to `chat()`:

```python
from app.services.ai_service import _resolve_provider_config

provider, api_key, model = _resolve_provider_config(org_settings, secrets)
result_dict = await ai_service.chat(
    provider=provider,
    api_key=api_key,
    model=model,
    ...
)
```

### `backend/requirements.txt`

Add:
```
openai>=1.0.0
```

### System prompt update

In the `<nexplane-proposal>` format block, update the `change_type` enum to include EC2 types:

```
"change_type": "dns_update|snapshot_asset|security_group_update|key_rotation|telemetry_agent_deploy|remote_command|microsegmentation_policy|ec2_stop|ec2_start|ec2_reboot|ec2_stop_start|ec2_launch|ec2_terminate"
```

---

## Section 3: Frontend Changes

### `frontend/src/types/api.ts`

Add `model` to `AIProviderInfo`:
```typescript
export interface AIProviderInfo {
  configured: boolean;
  model?: string;
}
```

### `frontend/src/pages/Settings.tsx`

The provider edit form currently shows one field (API key). Extend it to show an optional model input below the key:

```tsx
{isEditing && (
  <div className="flex gap-2 mt-2">
    <input type="password" placeholder="sk-ant-... / sk-..." ... />
    <input type="text" placeholder={provider === "anthropic" ? "claude-sonnet-4-6" : "gpt-4o"} ... />
    <button>Save</button>
    <button>Cancel</button>
  </div>
)}
```

Model input is optional — if left blank, the backend uses the provider default. If a model is already configured, show it as the placeholder value.

The `setProviderMutation` passes both `api_key` and `model` (empty string omitted):
```typescript
apiClient.put(`/settings/ai-providers/${provider}`, {
  api_key: key,
  ...(modelInput.trim() && { model: modelInput.trim() }),
})
```

The provider row also shows the active model when configured:
```tsx
{info?.model && <span className="text-xs text-gray-400">{info.model}</span>}
```

---

## Files Changed

| File | Change |
|------|--------|
| `backend/app/schemas/credential.py` | Add `model: str | None` to `AIProviderInfo` and `AIProviderWrite` |
| `backend/app/routers/settings.py` | Read/write `model` in provider blob; return in GET response |
| `backend/app/services/ai_service.py` | Replace `_resolve_api_key` with `_resolve_provider_config`; branch `chat()` on provider; update system prompt change_type list |
| `backend/app/routers/projects.py` | Call `_resolve_provider_config`; pass `provider` + `model` to `chat()` |
| `backend/requirements.txt` | Add `openai>=1.0.0` |
| `frontend/src/types/api.ts` | Add `model?: string` to `AIProviderInfo` |
| `frontend/src/pages/Settings.tsx` | Add model input to provider edit form; show active model in provider row |
