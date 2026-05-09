# AI Asset Context + Prompt Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inject the full org asset inventory (grouped, condensed) into the AI planning assistant, improve the structured output template so proposals include enough data to create real CRs with resolved asset IDs, and add a side-by-side prompt preview panel to AIPanel.

**Architecture:** The backend `ai_service.py` gets an improved `_build_system_prompt()` (grouped asset context, richer output template, `build_prompt_preview()` public method) and `_parse_proposal()` updated for new field names. The `projects.py` router resolves `target_assets` names to UUIDs after each AI response and gains a new `GET /projects/{id}/ai/prompt-preview` endpoint. The frontend updates TypeScript types, adds `projectsApi.getPromptPreview()`, and adds a side-by-side preview panel to `AIPanel.tsx`.

**Tech Stack:** Python/FastAPI + SQLAlchemy async (backend), React/TypeScript + @tanstack/react-query (frontend), existing Anthropic/OpenAI client

---

## File Structure

```
backend/app/services/ai_service.py         MODIFY — grouped asset context, richer template, build_prompt_preview()
backend/app/routers/projects.py            MODIFY — name→UUID resolution, GET /ai/prompt-preview endpoint
backend/tests/test_ai_service.py           NEW    — unit tests for ai_service changes
frontend/src/types/api.ts                  MODIFY — update AIProposedCR, add PromptPreviewResponse
frontend/src/api/endpoints.ts             MODIFY — add projectsApi.getPromptPreview()
frontend/src/components/AIPanel.tsx        MODIFY — preview panel, seq/depends_on display, resolved IDs
```

---

## Task 1: Improved System Prompt + Grouped Asset Context

**Files:**
- Modify: `backend/app/services/ai_service.py`
- Create: `backend/tests/test_ai_service.py`

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_ai_service.py`:

```python
import pytest
from app.services.ai_service import AIService, _build_asset_context_text, _SYSTEM_PROMPT_TEMPLATE
from app.services.secrets_service import SecretsService


def _make_asset(name, asset_type, environment, criticality=None, connector_type=None, tags=None):
    return {
        "name": name,
        "asset_type": asset_type,
        "environment": environment,
        "criticality": criticality,
        "connector_type": connector_type,
        "tags": tags or [],
    }


def test_build_asset_context_text_groups_servers_by_environment():
    assets = [
        _make_asset("prod-web-01", "server", "prod", "critical", "aws", ["web"]),
        _make_asset("staging-web-01", "server", "staging", "medium", "aws"),
        _make_asset("prod-db-01", "server", "prod", "critical", "aws", ["db"]),
    ]
    text = _build_asset_context_text(assets)
    assert "Servers — Production (2)" in text
    assert "Servers — Staging (1)" in text
    assert "prod-web-01" in text
    assert "prod-db-01" in text
    assert "staging-web-01" in text
    # Production group should appear before staging
    assert text.index("Production") < text.index("Staging")


def test_build_asset_context_text_groups_cloud_accounts():
    assets = [
        _make_asset("AWS Prod", "cloud_account", "prod", connector_type="aws"),
        _make_asset("GCP Prod", "cloud_account", "prod", connector_type="gcp"),
    ]
    text = _build_asset_context_text(assets)
    assert "Cloud Accounts (2)" in text
    assert "AWS Prod" in text
    assert "GCP Prod" in text


def test_build_asset_context_text_puts_other_types_in_other_group():
    assets = [
        _make_asset("prod-alb-01", "load_balancer", "prod"),
        _make_asset("nexplane.internal", "dns_zone", "prod"),
    ]
    text = _build_asset_context_text(assets)
    assert "Other (" in text
    assert "prod-alb-01" in text
    assert "nexplane.internal" in text


def test_build_asset_context_text_empty_returns_placeholder():
    text = _build_asset_context_text([])
    assert "No assets" in text


def test_build_asset_context_text_includes_tags():
    assets = [_make_asset("prod-web-01", "server", "prod", tags=["nginx", "web"])]
    text = _build_asset_context_text(assets)
    assert "nginx" in text
    assert "web" in text


def test_system_prompt_template_contains_required_sections():
    from app.services.ai_service import AIService
    from unittest.mock import MagicMock
    svc = AIService(MagicMock())
    assets = [_make_asset("prod-web-01", "server", "prod", "critical", "aws")]
    prompt = svc._build_system_prompt("Harden prod servers", assets)
    assert "Harden prod servers" in prompt
    assert "prod-web-01" in prompt
    assert "nexplane-proposal" in prompt
    assert '"seq"' in prompt
    assert '"target_assets"' in prompt
    assert '"depends_on"' in prompt
    assert '"desired_outcome"' in prompt


