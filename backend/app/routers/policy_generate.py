from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.routers import current_user
from app.models.user import User

router = APIRouter(prefix="/policy", tags=["Policy"])

POLICY_PROMPTS = {
    "seccomp": """You are a Linux security expert. Generate a minimal seccomp profile JSON for a process that uses the following syscalls:
{syscalls}

Return ONLY valid JSON in the seccomp profile format (defaultAction, syscalls array with names and action SCMP_ACT_ALLOW).
Keep it minimal — only allow the listed syscalls plus essential ones (exit, exit_group, brk, mmap, mprotect if not already listed).""",

    "apparmor": """You are a Linux security expert. Generate a minimal AppArmor profile for a service that had these denial events:
{events}

Return ONLY a valid AppArmor profile text that allows the observed paths and denies everything else.""",

    "iptables": """You are a Linux network security expert. Generate minimal iptables rules for a service with these observed connections:
{connections}

Return ONLY iptables-restore format rules (with *filter and COMMIT). Allow only the observed destination IPs/ports, block all other outbound.""",

    "wdac": """You are a Windows security expert. Generate a minimal WDAC (Windows Defender Application Control) policy XML for a process that triggered these audit events:
{events}

Return ONLY valid WDAC policy XML that allows the observed signed executables and blocks unsigned code.""",

    "asr": """You are a Windows security expert. The following ASR (Attack Surface Reduction) rules triggered in audit mode:
{events}

Recommend which rules to enable in block mode vs keep in audit mode. Return as JSON: {{"block": ["rule-name"], "audit": ["rule-name"], "reasoning": "..."}}""",
}


class PolicyGenerateRequest(BaseModel):
    policy_type: str  # "seccomp" | "apparmor" | "iptables" | "wdac" | "asr"
    observation: dict  # from the learn phase CR result
    asset_id: str | None = None


class PolicyGenerateResponse(BaseModel):
    policy_type: str
    generated_policy: str
    model_used: str


@router.post("/generate", response_model=PolicyGenerateResponse)
async def generate_policy(
    body: PolicyGenerateRequest,
    user: User = Depends(current_user),
):
    prompt_template = POLICY_PROMPTS.get(body.policy_type)
    if not prompt_template:
        raise HTTPException(400, f"Unknown policy_type: {body.policy_type}. Valid: {list(POLICY_PROMPTS)}")

    obs = body.observation
    if body.policy_type == "seccomp":
        syscalls = obs.get("observed_syscalls", obs.get("syscalls_seen", []))
        prompt = prompt_template.format(syscalls="\n".join(syscalls))
    elif body.policy_type == "apparmor":
        events = obs.get("observed_denials", obs.get("audit_events", []))
        prompt = prompt_template.format(events="\n".join(str(e) for e in events[:50]))
    elif body.policy_type == "iptables":
        connections = obs.get("connections_seen", obs.get("observed_connections", []))
        prompt = prompt_template.format(connections="\n".join(str(c) for c in connections[:100]))
    elif body.policy_type in ("wdac", "asr"):
        events = obs.get("audit_events", [])
        prompt = prompt_template.format(events="\n".join(str(e) for e in events[:50]))
    else:
        prompt = prompt_template

    try:
        from app.services.ai_service import AIService
        from app.services.secrets_service import SecretsService
        from app.config import settings as app_settings
        from sqlalchemy.ext.asyncio import AsyncSession
        from app.database import AsyncSessionLocal
        from sqlalchemy import select
        from app.models.org_settings import OrganizationSettings
        from app.services.ai_service import _resolve_provider_config

        secrets = SecretsService(app_settings.SECRET_KEY)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(OrganizationSettings).where(
                    OrganizationSettings.organization_id == user.organization_id
                )
            )
            org_settings = result.scalar_one_or_none()

        if not org_settings or (not org_settings.anthropic_api_key_encrypted and not org_settings.ai_providers_encrypted):
            raise HTTPException(
                status_code=402,
                detail="AI not configured — add an AI provider API key in Settings",
            )

        provider, api_key, model = _resolve_provider_config(org_settings, secrets)
        ai_service = AIService(secrets)
        result_dict = await ai_service.chat(
            provider=provider,
            api_key=api_key,
            model=model,
            conversation=[{"role": "user", "content": prompt}],
            project_goal="Generate security policy",
            asset_context=[],
        )
        generated = result_dict.get("reply", "")
        model_used = model
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"AI generation failed: {e}")

    return PolicyGenerateResponse(
        policy_type=body.policy_type,
        generated_policy=generated,
        model_used=model_used,
    )


@router.get("/templates")
async def list_policy_templates(user: User = Depends(current_user)):
    return [{"policy_type": k, "description": f"Generate {k} policy from observations"} for k in POLICY_PROMPTS]
