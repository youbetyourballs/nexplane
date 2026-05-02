import secrets as secrets_mod
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.org_settings import OrganizationSettings
from app.models.user import User, UserRole
from app.routers import current_user, require_roles
from app.schemas.org_settings import OrgSettingsRead, AIKeyUpdate
from app.schemas.credential import AIProvidersRead, AIProviderInfo, AIProviderWrite, AIDefaultWrite
from app.services.secrets_service import SecretsService
from app import config as app_config

SUPPORTED_PROVIDERS = {"anthropic": "sk-ant-", "openai": "sk-"}

router = APIRouter(prefix="/settings", tags=["Settings"])


def _get_secrets() -> SecretsService:
    return SecretsService(app_config.settings.SECRET_KEY)


async def _get_or_create_org_settings(
    org_id, db: AsyncSession
) -> OrganizationSettings:
    result = await db.execute(
        select(OrganizationSettings).where(OrganizationSettings.organization_id == org_id)
    )
    org_settings = result.scalar_one_or_none()
    if not org_settings:
        org_settings = OrganizationSettings(organization_id=org_id)
        db.add(org_settings)
        await db.flush()
    return org_settings


@router.get("", response_model=OrgSettingsRead)
async def get_settings(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(OrganizationSettings).where(
            OrganizationSettings.organization_id == user.organization_id
        )
    )
    org_settings = result.scalar_one_or_none()
    if not org_settings:
        return OrgSettingsRead(ai_configured=False, agent_configured=False, updated_at=None)
    return OrgSettingsRead(
        ai_configured=org_settings.anthropic_api_key_encrypted is not None,
        agent_configured=org_settings.agent_secret_encrypted is not None,
        updated_at=org_settings.updated_at,
    )


@router.put("/ai-key", response_model=OrgSettingsRead)
async def update_ai_key(
    body: AIKeyUpdate,
    user: User = Depends(require_roles(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
):
    if not body.api_key.startswith("sk-ant-"):
        raise HTTPException(status_code=422, detail="Invalid API key format — must start with 'sk-ant-'")
    svc = _get_secrets()
    org_settings = await _get_or_create_org_settings(user.organization_id, db)
    org_settings.anthropic_api_key_encrypted = svc.encrypt(body.api_key)
    await db.commit()
    await db.refresh(org_settings)
    return OrgSettingsRead(
        ai_configured=True,
        agent_configured=org_settings.agent_secret_encrypted is not None,
        updated_at=org_settings.updated_at,
    )


@router.get("/ai-providers", response_model=AIProvidersRead)
async def get_ai_providers(
    user: User = Depends(require_roles(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
):
    settings = await _get_or_create_org_settings(user.organization_id, db)
    svc = _get_secrets()
    providers_data: dict = {}

    if settings.ai_providers_encrypted:
        providers_data = svc.decrypt_json(settings.ai_providers_encrypted)
    elif settings.anthropic_api_key_encrypted:
        providers_data = {
            "default": "anthropic",
            "providers": {"anthropic": {"api_key": svc.decrypt(settings.anthropic_api_key_encrypted)}},
        }

    providers = {
        name: AIProviderInfo(configured=bool(info.get("api_key")))
        for name, info in providers_data.get("providers", {}).items()
    }
    for p in SUPPORTED_PROVIDERS:
        if p not in providers:
            providers[p] = AIProviderInfo(configured=False)

    return AIProvidersRead(default=providers_data.get("default"), providers=providers)


@router.put("/ai-providers/default", status_code=200)
async def set_default_provider(
    body: AIDefaultWrite,
    user: User = Depends(require_roles(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
):
    svc = _get_secrets()
    settings = await _get_or_create_org_settings(user.organization_id, db)
    providers_data = svc.decrypt_json(settings.ai_providers_encrypted) if settings.ai_providers_encrypted else {}
    provider_keys = providers_data.get("providers", {})
    if body.provider not in provider_keys or not provider_keys[body.provider].get("api_key"):
        raise HTTPException(status_code=422, detail=f"Provider '{body.provider}' is not configured")
    providers_data["default"] = body.provider
    settings.ai_providers_encrypted = svc.encrypt_json(providers_data)
    await db.commit()
    return {"status": "ok"}


@router.put("/ai-providers/{provider}", status_code=200)
async def set_ai_provider(
    provider: str,
    body: AIProviderWrite,
    user: User = Depends(require_roles(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
):
    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
    prefix = SUPPORTED_PROVIDERS[provider]
    if not body.api_key.startswith(prefix):
        raise HTTPException(status_code=422, detail=f"{provider} API key must start with '{prefix}'")

    svc = _get_secrets()
    settings = await _get_or_create_org_settings(user.organization_id, db)
    providers_data = svc.decrypt_json(settings.ai_providers_encrypted) if settings.ai_providers_encrypted else {}
    providers_data.setdefault("providers", {})[provider] = {"api_key": body.api_key}
    if not providers_data.get("default"):
        providers_data["default"] = provider
    settings.ai_providers_encrypted = svc.encrypt_json(providers_data)
    await db.commit()
    return {"status": "ok"}


@router.delete("/ai-providers/{provider}", status_code=204)
async def delete_ai_provider(
    provider: str,
    user: User = Depends(require_roles(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
):
    if provider not in SUPPORTED_PROVIDERS:
        return
    svc = _get_secrets()
    settings = await _get_or_create_org_settings(user.organization_id, db)
    if not settings.ai_providers_encrypted:
        return
    providers_data = svc.decrypt_json(settings.ai_providers_encrypted)
    providers_data.get("providers", {}).pop(provider, None)
    if providers_data.get("default") == provider:
        remaining = [p for p, info in providers_data.get("providers", {}).items() if info.get("api_key")]
        providers_data["default"] = remaining[0] if remaining else None
    settings.ai_providers_encrypted = svc.encrypt_json(providers_data)
    await db.commit()


@router.post("/agent-secret", response_model=OrgSettingsRead)
async def generate_agent_secret(
    user: User = Depends(require_roles(UserRole.admin)),
    db: AsyncSession = Depends(get_db),
):
    """Generates a new random agent secret, stores it encrypted, returns it once in plaintext."""
    new_secret = "sk-agent-" + secrets_mod.token_hex(24)
    svc = _get_secrets()
    org_settings = await _get_or_create_org_settings(user.organization_id, db)
    org_settings.agent_secret_encrypted = svc.encrypt(new_secret)
    await db.commit()
    await db.refresh(org_settings)
    return OrgSettingsRead(
        ai_configured=org_settings.anthropic_api_key_encrypted is not None,
        agent_configured=True,
        updated_at=org_settings.updated_at,
        agent_secret_plaintext=new_secret,
    )