def test_parse_proposal_handles_new_field_names():
    from app.services.ai_service import AIService
    from unittest.mock import MagicMock
    svc = AIService(MagicMock())
    text = '''
Some explanation.
<nexplane-proposal>
[{"seq": 1, "title": "Harden SSH", "change_type": "agent_ossecurity",
  "target_assets": ["prod-web-01"], "desired_outcome": {"dry_run": false},
  "depends_on": [], "notes": "CIS 5.2"}]
</nexplane-proposal>
'''
    result = svc._parse_proposal(text)
    assert result is not None
    assert result[0]["title"] == "Harden SSH"
    assert result[0]["target_assets"] == ["prod-web-01"]
    assert result[0]["desired_outcome"] == {"dry_run": False}
    assert result[0]["seq"] == 1
    assert result[0]["depends_on"] == []


def test_parse_proposal_handles_old_field_names_for_backward_compat():
    from app.services.ai_service import AIService
    from unittest.mock import MagicMock
    svc = AIService(MagicMock())
    text = '''
<nexplane-proposal>
[{"title": "Old format", "change_type": "ec2_stop",
  "suggested_assets": ["prod-web-01"], "desired_outcome_sketch": {"dry_run": true}}]
</nexplane-proposal>
'''
    result = svc._parse_proposal(text)
    assert result is not None
    assert result[0]["title"] == "Old format"


def test_build_prompt_preview_returns_full_assembled_prompt():
    from app.services.ai_service import AIService
    from unittest.mock import MagicMock
    svc = AIService(MagicMock())
    assets = [_make_asset("prod-web-01", "server", "prod", "critical", "aws")]
    conversation = [{"role": "user", "content": "First message"}]
    preview = svc.build_prompt_preview(
        goal="Harden prod servers",
        asset_context=assets,
        conversation=conversation,
        draft_message="What should I do next?",
    )
    assert "Harden prod servers" in preview
    assert "prod-web-01" in preview
    assert "First message" in preview
    assert "What should I do next?" in preview
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_ai_service.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name '_build_asset_context_text'`

- [ ] **Step 3: Rewrite `backend/app/services/ai_service.py`**

Replace the entire file with:

```python
import json
import re
from app.services.secrets_service import SecretsService


_PROVIDER_DEFAULTS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
}

# All plannable change types grouped for readability in the system prompt
_CHANGE_TYPES_TEXT = """
Agent commands (run directly on hosts via Nexplane Agent):
  agent_ossecurity, agent_linuxauth, agent_linux_patch, agent_linuxupgrade,
  agent_winharden, agent_win_patch, agent_crossplatform, agent_compliance,
  agent_forensics, agent_fleet, agent_backup, agent_reboot, agent_credrotation, agent_iac

EC2 lifecycle: ec2_launch, ec2_stop, ec2_start, ec2_reboot, ec2_stop_start, ec2_terminate
EC2 ops: key_pair_create, ssm_command, snapshot_asset, capture_instance_state

Networking: security_group_update, microsegmentation_policy, dns_update,
  route53_zone_create, route53_record_upsert, route53_record_delete
  alb_create, alb_delete, target_group_create, target_group_delete,
  listener_create, listener_modify, listener_delete, register_targets, deregister_targets

Storage: s3_bucket_create, s3_bucket_delete, s3_lifecycle_configure,
  block_s3_public_access, restore_s3_public_access, put_bucket_policy

IAM / identity: iam_user_create, iam_user_delete, attach_iam_policy, detach_iam_policy,
  disable_iam_user, enable_iam_user, rotate_iam_key, key_rotation,
  offboard_user, onboard_user

Database: rds_instance_create, rds_instance_delete, rds_snapshot_create,
  rds_replica_create, promote_db_replica, provision_db_user, deprovision_db_user

Monitoring: cloudwatch_alarm_create, cloudwatch_alarm_delete

IaC / config: terraform_local_apply, ansible_local_playbook, tag_resource

Tailscale / agent deploy: tailscale_join, tailscale_remove, deploy_nexplane_agent, remove_nexplane_agent

GCE: gce_instance_create, gce_stop, gce_start, gce_instance_reboot, gce_instance_delete, gce_disk_snapshot
GCP ops: gcp_firewall_create, gcp_firewall_delete, gcp_block_public_bucket_access,
  gcp_disable_service_account, gcp_rotate_service_account_key

Azure VM: azure_vm_create, azure_vm_stop, azure_vm_start, azure_vm_reboot, azure_vm_delete, azure_vm_snapshot
Azure ops: azure_update_nsg_rule, azure_restore_nsg_rule, azure_disable_public_blob_access,
  azure_enable_public_blob_access, azure_rotate_storage_key, azure_storage_account_create,
  azure_storage_account_delete, azure_blob_container_create, azure_blob_container_delete,
  azure_managed_identity_create, azure_managed_identity_delete,
  azure_role_assignment_create, azure_role_assignment_delete,
  azure_vnet_create, azure_vnet_delete, azure_dns_zone_create, azure_dns_zone_delete,
  azure_dns_record_create, azure_dns_record_delete,
  azure_sql_server_create, azure_sql_server_delete,
  azure_sql_database_create, azure_sql_database_delete,
  azure_metric_alert_create, azure_metric_alert_delete

Incident response: isolate_host, lockdown_account, preserve_evidence
Backup / DR: create_backup, verify_backup, restore_files, dr_failover, dr_dns_failover_route53
""".strip()


