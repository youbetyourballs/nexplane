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
        return OrgSettingsRead(ai_configured=False, updated_at=None)
    return OrgSettingsRead(
        ai_configured=org_settings.anthropic_api_key_encrypted is not None,
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
    secrets = _get_secrets()
    encrypted = secrets.encrypt(body.api_key)

    result = await db.execute(
        select(OrganizationSettings).where(
            OrganizationSettings.organization_id == user.organization_id
        )
    )
    org_settings = result.scalar_one_or_none()

    if org_settings:
        org_settings.anthropic_api_key_encrypted = encrypted
    else:
        org_settings = OrganizationSettings(
            organization_id=user.organization_id,
            anthropic_api_key_encrypted=encrypted,
        )
        db.add(org_settings)

    await db.commit()
    await db.refresh(org_settings)
    return OrgSettingsRead(
        ai_configured=True,
        updated_at=org_settings.updated_at,
    )
