# AI Manifest Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded `_CHANGE_TYPES_TEXT` constant in `ai_service.py` with a dynamic call to the CR manifest, then add a smoke phase that validates LLM proposals contain only manifest CR types.

**Architecture:** `_build_change_types_text()` calls `get_manifest()` (already cached in-memory by `manifest_builder.py`), formats all 390 entries grouped by domain, and is called from `_build_system_prompt()` instead of the hardcoded constant. The smoke phase `AI_MANIFEST_PLAN` sends one chat message to a real project, asserts a `<nexplane-proposal>` block appears, and cross-references every `change_type` against `GET /cr-manifest`.

**Tech Stack:** Python, FastAPI, pytest, `backend/app/services/ai_service.py`, `backend/app/services/manifest_builder.py`, `backend/tests/unit/test_ai_service.py` (new), `backend/tests/smoke/test_aws_live.py`

---

### Task 1: Update `ai_service.py` + unit tests

**Files:**
- Modify: `backend/app/services/ai_service.py` (lines 11–59 remove constant; line 218 update call; add `_build_change_types_text()` after line 59)
- Create: `backend/tests/unit/test_ai_service.py`

- [ ] **Step 1: Write failing unit tests**

Create `backend/tests/unit/test_ai_service.py`:

```python
import pytest
from unittest.mock import patch
from app.services.ai_service import _build_change_types_text, AIService
from app.services.secrets_service import SecretsService


def test_build_change_types_text_contains_manifest_types():
    text = _build_change_types_text()
    assert "configure_selinux" in text
    assert "rotate_iam_key" in text
    assert "## hardening" in text


def test_build_change_types_text_no_hardcoded_remnants():
    text = _build_change_types_text()
    assert "EC2 lifecycle:" not in text
    assert "Agent commands (run directly on hosts" not in text


def test_build_system_prompt_includes_manifest():
    svc = AIService(SecretsService())
    prompt = svc._build_system_prompt("harden nginx", [])
    assert "configure_selinux" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

```
cd /home/ec2-user/nexplane
docker exec nexplane-backend-1 python -m pytest backend/tests/unit/test_ai_service.py -v
```

Expected: FAIL — `_build_change_types_text` not found (ImportError).

- [ ] **Step 3: Add `_build_change_types_text()` and remove the constant**

In `backend/app/services/ai_service.py`:

**Remove** lines 11–59 (the entire `_CHANGE_TYPES_TEXT = """...""".strip()` block).

**Add** this function after the imports (after line 4):

```python
def _build_change_types_text() -> str:
    from app.services.manifest_builder import get_manifest
    from collections import defaultdict

    entries = get_manifest()
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        by_domain[e["domain"]].append(e)

    lines = []
    for domain in sorted(by_domain):
        lines.append(f"\n## {domain}")
        for e in sorted(by_domain[domain], key=lambda x: x["change_type"]):
            touches = ", ".join(e.get("touches") or [])
            preconditions = "; ".join(e.get("preconditions") or [])
            effects = "; ".join(e.get("effects") or [])
            rollback = e.get("rollback_type", "unknown")
            line = (
                f"{e['change_type']}  —  {e['display_name']}"
                f"  |  touches: {touches}"
                f"  |  requires: {preconditions}"
                f"  |  effects: {effects}"
                f"  |  rollback: {rollback}"
            )
            lines.append(line)
    return "\n".join(lines).strip()
```

**Update** `_build_system_prompt()` — change line `change_types=_CHANGE_TYPES_TEXT,` to:

```python
change_types=_build_change_types_text(),
```

- [ ] **Step 4: Run tests to verify they pass**

```
docker exec nexplane-backend-1 python -m pytest backend/tests/unit/test_ai_service.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Run full unit suite to check for regressions**

```
docker exec nexplane-backend-1 python -m pytest backend/tests/unit/ -v --tb=short
```

Expected: all pass.

- [ ] **Step 6: SCP and sync to EC2**

```
# From laptop: all edits go through EC2
scp -i ~/.ssh/id_ed25519 backend/app/services/ai_service.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/app/services/ai_service.py
scp -i ~/.ssh/id_ed25519 backend/tests/unit/test_ai_service.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/unit/test_ai_service.py
```

- [ ] **Step 7: Restart backend so manifest is rebuilt**