_SYSTEM_PROMPT_TEMPLATE = """You are a planning assistant for Nexplane, a secure infrastructure change management platform.

The operator is planning a project with this goal: {goal}

## Available Assets ({asset_count} total)

{assets_text}

## Agent Capabilities (execution tier 3 — runs directly on hosts via Nexplane Agent)

Linux OS hardening: SELinux/AppArmor/seccomp; CIS sysctl; iptables/nftables; auditd; AIDE/Tripwire; eBPF (Cilium/Falco/Tetragon).
Linux auth: SSH hardening; PAM (lockout, complexity, timeout); CA certificates; NTP. Audits: user misconfigs, SUID, sudo, PwnKit, DirtyPipe.
Windows hardening: LAPS; Credential Guard; AppLocker; SMB hardening; BitLocker; Windows Firewall; SCHANNEL; RDP; audit policy; registry.
Cross-platform: TLS certificates (ACME/internal CA); DNS resolvers (DoH/DoT); software inventory.
Linux upgrade: in-place security/package/dist upgrade with snapshot; containerize-and-migrate workflow.

## Supported Change Types

{change_types}

## Your Job

1. Ask targeted clarifying questions ONE AT A TIME to understand scope, affected assets, risk tolerance, and sequencing. Reference assets by their exact names from the Available Assets list above.
2. When you have enough information, write your explanation as prose first, then append a structured proposal block.

## Output Format (when ready to propose a full plan)

Write human-readable explanation first. Then append:

<nexplane-proposal>
[
  {{
    "seq": 1,
    "title": "Short descriptive title",
    "change_type": "<one of the change types listed above>",
    "target_assets": ["exact asset name from Available Assets"],
    "desired_outcome": {{
      "dry_run": false
    }},
    "depends_on": [],
    "notes": "Optional: sequencing rationale, caveats, dependencies"
  }},
  {{
    "seq": 2,
    "title": "Second change request",
    "change_type": "agent_linux_patch",
    "target_assets": ["exact asset name"],
    "desired_outcome": {{
      "dry_run": false
    }},
    "depends_on": [1],
    "notes": "Run after seq 1 is complete"
  }}
]
</nexplane-proposal>

Rules:
- target_assets must use exact asset names from the Available Assets list above.
- seq is a 1-based integer. depends_on lists seq values this CR must follow.
- Only emit <nexplane-proposal> once, when the full plan is ready.
- Do not include <nexplane-proposal> in clarifying question responses."""


def _build_asset_context_text(assets: list[dict]) -> str:
    """Build a grouped, condensed asset context block for the system prompt."""
    if not assets:
        return "No assets registered yet."

    servers_by_env: dict[str, list[dict]] = {}
    cloud_accounts: list[dict] = []
    other: list[dict] = []

    env_order = ["prod", "staging", "dev"]

    for a in assets:
        atype = a.get("asset_type", "")
        env = a.get("environment", "")
        if atype == "server" or atype == "endpoint":
            servers_by_env.setdefault(env, []).append(a)
        elif atype == "cloud_account":
            cloud_accounts.append(a)
        else:
            other.append(a)

    lines: list[str] = []

    # Servers grouped by environment
    for env in env_order:
        group = servers_by_env.get(env, [])
        if not group:
            continue
        label = env.capitalize()
        lines.append(f"**Servers — {label} ({len(group)})**")
        for a in group:
            parts = [f"• {a['name']}", a.get("asset_type", "server")]
            if a.get("criticality"):
                parts.append(a["criticality"])
            if a.get("connector_type"):
                parts.append(a["connector_type"])
            line = "  " + "   ".join(parts)
            if a.get("tags"):
                line += f"   [{', '.join(a['tags'])}]"
            lines.append(line)
    # Remaining server environments not in env_order
    for env, group in servers_by_env.items():
        if env not in env_order:
            lines.append(f"**Servers — {env.capitalize()} ({len(group)})**")
            for a in group:
                lines.append(f"  • {a['name']}   {a.get('asset_type', 'server')}")

    if cloud_accounts:
        lines.append(f"**Cloud Accounts ({len(cloud_accounts)})**")
        for a in cloud_accounts:
            line = f"  • {a['name']}"
            if a.get("connector_type"):
                line += f"   {a['connector_type']}"
            lines.append(line)

    if other:
        lines.append(f"**Other ({len(other)})**")
        compact = "  " + " • ".join(
            f"{a['name']} ({a.get('asset_type', '?')}, {a.get('environment', '?')})"
            for a in other
        )
        lines.append(compact)

    return "\n".join(lines)


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


