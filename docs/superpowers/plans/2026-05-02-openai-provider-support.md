# OpenAI Provider Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire up OpenAI as a fully functional AI provider so that operators who set OpenAI as their default actually get OpenAI responses, and make the model configurable per provider via the Settings UI.

**Architecture:** `_resolve_api_key` in `ai_service.py` is replaced by `_resolve_provider_config` returning `(provider, api_key, model)`. The `chat()` method branches on provider name to use either the Anthropic or OpenAI SDK. The encrypted providers blob gains an optional `model` field per provider. The Settings UI adds an optional model input to the provider edit form.

**Tech Stack:** Python/FastAPI, `anthropic` SDK (already installed), `openai` SDK (new), React/TypeScript

---

## File Map

**Modified:**
- `backend/requirements.txt` — add `openai>=1.0.0`
- `backend/app/schemas/credential.py` — add `model: str | None` to `AIProviderInfo` and `AIProviderWrite`
- `backend/app/routers/settings.py` — read/write `model` in provider blob; return in GET response
- `backend/app/services/ai_service.py` — replace `_resolve_api_key` with `_resolve_provider_config`; branch `chat()` on provider; update system prompt change_type list
- `backend/app/routers/projects.py` — call `_resolve_provider_config`; pass `provider` + `model` to `chat()`
- `frontend/src/types/api.ts` — add `model?: string` to `AIProviderInfo`
- `frontend/src/pages/Settings.tsx` — add model input to provider edit form; show active model in provider row

**New test file:**
- `backend/app/tests/test_ai_provider.py`

---

### Task 1: Add openai to requirements and schemas

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `backend/app/schemas/credential.py`

- [ ] **Step 1: Add openai to requirements.txt**

Open `backend/requirements.txt` and add this line:
```
openai>=1.0.0
```

- [ ] **Step 2: Install in the running container**

```bash
docker compose exec backend pip install openai>=1.0.0
```
Expected: `Successfully installed openai-...`

- [ ] **Step 3: Update AIProviderInfo and AIProviderWrite in credential.py**

Replace the two classes in `backend/app/schemas/credential.py`:

```python
class AIProviderInfo(BaseModel):
    configured: bool
    model: str | None = None


class AIProviderWrite(BaseModel):
    api_key: str
    model: str | None = None
```

- [ ] **Step 4: Verify schemas import cleanly**

```bash
docker compose exec backend python -c "from app.schemas.credential import AIProviderInfo, AIProviderWrite; print('ok')"
```
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt backend/app/schemas/credential.py
git commit -m "feat: add openai>=1.0.0 to requirements and add model field to AI provider schemas"
```

---

### Task 2: Update settings router to read/write model

**Files:**
- Modify: `backend/app/routers/settings.py`

The `GET /settings/ai-providers` route builds `AIProviderInfo` objects but never reads the `model` field from the blob. The `PUT /settings/ai-providers/{provider}` route stores only `api_key` and discards `model`. Both need updating.

- [ ] **Step 1: Write failing test**

Create `backend/app/tests/test_ai_provider.py`:

```python
import pytest
from httpx import AsyncClient
from app.main import app


