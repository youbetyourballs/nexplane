# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json
import re
from app.services.secrets_service import SecretsService
from app.mcp_tools.server_instructions import NEXPLANE_SERVER_INSTRUCTIONS


_PROVIDER_DEFAULTS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
}

_CHANGE_TYPES_TEXT_CACHE: str | None = None

def _build_change_types_text() -> str:
    global _CHANGE_TYPES_TEXT_CACHE
    if _CHANGE_TYPES_TEXT_CACHE is not None:
        return _CHANGE_TYPES_TEXT_CACHE
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
    _CHANGE_TYPES_TEXT_CACHE = "\n".join(lines).strip()
    return _CHANGE_TYPES_TEXT_CACHE


_SYSTEM_PROMPT_TEMPLATE = (
    NEXPLANE_SERVER_INSTRUCTIONS
    + """

---

You are the Nexplane AI planning assistant. The operator is planning a project with this goal: {goal}

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
)


def _build_asset_context_text(assets: list[dict]) -> str:
    """Build a grouped, condensed asset context block for the system prompt."""
    if not assets:
        return "No assets registered yet."

    servers_by_env: dict[str, list[dict]] = {}
    cloud_accounts: list[dict] = []
    other: list[dict] = []

    env_order = ["prod", "staging", "dev"]
    env_labels = {"prod": "Production", "staging": "Staging", "dev": "Dev"}

    for a in assets:
        atype = a.get("asset_type", "")
        env = a.get("environment", "")
        if atype in ("server", "endpoint"):
            servers_by_env.setdefault(env, []).append(a)
        elif atype == "cloud_account":
            cloud_accounts.append(a)
        else:
            other.append(a)

    lines: list[str] = []

    for env in env_order:
        group = servers_by_env.get(env, [])
        if not group:
            continue
        label = env_labels.get(env, env.capitalize())
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
        compact = "  " + ", ".join(
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
        safe_goal = goal.replace("{", "{{").replace("}", "}}")
        safe_assets_text = assets_text.replace("{", "{{").replace("}", "}}")
        safe_change_types = _build_change_types_text().replace("{", "{{").replace("}", "}}")
        rendered = _SYSTEM_PROMPT_TEMPLATE.format(
            goal=safe_goal,
            asset_count=len(asset_context),
            assets_text=safe_assets_text,
            change_types=safe_change_types,
        )
        # Restore literal braces that were escaped in user-supplied text
        return rendered.replace("{{", "{").replace("}}", "}")

    def _parse_proposal(self, text: str) -> list[dict] | None:
        match = re.search(r"<nexplane-proposal>(.*?)</nexplane-proposal>", text, re.DOTALL)
        if not match:
            return None
        try:
            items = json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(items, list):
            return None
        for item in items:
            if not isinstance(item, dict):
                return None
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