class AIService:
    def __init__(self, secrets_service: SecretsService):
        self._secrets = secrets_service

    def _build_system_prompt(self, goal: str, asset_context: list[dict]) -> str:
        assets_text = _build_asset_context_text(asset_context)
        return _SYSTEM_PROMPT_TEMPLATE.format(
            goal=goal,
            asset_count=len(asset_context),
            assets_text=assets_text,
            change_types=_CHANGE_TYPES_TEXT,
        )

    def _parse_proposal(self, text: str) -> list[dict] | None:
        match = re.search(r"<nexplane-proposal>(.*?)</nexplane-proposal>", text, re.DOTALL)
        if not match:
            return None
        try:
            items = json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            return None
        # Normalise field names — support both new (target_assets/desired_outcome)
        # and old (suggested_assets/desired_outcome_sketch) for backward compatibility.
        for item in items:
            if "suggested_assets" in item and "target_assets" not in item:
                item["target_assets"] = item.pop("suggested_assets")
            if "desired_outcome_sketch" in item and "desired_outcome" not in item:
                item["desired_outcome"] = item.pop("desired_outcome_sketch")
            item.setdefault("seq", None)
            item.setdefault("depends_on", [])
            item.setdefault("target_assets", [])
            item.setdefault("desired_outcome", {})
        return items

    def _strip_proposal_tags(self, text: str) -> str:
        return re.sub(
            r"\s*<nexplane-proposal>.*?</nexplane-proposal>", "", text, flags=re.DOTALL
        ).strip()

    def build_prompt_preview(
        self,
        goal: str,
        asset_context: list[dict],
        conversation: list[dict],
        draft_message: str | None = None,
    ) -> str:
        """Return the full assembled prompt string without calling any AI API."""
        system_prompt = self._build_system_prompt(goal, asset_context)
        parts = [f"## SYSTEM PROMPT\n\n{system_prompt}"]

        if conversation:
            history = "\n".join(
                f"[{m['role'].upper()}] {m['content']}" for m in conversation
            )
            parts.append(f"## CONVERSATION HISTORY\n\n{history}")

        if draft_message:
            parts.append(f"## CURRENT MESSAGE (draft)\n\n{draft_message}")

        return "\n\n---\n\n".join(parts)

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

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_ai_service.py -v
```

Expected: All 8 tests PASS.

- [ ] **Step 5: Run full suite to check for regressions**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q --tb=short --ignore=tests/smoke 2>&1 | tail -5
```

Expected: 60+ passed, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ai_service.py backend/tests/test_ai_service.py
git commit -m "feat(ai): grouped asset context, richer output template, build_prompt_preview"
```

---

## Task 2: Asset Name→ID Resolution + Prompt Preview Endpoint

**Files:**
- Modify: `backend/app/routers/projects.py`
- Test: `backend/tests/test_ai_service.py` (extend)

- [ ] **Step 1: Write failing tests for name resolution and preview endpoint**

Append to `backend/tests/test_ai_service.py`:

```python
def test_resolve_target_assets_to_ids():
    """The projects router resolves target_assets names to UUIDs using the in-memory asset list."""
    from app.routers.projects import _resolve_asset_ids

    name_to_id = {
        "prod-web-01": "uuid-1111",
        "prod-db-01": "uuid-2222",
    }
    proposed = [
        {"title": "Harden SSH", "change_type": "agent_ossecurity",
         "target_assets": ["prod-web-01", "prod-db-01"], "desired_outcome": {}, "seq": 1, "depends_on": []},
        {"title": "Unknown asset CR", "change_type": "ec2_stop",
         "target_assets": ["nonexistent-server"], "desired_outcome": {}, "seq": 2, "depends_on": [1]},
    ]
    resolved = _resolve_asset_ids(proposed, name_to_id)
    assert resolved[0]["target_asset_ids"] == ["uuid-1111", "uuid-2222"]
    # Unknown names are silently dropped (not in inventory)
    assert resolved[1]["target_asset_ids"] == []


def test_resolve_asset_ids_handles_empty_input():
    from app.routers.projects import _resolve_asset_ids
    result = _resolve_asset_ids([], {})
    assert result == []