@pytest.mark.asyncio
async def test_set_provider_stores_model(auth_client):
    resp = await auth_client.put(
        "/settings/ai-providers/anthropic",
        json={"api_key": "sk-ant-test-key-for-model-test", "model": "claude-opus-4-7"},
    )
    assert resp.status_code == 200

    get_resp = await auth_client.get("/settings/ai-providers")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["providers"]["anthropic"]["model"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_set_provider_without_model_returns_none(auth_client):
    resp = await auth_client.put(
        "/settings/ai-providers/anthropic",
        json={"api_key": "sk-ant-test-key-no-model"},
    )
    assert resp.status_code == 200

    get_resp = await auth_client.get("/settings/ai-providers")
    data = get_resp.json()
    assert data["providers"]["anthropic"]["model"] is None
```

- [ ] **Step 2: Run to verify they fail**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/test_ai_provider.py -v 2>&1 | tail -10"
```
Expected: both tests fail (model not stored/returned yet).

- [ ] **Step 3: Update PUT /ai-providers/{provider} to store model**

In `backend/app/routers/settings.py`, find the `set_ai_provider` function. The line that writes the provider entry currently is:

```python
providers_data.setdefault("providers", {})[provider] = {"api_key": body.api_key}
```

Replace it with:

```python
entry = {"api_key": body.api_key}
if body.model:
    entry["model"] = body.model
providers_data.setdefault("providers", {})[provider] = entry
```

- [ ] **Step 4: Update GET /ai-providers to return model**

In `backend/app/routers/settings.py`, find the `get_ai_providers` function. The dict comprehension that builds `providers` currently is:

```python
providers = {
    name: AIProviderInfo(configured=bool(info.get("api_key")))
    for name, info in providers_data.get("providers", {}).items()
}
```

Replace it with:

```python
providers = {
    name: AIProviderInfo(
        configured=bool(info.get("api_key")),
        model=info.get("model"),
    )
    for name, info in providers_data.get("providers", {}).items()
}
```

- [ ] **Step 5: Run tests**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/test_ai_provider.py -v 2>&1 | tail -10"
```
Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/settings.py backend/app/tests/test_ai_provider.py
git commit -m "feat: store and return model field per AI provider in settings"
```

---

### Task 3: Replace _resolve_api_key with _resolve_provider_config

**Files:**
- Modify: `backend/app/services/ai_service.py`

- [ ] **Step 1: Write failing test**

Append to `backend/app/tests/test_ai_provider.py`:

```python
def test_resolve_provider_config_returns_tuple():
    from app.services.ai_service import _resolve_provider_config
    from app.services.secrets_service import SecretsService
    import json

    svc = SecretsService("test-secret-key-32-chars-padding!!")

    class FakeSettings:
        anthropic_api_key_encrypted = None
        ai_providers_encrypted = svc.encrypt_json({
            "default": "openai",
            "providers": {
                "openai": {"api_key": "sk-test", "model": "gpt-4o-mini"},
            },
        })

    provider, api_key, model = _resolve_provider_config(FakeSettings(), svc)
    assert provider == "openai"
    assert api_key == "sk-test"
    assert model == "gpt-4o-mini"


def test_resolve_provider_config_defaults_model_for_anthropic():
    from app.services.ai_service import _resolve_provider_config
    from app.services.secrets_service import SecretsService

    svc = SecretsService("test-secret-key-32-chars-padding!!")

    class FakeSettings:
        anthropic_api_key_encrypted = None
        ai_providers_encrypted = svc.encrypt_json({
            "default": "anthropic",
            "providers": {"anthropic": {"api_key": "sk-ant-test"}},
        })

    provider, api_key, model = _resolve_provider_config(FakeSettings(), svc)
    assert provider == "anthropic"
    assert api_key == "sk-ant-test"
    assert model == "claude-sonnet-4-6"


def test_resolve_provider_config_falls_back_to_legacy_key():
    from app.services.ai_service import _resolve_provider_config
    from app.services.secrets_service import SecretsService

    svc = SecretsService("test-secret-key-32-chars-padding!!")

    class FakeSettings:
        ai_providers_encrypted = None
        anthropic_api_key_encrypted = svc.encrypt("sk-ant-legacy")

    provider, api_key, model = _resolve_provider_config(FakeSettings(), svc)
    assert provider == "anthropic"
    assert api_key == "sk-ant-legacy"
    assert model == "claude-sonnet-4-6"
```

- [ ] **Step 2: Run to verify they fail**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/test_ai_provider.py::test_resolve_provider_config_returns_tuple -v 2>&1 | tail -10"
```
Expected: FAIL — `ImportError: cannot import name '_resolve_provider_config'`

- [ ] **Step 3: Replace _resolve_api_key with _resolve_provider_config in ai_service.py**

In `backend/app/services/ai_service.py`, replace the `_resolve_api_key` function entirely with:

```python
_PROVIDER_DEFAULTS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
}


def _resolve_provider_config(settings, secrets_svc: SecretsService) -> tuple[str, str, str]:
    """Returns (provider, api_key, model)."""
    if settings.ai_providers_encrypted:
        data = secrets_svc.decrypt_json(settings.ai_providers_encrypted)
        provider = data.get("default", "anthropic")
        providers = data.get("providers", {})
        if provider in providers and providers[provider].get("api_key"):
            api_key = providers[provider]["api_key"]
            model = providers[provider].get("model") or _PROVIDER_DEFAULTS.get(provider, "gpt-4o")
            return provider, api_key, model
    if settings.anthropic_api_key_encrypted:
        return "anthropic", secrets_svc.decrypt(settings.anthropic_api_key_encrypted), _PROVIDER_DEFAULTS["anthropic"]
    raise ValueError("No AI provider configured")
