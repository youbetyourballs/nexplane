import json
import re
from app.services.secrets_service import SecretsService


_PROVIDER_DEFAULTS = {
    "anthropic": "claude-sonnet-4-6",
    "openai": "gpt-4o",
}

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
        rendered = _SYSTEM_PROMPT_TEMPLATE.format(
            goal=safe_goal,
            asset_count=len(asset_context),
            assets_text=safe_assets_text,
            change_types=_CHANGE_TYPES_TEXT,
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