def test_resolve_asset_ids_handles_no_target_assets_key():
    from app.routers.projects import _resolve_asset_ids
    proposed = [{"title": "No targets", "change_type": "ec2_stop", "seq": 1}]
    result = _resolve_asset_ids(proposed, {"prod-web-01": "uuid-1"})
    assert result[0]["target_asset_ids"] == []
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
docker exec nexplane-backend-1 python -m pytest tests/test_ai_service.py::test_resolve_target_assets_to_ids -v 2>&1 | tail -5
```

Expected: `ImportError: cannot import name '_resolve_asset_ids'`

- [ ] **Step 3: Add `_resolve_asset_ids` and update the AI chat endpoint in `backend/app/routers/projects.py`**

After the existing imports block at the top of the file (around line 24, after `from app.config import settings as app_settings`), add this helper function:

```python
def _resolve_asset_ids(proposed_crs: list[dict], name_to_id: dict[str, str]) -> list[dict]:
    """Resolve target_assets names to UUIDs in-place, adding target_asset_ids to each CR dict."""
    for cr in proposed_crs:
        cr["target_asset_ids"] = [
            name_to_id[n] for n in cr.get("target_assets", []) if n in name_to_id
        ]
    return proposed_crs
```

In the `ai_chat` endpoint (around line 375, after `result_dict = await ai_service.chat(...)`), add the resolution step before the return:

Find this block:
```python
    proposed_crs = result_dict["proposed_crs"]
