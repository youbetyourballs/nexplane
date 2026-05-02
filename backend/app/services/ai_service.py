import json
import re
from app.services.secrets_service import SecretsService


def _resolve_api_key(settings, secrets_svc: SecretsService) -> str:
    """Resolve AI API key from ai_providers_encrypted or legacy anthropic_api_key_encrypted."""
    if settings.ai_providers_encrypted:
        data = secrets_svc.decrypt_json(settings.ai_providers_encrypted)
        default = data.get("default", "anthropic")
        providers = data.get("providers", {})
        if default in providers and providers[default].get("api_key"):
            return providers[default]["api_key"]
    if settings.anthropic_api_key_encrypted:
        return secrets_svc.decrypt(settings.anthropic_api_key_encrypted)
    raise ValueError("No AI provider configured")


_SYSTEM_PROMPT_TEMPLATE = """You are a planning assistant for Nexplane, a secure infrastructure change management platform.

The operator is planning a project with this goal: {goal}

Available assets in their environment:
{assets_text}

Agent capabilities (execution tier 3 — runs directly on hosts via Nexplane Agent):

Linux OS hardening: configure SELinux/AppArmor/seccomp modes and policies; apply CIS sysctl parameters; manage iptables/nftables/firewalld rules; blacklist dangerous kernel modules; harden mount options (noexec/nosuid/nodev); deploy auditd rule sets; set up AIDE/Tripwire file integrity monitoring; load eBPF programs (Cilium/Falco/Tetragon). Read-only audits available for OS security posture and eBPF state.

Linux auth, access, and certificates: harden SSH (disable root login, key-only auth, cipher allowlist); configure PAM (password complexity, account lockout, session timeout); install/remove CA certificates in the OS trust store; configure NTP (chrony/timesyncd/ntpd). Read-only audits available for user/group misconfigurations (no-expiry accounts, UID 0 non-root, service shells) and privilege escalation vulnerabilities (PwnKit, DirtyPipe, SUID binaries, sudo misconfig).

Windows hardening: configure LAPS; enable Credential Guard (VBS); enforce PowerShell Constrained Language Mode; deploy AppLocker application allowlist; harden SMB (disable SMBv1, require signing); enable BitLocker; configure Windows Firewall rules; disable legacy TLS/SSL via SCHANNEL; harden RDP (NLA, encryption, idle timeout); configure Windows audit policy (CIS/STIG); harden registry (disable autorun, LM hash, WDigest, NTLMv1). Read-only audit available for suspicious scheduled tasks.

Cross-platform: manage TLS certificates (ACME/internal CA/manual); configure DNS resolvers (DoH/DoT/plain); audit installed software inventory (dpkg/rpm/Windows).

Linux instance upgrade: in-place security/package/dist upgrade with snapshot; containerize-and-migrate workflow.

All agent actions have rollback support where applicable. Use these capabilities when proposing hardening, compliance, or upgrade change requests targeting Linux or Windows servers.

Your job:
1. Ask targeted clarifying questions ONE AT A TIME to understand scope, affected assets, risk tolerance, and sequencing constraints. Reference available assets by name when relevant.
2. When you have enough information to propose a complete change plan, write your proposal as prose and then append a structured block using this EXACT format:

<nexplane-proposal>
[
  {{
    "title": "Short descriptive title for the change request",
    "change_type": "dns_update|snapshot_asset|security_group_update|key_rotation|telemetry_agent_deploy|remote_command|microsegmentation_policy",
    "suggested_assets": ["asset name 1", "asset name 2"],
    "desired_outcome_sketch": {{}},
    "notes": "Optional sequencing or dependency notes"
  }}
]
</nexplane-proposal>

Only include the <nexplane-proposal> block when you are ready to propose the FULL plan. Do not include it in clarifying question responses."""


class AIService:
    def __init__(self, secrets_service: SecretsService):
        self._secrets = secrets_service

    def _build_system_prompt(self, goal: str, asset_context: list[dict]) -> str:
        if asset_context:
            lines = []
            for a in asset_context:
                line = f"- {a['name']} ({a['asset_type']}, {a['environment']})"
                if a.get("tags"):
                    line += f" [tags: {', '.join(a['tags'])}]"
                lines.append(line)
            assets_text = "\n".join(lines)
        else:
            assets_text = "No assets registered yet."
        return _SYSTEM_PROMPT_TEMPLATE.format(goal=goal, assets_text=assets_text)

    def _parse_proposal(self, text: str) -> list[dict] | None:
        match = re.search(r"<nexplane-proposal>(.*?)</nexplane-proposal>", text, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            return None

    def _strip_proposal_tags(self, text: str) -> str:
        return re.sub(
            r"\s*<nexplane-proposal>.*?</nexplane-proposal>", "", text, flags=re.DOTALL
        ).strip()

    async def chat(
        self,
        api_key: str,
        conversation: list[dict],
        project_goal: str,
        asset_context: list[dict],
    ) -> dict:
        import anthropic
        from app.config import settings

        client = anthropic.AsyncAnthropic(api_key=api_key)
        system_prompt = self._build_system_prompt(project_goal, asset_context)

        messages = [{"role": m["role"], "content": m["content"]} for m in conversation]

        response = await client.messages.create(
            model=settings.AI_MODEL,
            max_tokens=2048,
            system=system_prompt,
            messages=messages,
        )

        reply_text = response.content[0].text
        proposed_crs = self._parse_proposal(reply_text)
        clean_reply = self._strip_proposal_tags(reply_text)

        return {"reply": clean_reply, "proposed_crs": proposed_crs}