```

- [ ] **Step 4: Run resolver tests**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/test_ai_provider.py -v -k 'resolve' 2>&1 | tail -10"
```
Expected: 3 resolver tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai_service.py backend/app/tests/test_ai_provider.py
git commit -m "feat: replace _resolve_api_key with _resolve_provider_config returning (provider, api_key, model)"
```

---

### Task 4: Branch chat() on provider, update projects router

**Files:**
- Modify: `backend/app/services/ai_service.py`
- Modify: `backend/app/routers/projects.py`

- [ ] **Step 1: Update chat() signature and add provider branching**

In `backend/app/services/ai_service.py`, replace the entire `chat()` method:

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
    system_prompt = self._build_system_prompt(project_goal, asset_context)
    messages = [{"role": m["role"], "content": m["content"]} for m in conversation]

    if provider == "openai":
        import openai
        client = openai.AsyncOpenAI(api_key=api_key)
        oai_messages = [{"role": "system", "content": system_prompt}] + messages
        response = await client.chat.completions.create(
            model=model,
            max_tokens=2048,
            messages=oai_messages,
        )
        reply_text = response.choices[0].message.content
    else:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        )
        reply_text = response.content[0].text

    proposed_crs = self._parse_proposal(reply_text)
    clean_reply = self._strip_proposal_tags(reply_text)
    return {"reply": clean_reply, "proposed_crs": proposed_crs}
```

- [ ] **Step 2: Update the system prompt change_type list**

In `backend/app/services/ai_service.py`, find the `<nexplane-proposal>` block in `_SYSTEM_PROMPT_TEMPLATE`. Replace the `change_type` line:

```
    "change_type": "dns_update|snapshot_asset|security_group_update|key_rotation|telemetry_agent_deploy|remote_command|microsegmentation_policy",
```

With:

```
    "change_type": "dns_update|snapshot_asset|security_group_update|key_rotation|telemetry_agent_deploy|remote_command|microsegmentation_policy|ec2_stop|ec2_start|ec2_reboot|ec2_stop_start|ec2_launch|ec2_terminate",
```

- [ ] **Step 3: Update projects router to use _resolve_provider_config**

In `backend/app/routers/projects.py`, find the AI chat route section. Replace:

```python
from app.services.ai_service import _resolve_api_key
secrets = SecretsService(app_settings.SECRET_KEY)
api_key = _resolve_api_key(org_settings, secrets)
```

With:

```python
from app.services.ai_service import _resolve_provider_config
secrets = SecretsService(app_settings.SECRET_KEY)
provider, api_key, model = _resolve_provider_config(org_settings, secrets)
```

Then replace the `ai_service.chat(...)` call:

```python
result_dict = await ai_service.chat(
    api_key=api_key,
    conversation=conversation,
    project_goal=project.goal or project.name,
    asset_context=asset_context,
)
```

With:

```python
result_dict = await ai_service.chat(
    provider=provider,
    api_key=api_key,
    model=model,
    conversation=conversation,
    project_goal=project.goal or project.name,
    asset_context=asset_context,
)
```

- [ ] **Step 4: Also update the 402 check in projects router**

The check currently gates on `anthropic_api_key_encrypted` or `ai_providers_encrypted`. Replace it to be provider-agnostic:

```python
if not org_settings or (not org_settings.anthropic_api_key_encrypted and not org_settings.ai_providers_encrypted):
    raise HTTPException(
        status_code=402,
        detail="AI not configured — add an AI provider API key in Settings",
    )
```

This line is already correct — no change needed. Just verify it's still there after your edits.

- [ ] **Step 5: Run full backend test suite**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/ -q --tb=short 2>&1 | tail -10"
```
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ai_service.py backend/app/routers/projects.py
git commit -m "feat: branch chat() on provider for OpenAI/Anthropic, update system prompt with EC2 change types"
```

---