```

If there is no such line, find `return AIChatResponse(` and insert before it:

```python
    # Resolve target_assets names to UUIDs using the already-loaded asset list
    if result_dict.get("proposed_crs"):
        name_to_id = {a.name: str(a.id) for a in assets}
        result_dict["proposed_crs"] = _resolve_asset_ids(result_dict["proposed_crs"], name_to_id)
```

Then find the return statement and verify it still passes `proposed_crs`:
```python
    return AIChatResponse(
        reply=result_dict["reply"],
        proposed_crs=result_dict["proposed_crs"],
    )
```

- [ ] **Step 4: Add the `GET /projects/{project_id}/ai/prompt-preview` endpoint**

Add this endpoint to `backend/app/routers/projects.py`, after the `ai_chat` endpoint:

```python
@router.get("/{project_id}/ai/prompt-preview")
async def get_prompt_preview(
    project_id: uuid.UUID,
    draft_message: str | None = Query(None),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the full assembled prompt string for this project (no AI call)."""
    project = await _get_project(db, project_id, user.organization_id)

    from sqlalchemy.orm import selectinload as _selectinload
    assets_result = await db.execute(
        select(Asset)
        .options(_selectinload(Asset.connector))
        .where(Asset.organization_id == user.organization_id)
    )
    assets = assets_result.scalars().all()
    asset_context = [
        {
            "name": a.name,
            "asset_type": a.asset_type.value,
            "environment": a.environment.value,
            "criticality": a.criticality.value if a.criticality else None,
            "connector_type": a.connector.connector_type.value if a.connector else None,
            "tags": a.tags or [],
        }
        for a in assets
    ]

    from app.config import settings as app_settings
    from app.services.ai_service import AIService
    from app.services.secrets_service import SecretsService
    ai_service = AIService(SecretsService(app_settings.SECRET_KEY))

    prompt = ai_service.build_prompt_preview(
        goal=project.goal or project.name,
        asset_context=asset_context,
        conversation=list(project.ai_context or []),
        draft_message=draft_message,
    )
    return {"prompt": prompt}
```

Also add `Query` to the fastapi imports at the top of the file if not already present. Find the line:
```python
from fastapi import APIRouter, Depends, HTTPException
```
and change to:
```python
from fastapi import APIRouter, Depends, HTTPException, Query
```

- [ ] **Step 5: Run all tests**

```bash
docker exec nexplane-backend-1 python -m pytest tests/ -q --tb=short --ignore=tests/smoke 2>&1 | tail -5
```

Expected: 63+ passed, 0 failed.

- [ ] **Step 6: Smoke-test the preview endpoint**

```bash
docker exec nexplane-backend-1 sh -c "python3 -c \"
import requests
r = requests.post('http://localhost:8000/auth/login', json={'email': 'admin@acme.example', 'password': 'admin123'})
t = r.json()['access_token']
h = {'Authorization': 'Bearer ' + t}
projects = requests.get('http://localhost:8000/projects', headers=h).json()
if projects:
    pid = projects[0]['id']
    p = requests.get(f'http://localhost:8000/projects/{pid}/ai/prompt-preview', headers=h, params={'draft_message': 'test'})
    print('Status:', p.status_code)
    print('Prompt length:', len(p.json().get('prompt', '')))
    print('Has assets section:', 'Available Assets' in p.json().get('prompt', ''))
else:
    print('No projects found — endpoint structure is correct')
\""
```

Expected: `Status: 200`, `Has assets section: True`

- [ ] **Step 7: Commit**

```bash
git add backend/app/routers/projects.py backend/tests/test_ai_service.py
git commit -m "feat(ai): asset name→UUID resolution, GET /ai/prompt-preview endpoint"
```

---

## Task 3: Update TypeScript Types

**Files:**
- Modify: `frontend/src/types/api.ts`

- [ ] **Step 1: Update `AIProposedCR` and add `PromptPreviewResponse` in `frontend/src/types/api.ts`**

Find the existing `AIProposedCR` interface (around line 152):

```typescript
export interface AIProposedCR {
  title: string;
  change_type: ChangeType;
  suggested_assets: string[];
  desired_outcome_sketch: Record<string, unknown>;
  notes?: string;
}
```

Replace with:

```typescript
export interface AIProposedCR {
  seq: number | null;
  title: string;
  change_type: ChangeType;
  target_assets: string[];
  target_asset_ids: string[];
  desired_outcome: Record<string, unknown>;
  depends_on: number[];
  notes?: string;
  // backward-compat aliases (may be present in old AI responses)
  suggested_assets?: string[];
  desired_outcome_sketch?: Record<string, unknown>;
}

export interface PromptPreviewResponse {
  prompt: string;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
docker exec nexplane-frontend-1 sh -c "cd /app && npx tsc --noEmit 2>&1 | grep -i 'AIProposedCR\|PromptPreview' | head -20"
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/api.ts
git commit -m "feat(ai): update AIProposedCR type with seq/depends_on/target_asset_ids"
```

---

## Task 4: Frontend API Client

**Files:**
- Modify: `frontend/src/api/endpoints.ts`

- [ ] **Step 1: Add `getPromptPreview` to `projectsApi` in `frontend/src/api/endpoints.ts`**

Find the `projectsApi` object. It currently ends with:
```typescript
  aiChat: (id: string, data: AIChatRequest) =>
    apiClient.post<AIChatResponse>(`/projects/${id}/ai/chat`, data).then((r) => r.data),
};
```

Change to:
```typescript
  aiChat: (id: string, data: AIChatRequest) =>
    apiClient.post<AIChatResponse>(`/projects/${id}/ai/chat`, data).then((r) => r.data),
  getPromptPreview: (id: string, draftMessage?: string) =>
    apiClient
      .get<PromptPreviewResponse>(`/projects/${id}/ai/prompt-preview`, {
        params: draftMessage ? { draft_message: draftMessage } : {},
      })
      .then((r) => r.data),
};
```

Also add `PromptPreviewResponse` to the import from `../types/api` at the top of the file. Find:
```typescript
import type { ... AIChatResponse ... } from "../types/api";
```
and add `PromptPreviewResponse` to the same import.

- [ ] **Step 2: Verify TypeScript compiles**

```bash
docker exec nexplane-frontend-1 sh -c "cd /app && npx tsc --noEmit 2>&1 | grep endpoints | head -10"
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/endpoints.ts
git commit -m "feat(ai): add projectsApi.getPromptPreview()"
```

---

## Task 5: AIPanel — Preview Panel + Updated CR Display

**Files:**
- Modify: `frontend/src/components/AIPanel.tsx`

This is the largest frontend change. The panel gains a side-by-side preview panel and updates how proposed CRs are displayed.

- [ ] **Step 1: Update imports in `AIPanel.tsx`**

Change the import block at the top of `frontend/src/components/AIPanel.tsx`:

```typescript
import { useState, useRef, useEffect, useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send, X, Plus, FileText } from "lucide-react";
import { projectsApi, changeRequestsApi } from "../api/endpoints";
import type { AIProposedCR, ChangeType } from "../types/api";
```

- [ ] **Step 2: Add `showPreview` state and preview fetch inside `AIPanel`**

Inside the `AIPanel` function body, after the existing state declarations (after `const bottomRef = ...`), add:

```typescript
  const [showPreview, setShowPreview] = useState(false);
  const [draftMessage, setDraftMessage] = useState("");

  const { data: previewData, isLoading: isLoadingPreview } = useQuery({
    queryKey: ["prompt-preview", projectId, draftMessage],
    queryFn: () => projectsApi.getPromptPreview(projectId, draftMessage || undefined),
    enabled: showPreview,
    staleTime: 10_000,
  });
```

Also rename `input` state and its setter to `draftMessage` and `setDraftMessage` throughout — or keep `input` for the textarea and use a debounced version for the preview query. **Use the simpler approach**: keep `input` as the textarea state, and set `draftMessage` only when preview is shown:

```typescript
  const [input, setInput] = useState("");
  const [showPreview, setShowPreview] = useState(false);

  const { data: previewData, isLoading: isLoadingPreview } = useQuery({
    queryKey: ["prompt-preview", projectId, showPreview ? input : ""],
    queryFn: () => projectsApi.getPromptPreview(projectId, input || undefined),
    enabled: showPreview,
    staleTime: 10_000,
  });
```

- [ ] **Step 3: Update the header to add the Preview toggle button**

Find the header div:
```typescript
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
        <span className="text-sm font-semibold text-slate-900">✦ AI Assistant</span>
        <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
          <X className="w-4 h-4" />
        </button>
      </div>
```

Replace with:
```typescript
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
        <span className="text-sm font-semibold text-slate-900">✦ AI Assistant</span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowPreview((v) => !v)}
            title="Toggle prompt preview"
            className={`p-1 rounded transition-colors ${showPreview ? "text-brand-600 bg-brand-50" : "text-slate-400 hover:text-slate-600"}`}
          >
            <FileText className="w-4 h-4" />
          </button>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
```

- [ ] **Step 4: Wrap the panel body in a side-by-side layout**

The current panel body is a `flex flex-col`. When `showPreview` is true, the conversation + input area should sit in a flex row next to the preview panel.

Find the outer div:
```typescript
    <div className="flex flex-col h-full bg-white border border-slate-200 rounded-lg overflow-hidden">
```

Replace the entire return with this restructured layout:

```typescript
  return (
    <div className="flex flex-col h-full bg-white border border-slate-200 rounded-lg overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
        <span className="text-sm font-semibold text-slate-900">✦ AI Assistant</span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowPreview((v) => !v)}
            title="Toggle prompt preview"
            className={`p-1 rounded transition-colors ${showPreview ? "text-brand-600 bg-brand-50" : "text-slate-400 hover:text-slate-600"}`}
          >
            <FileText className="w-4 h-4" />
          </button>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Body — chat on left, optional preview on right */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Chat column */}
        <div className="flex flex-col flex-1 min-w-0 min-h-0">
          {/* Conversation */}
          <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-0">
            {messages.length === 0 && (
              <div className="text-sm text-slate-400 text-center py-8">
                Describe your goal above, then ask the AI to help plan the change requests.
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-xs rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                    m.role === "user" ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-900"
                  }`}
                >
                  {m.content}
                </div>
              </div>
            ))}
            {chatMutation.isPending && (
              <div className="flex justify-start">
                <div className="bg-slate-100 rounded-lg px-3 py-2 text-sm text-slate-400 animate-pulse">
                  Thinking…
                </div>
              </div>
            )}
            {chatMutation.isError && (
              <div className="text-xs text-red-500 text-center">
                {(chatMutation.error as { response?: { status?: number } })?.response?.status === 402
                  ? "AI not configured. Ask an admin to add an Anthropic API key in Settings."
                  : "Something went wrong. Please try again."}
              </div>
            )}
            <div ref={bottomRef} />
          </div>

          {/* Proposed CRs */}
          {proposedCRs && proposedCRs.length > 0 && (
            <div className="border-t border-slate-100 p-3 space-y-2 shrink-0">
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
                Proposed Changes
              </p>
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                {proposedCRs.map((cr, i) => (
                  <div
                    key={i}
                    className="flex items-start justify-between gap-2 bg-slate-50 rounded-md px-3 py-2"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5">
                        {cr.seq != null && (
                          <span className="text-xs font-mono text-slate-400 shrink-0">{cr.seq}.</span>
                        )}
                        <span className="text-xs font-medium text-slate-900 truncate">{cr.title}</span>
                      </div>
                      <div className="text-xs text-slate-400 mt-0.5">
                        {cr.change_type.replace(/_/g, " ")}
                        {cr.target_assets?.length > 0 && (
                          <> · {cr.target_assets.slice(0, 2).join(", ")}{cr.target_assets.length > 2 ? ` +${cr.target_assets.length - 2}` : ""}</>
                        )}
                        {cr.depends_on?.length > 0 && (
                          <> · <span className="text-amber-600">after {cr.depends_on.join(", ")}</span></>
                        )}
                        {cr.notes && <> · {cr.notes}</>}
                      </div>
                    </div>
                    {addedIndices.has(i) ? (
                      <span className="text-xs text-emerald-600 shrink-0">✓ Added</span>
                    ) : (
                      <button
                        onClick={() => addCRMutation.mutate({ cr, idx: i })}
                        disabled={addCRMutation.isPending}
                        className="shrink-0 p-1 text-brand-600 hover:bg-brand-50 rounded"
                      >
                        <Plus className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                ))}
              </div>
              {addAllMutation.isError && (
                <p className="text-xs text-red-500">Some items could not be added. Check the project and try again.</p>
              )}
              {proposedCRs.some((_, i) => !addedIndices.has(i)) && (
                <button
                  onClick={() => addAllMutation.mutate()}
                  disabled={addAllMutation.isPending}
                  className="w-full text-xs text-brand-600 hover:underline py-1 disabled:opacity-50"
                >
                  {addAllMutation.isPending ? "Adding…" : "Add all to Project"}
                </button>
              )}
            </div>
          )}

          {/* Input */}
          <div className="border-t border-slate-100 p-3 flex gap-2 shrink-0">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask the AI to help plan your project…"
              rows={2}
              className="flex-1 text-sm border border-slate-200 rounded-md px-3 py-2 focus:outline-none focus:ring-2 focus:ring-brand-500 resize-none"
            />
            <button
              onClick={handleSend}
              disabled={!input.trim() || chatMutation.isPending}
              className="self-end p-2 bg-brand-600 text-white rounded-md hover:bg-brand-700 disabled:opacity-50"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Preview panel */}
        {showPreview && (
          <div className="w-72 border-l border-slate-100 flex flex-col min-h-0 bg-slate-50">
            <div className="px-3 py-2 border-b border-slate-100 shrink-0">
              <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">Full Prompt</span>
            </div>
            <div className="flex-1 overflow-y-auto p-3 min-h-0">
              {isLoadingPreview ? (
                <p className="text-xs text-slate-400 animate-pulse">Loading…</p>
              ) : previewData?.prompt ? (
                <pre className="text-xs text-slate-600 whitespace-pre-wrap font-mono leading-relaxed">
                  {previewData.prompt}
                </pre>
              ) : (
                <p className="text-xs text-slate-400">No preview available.</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
```

- [ ] **Step 5: Update `addCRMutation` and `addAllMutation` to use resolved IDs and new field names**

Find `addCRMutation` and update the `mutationFn`:

```typescript
  const addCRMutation = useMutation({
    mutationFn: async ({ cr, idx }: { cr: AIProposedCR; idx: number }) => {
      const newCr = await changeRequestsApi.create({
        title: cr.title,
        change_type: cr.change_type as ChangeType,
        target_asset_ids: cr.target_asset_ids ?? [],
        desired_outcome: cr.desired_outcome ?? cr.desired_outcome_sketch ?? {},
      });
      await projectsApi.addMember(projectId, { change_request_id: newCr.id });
      return idx;
    },
    ...
  });
```

Find `addAllMutation` and update its `changeRequestsApi.create` calls similarly:

```typescript
          const newCr = await changeRequestsApi.create({
            title: cr.title,
            change_type: cr.change_type as ChangeType,
            target_asset_ids: cr.target_asset_ids ?? [],
            desired_outcome: cr.desired_outcome ?? cr.desired_outcome_sketch ?? {},
          });
```

- [ ] **Step 6: Verify TypeScript compiles**

```bash
docker exec nexplane-frontend-1 sh -c "cd /app && npx tsc --noEmit 2>&1 | grep -i 'AIPanel\|Compliance\|Smoke' | head -20"
```

Expected: no new errors.

- [ ] **Step 7: Restart frontend and verify in browser**

```bash
docker compose -f f:/Nexplane/nexplane/docker-compose.yml stop frontend && docker compose -f f:/Nexplane/nexplane/docker-compose.yml up frontend -d
```

Open a project in the UI, click the AI Assistant panel, click the `FileText` icon in the header — the right-side preview panel should appear showing the full assembled prompt.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/AIPanel.tsx
git commit -m "feat(ai): side-by-side prompt preview panel, seq/depends_on CR display, resolved asset IDs"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ Full org inventory injected (already done in current code; now formatted as grouped context)
- ✅ Grouped by type + environment (servers prod/staging/dev, cloud_accounts, other)
- ✅ Richer output template: seq, title, change_type, target_assets, desired_outcome, depends_on, notes
- ✅ Narrative + embedded proposal — template instructs AI to write prose first, then `<nexplane-proposal>`
- ✅ Asset name→UUID resolution: `_resolve_asset_ids()` populates `target_asset_ids` before returning from chat endpoint
- ✅ `GET /projects/{id}/ai/prompt-preview` returns full assembled prompt string
- ✅ Side-by-side preview panel: toggled by FileText icon in header, fetches from preview endpoint
- ✅ CR display: seq number, target_assets names, depends_on indicator ("after 1, 2"), notes
- ✅ `addCRMutation` and `addAllMutation` use `target_asset_ids` and `desired_outcome`
- ✅ Backward compatibility: old `suggested_assets`/`desired_outcome_sketch` normalised in `_parse_proposal()`

**Placeholder scan:** None found — all code blocks are complete and specific.

**Type consistency:**
- `AIProposedCR.target_asset_ids` defined in Task 3 (types/api.ts), used in Task 5 (AIPanel.tsx) ✅
- `AIProposedCR.desired_outcome` defined in Task 3, used in Task 5 ✅
- `PromptPreviewResponse` defined in Task 3, used in Task 4 (endpoints.ts) ✅
- `_resolve_asset_ids()` defined in Task 2 (projects.py), tested in Task 2 ✅
- `build_prompt_preview()` defined in Task 1 (ai_service.py), called in Task 2 (projects.py) ✅
- `_build_asset_context_text()` defined in Task 1, tested in Task 1 ✅
