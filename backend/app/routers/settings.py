import secrets as secrets_mod
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.org_settings import OrganizationSettings
from app.models.user import User, UserRole
from app.routers import current_user, require_roles
from app.schemas.org_settings import OrgSettingsRead, AIKeyUpdate
from app.services.secrets_service import SecretsService
from app import config as app_config

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