```
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39 "cd /home/ec2-user/nexplane && docker compose restart backend"
```

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/ai_service.py backend/tests/unit/test_ai_service.py
git commit -m "feat: replace hardcoded _CHANGE_TYPES_TEXT with dynamic manifest call"
```

---

### Task 2: Add `AI_MANIFEST_PLAN` smoke phase

**Files:**
- Modify: `backend/tests/smoke/test_aws_live.py` — add `run_phase_ai_manifest_plan()` function and wire it into the phases block after `CR_MANIFEST`

**Context:** The smoke test file is large (~22k+ lines). The `CR_MANIFEST` phase function is `run_phase_cr_manifest` near line 22272. The phases dispatch block has `if "CR_MANIFEST" in phases:` as the last entry. The HTTP client pattern is `client.post(f"/projects/{project_id}/ai/chat", json={"message": message})`. The org settings endpoint is `GET /org/settings`.

- [ ] **Step 1: Write the smoke phase function**

Add `run_phase_ai_manifest_plan()` after `run_phase_cr_manifest` (after its closing line):

```python
def run_phase_ai_manifest_plan(client, log):
    log("=== AI_MANIFEST_PLAN ===")

    # 1. Get org settings to verify AI is configured
    r = client.get("/org/settings")
    assert r.status_code == 200, f"GET /org/settings failed: {r.text}"
    settings = r.json()
    ai_configured = settings.get("ai_providers_encrypted") or settings.get("anthropic_api_key_encrypted")
    assert ai_configured, "No AI provider configured — set an API key in org settings before running this phase"

    # 2. Create a smoke project
    project_name = f"smoke-ai-manifest-{int(__import__('time').time())}"
    r = client.post("/projects", json={
        "name": project_name,
        "goal": "Harden the nginx service with SELinux on this Linux host.",
    })
    assert r.status_code in (200, 201), f"POST /projects failed: {r.text}"
    project_id = r.json()["id"]
    log(f"Created project {project_id}")

    # 3. Get a server asset to include in context
    r = client.get("/assets?asset_type=server")
    assert r.status_code == 200, f"GET /assets failed: {r.text}"
    assets = r.json() if isinstance(r.json(), list) else r.json().get("items", [])
    assert assets, "No server assets registered — register at least one server asset before running this phase"
    asset_id = assets[0]["id"]
    log(f"Using asset {asset_id}")

    try:
        # 4. Send one chat message requesting a full plan
        r = client.post(
            f"/projects/{project_id}/ai/chat",
            json={
                "message": "Propose a full plan now. Include all required steps.",
                "asset_ids": [asset_id],
            },
            timeout=120,
        )
        assert r.status_code == 200, f"POST ai/chat failed: {r.text}"
        body = r.json()

        # 5. Assert proposal present
        proposed_crs = body.get("proposed_crs")
        assert proposed_crs, f"No proposed_crs in response — LLM did not emit a <nexplane-proposal> block. Response: {body.get('message', '')[:500]}"
        log(f"Got {len(proposed_crs)} proposed CRs")

        # 6. Load the full manifest vocabulary
        r = client.get("/cr-manifest")
        assert r.status_code == 200, f"GET /cr-manifest failed: {r.text}"
        manifest = r.json()["entries"]
        valid_change_types = {e["change_type"] for e in manifest}
        hardening_types = {e["change_type"] for e in manifest if e["domain"] == "hardening"}

        # 7. Assert no hallucinations — every change_type must exist in manifest
        proposed_types = [cr["change_type"] for cr in proposed_crs if "change_type" in cr]
        hallucinated = [ct for ct in proposed_types if ct not in valid_change_types]
        assert not hallucinated, f"Hallucinated CR types not in manifest: {hallucinated}"
        log(f"All {len(proposed_types)} proposed change_types are valid manifest entries")

        # 8. Assert at least one hardening CR in the plan
        hardening_in_plan = [ct for ct in proposed_types if ct in hardening_types]
        assert hardening_in_plan, (
            f"No hardening-domain CR types in proposal. Proposed: {proposed_types}. "
            f"Hardening types available: {sorted(hardening_types)[:10]}..."
        )
        log(f"Hardening CRs in plan: {hardening_in_plan}")

    finally:
        # 9. Cleanup
        client.delete(f"/projects/{project_id}")
        log(f"Deleted project {project_id}")

    log("AI_MANIFEST_PLAN PASSED ✓")
```

- [ ] **Step 2: Wire into phases dispatch block**

After the `if "CR_MANIFEST" in phases:` block, add:

```python
    if "AI_MANIFEST_PLAN" in phases:
        run_phase_ai_manifest_plan(client, log)
```

Also add `"AI_MANIFEST_PLAN"` to the phases list at the top of the file where the available phases are documented/listed (search for `"CR_MANIFEST"` in that list to find the right location).

- [ ] **Step 3: SCP updated smoke test to EC2**

```
scp -i ~/.ssh/id_ed25519 backend/tests/smoke/test_aws_live.py ec2-user@100.101.186.39:/home/ec2-user/nexplane/backend/tests/smoke/test_aws_live.py
```

- [ ] **Step 4: Run the smoke phase on EC2**

```
ssh -i ~/.ssh/id_ed25519 ec2-user@100.101.186.39
cd /home/ec2-user/nexplane
PHASES=AI_MANIFEST_PLAN pytest backend/tests/smoke/test_aws_live.py -v -s 2>&1 | tee /tmp/ai_manifest_smoke.log
```

Expected output includes:
```
=== AI_MANIFEST_PLAN ===
Created project <id>
Using asset <id>
Got N proposed CRs
All N proposed change_types are valid manifest entries
Hardening CRs in plan: ['configure_selinux', ...]
Deleted project <id>
AI_MANIFEST_PLAN PASSED ✓
```

- [ ] **Step 5: Commit**

```bash
git add backend/tests/smoke/test_aws_live.py
git commit -m "feat: add AI_MANIFEST_PLAN smoke phase — validates LLM proposals use manifest CR types"
```