### Task 5: Frontend — model field in Settings UI

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/pages/Settings.tsx`

- [ ] **Step 1: Add model to AIProviderInfo type**

In `frontend/src/types/api.ts`, replace:

```typescript
export interface AIProviderInfo {
  configured: boolean;
}
```

With:

```typescript
export interface AIProviderInfo {
  configured: boolean;
  model?: string;
}
```

- [ ] **Step 2: Add modelInput state to Settings component**

In `frontend/src/pages/Settings.tsx`, find the existing state declarations near the top of the `Settings` function. Add one new state variable after `apiKeyInput`:

```typescript
const [modelInput, setModelInput] = useState("");
```

- [ ] **Step 3: Update setProviderMutation to include model**

In `frontend/src/pages/Settings.tsx`, replace the `setProviderMutation` mutationFn:

```typescript
const setProviderMutation = useMutation({
  mutationFn: ({ provider, key }: { provider: string; key: string }) =>
    apiClient.put(`/settings/ai-providers/${provider}`, {
      api_key: key,
      ...(modelInput.trim() && { model: modelInput.trim() }),
    }),
  onSuccess: () => {
    refetchAIProviders();
    setEditingProvider(null);
    setApiKeyInput("");
    setModelInput("");
  },
});
```

- [ ] **Step 4: Add model input to the edit form and show active model**

In `frontend/src/pages/Settings.tsx`, find the `isEditing` block inside the provider map. Currently it renders:

```tsx
{isEditing && (
  <div className="flex gap-2 mt-2">
    <input
      type="password"
      value={apiKeyInput}
      onChange={(e) => setApiKeyInput(e.target.value)}
      placeholder={provider === "anthropic" ? "sk-ant-..." : "sk-..."}
      className="flex-1 border border-gray-300 rounded px-2 py-1 text-xs focus:ring-1 focus:ring-indigo-500"
      autoFocus
    />
    <button
      onClick={() => setProviderMutation.mutate({ provider, key: apiKeyInput })}
      disabled={setProviderMutation.isPending || !apiKeyInput}
      className="text-xs bg-indigo-600 text-white px-3 py-1 rounded hover:bg-indigo-700 disabled:opacity-50"
    >
      Save
    </button>
    <button onClick={() => setEditingProvider(null)} className="text-xs text-gray-500">
      Cancel
    </button>
  </div>
)}
```

Replace it with:

```tsx
{isEditing && (
  <div className="mt-2 space-y-1.5">
    <input
      type="password"
      value={apiKeyInput}
      onChange={(e) => setApiKeyInput(e.target.value)}
      placeholder={provider === "anthropic" ? "sk-ant-..." : "sk-..."}
      className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:ring-1 focus:ring-indigo-500"
      autoFocus
    />
    <input
      type="text"
      value={modelInput}
      onChange={(e) => setModelInput(e.target.value)}
      placeholder={`Model (default: ${provider === "anthropic" ? "claude-sonnet-4-6" : "gpt-4o"})`}
      className="w-full border border-gray-300 rounded px-2 py-1 text-xs focus:ring-1 focus:ring-indigo-500"
    />
    <div className="flex gap-2">
      <button
        onClick={() => setProviderMutation.mutate({ provider, key: apiKeyInput })}
        disabled={setProviderMutation.isPending || !apiKeyInput}
        className="text-xs bg-indigo-600 text-white px-3 py-1 rounded hover:bg-indigo-700 disabled:opacity-50"
      >
        Save
      </button>
      <button onClick={() => { setEditingProvider(null); setModelInput(""); }} className="text-xs text-gray-500">
        Cancel
      </button>
    </div>
  </div>
)}
```

Also add the active model display in the provider info row. Find the line that shows the configured/not-configured status:

```tsx
<span className={`text-xs ${info?.configured ? "text-green-600" : "text-gray-400"}`}>
  {info?.configured ? "● Configured" : "○ Not configured"}
</span>
```

Add the model display immediately after it:

```tsx
<span className={`text-xs ${info?.configured ? "text-green-600" : "text-gray-400"}`}>
  {info?.configured ? "● Configured" : "○ Not configured"}
</span>
{info?.model && (
  <span className="text-xs text-gray-400">· {info.model}</span>
)}
```

- [ ] **Step 5: Restart frontend and verify no errors**

```bash
docker compose stop frontend && docker compose up frontend -d
```

Wait 8 seconds:

```bash
docker compose logs frontend --tail=5 2>&1 | grep -v warning
```
Expected: `VITE v6.4.2  ready` with no errors.

- [ ] **Step 6: Manually verify in browser**

Open http://localhost:3000/settings. In the AI Providers section:
1. Click **"Add key"** or **"Update"** next to Anthropic or OpenAI
2. Confirm a second input appears below the API key field with the model placeholder
3. Enter an API key and leave the model blank → save → confirm the row shows "● Configured" with no model suffix
4. Click Update again, enter a model like `gpt-4o-mini` → save → confirm the row shows "● Configured · gpt-4o-mini"

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/api.ts frontend/src/pages/Settings.tsx
git commit -m "feat: add optional model input to AI provider Settings UI, show active model in provider row"
```

---

### Task 6: Full verification

- [ ] **Step 1: Run full backend test suite**

```bash
docker compose exec backend sh -c "cd /app && python -m pytest app/tests/ -q --tb=short 2>&1 | tail -5"
```
Expected: all tests pass, 0 failures.

- [ ] **Step 2: Rebuild backend image to bake in openai package**

```bash
docker compose build backend && docker compose up backend -d
```

Wait for it to start:
```bash
docker compose logs backend --tail=5 2>&1 | grep -v warning
```
Expected: `Application startup complete.`

- [ ] **Step 3: Smoke test with OpenAI (if key is configured)**

In the browser:
1. Go to Settings → set OpenAI as the default provider
2. Open any draft project → click **"Plan with AI"**
3. Send a message — verify a response arrives (not a 502 error)

- [ ] **Step 4: Smoke test with Anthropic (regression check)**

1. Go to Settings → set Anthropic as the default provider
2. Open a draft project → send a message
3. Verify a response arrives

- [ ] **Step 5: Final git status check**

```bash
git status
```
Expected: clean working tree (no untracked or modified files).
