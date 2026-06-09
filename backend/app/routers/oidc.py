import logging
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.models.identity_provider import IdentityProvider, IdpType
from app.models.user import User, UserRole
from app.services.auth_service import create_access_token, hash_password
from app.services.oidc_service import OidcConfig, build_authorization_url, exchange_code_for_userinfo, _get_oidc_metadata

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/oidc", tags=["OIDC"])


def _cfg_from_idp(idp: IdentityProvider) -> OidcConfig:
    raw = {k: idp.config[k] for k in ("issuer", "client_id", "client_secret", "scopes") if k in idp.config}
    if "scopes" in raw and isinstance(raw["scopes"], str):
        raw["scopes"] = raw["scopes"].split()
    return OidcConfig(**raw)

_pending_states: dict[str, dict] = {}


def _generate_state(idp_id: uuid.UUID) -> str:
    state = secrets.token_urlsafe(32)
    _pending_states[state] = {
        "idp_id": str(idp_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return state


def _verify_state(state: str, idp_id: uuid.UUID) -> bool:
    entry = _pending_states.pop(state, None)
    if not entry:
        return False
    return entry["idp_id"] == str(idp_id)


async def _get_active_oidc_idp(db: AsyncSession, idp_id: uuid.UUID) -> IdentityProvider:
    result = await db.execute(
        select(IdentityProvider).where(
            IdentityProvider.id == idp_id,
            IdentityProvider.type == IdpType.oidc,
            IdentityProvider.enabled == True,  # noqa: E712
        )
    )
    idp = result.scalar_one_or_none()
    if not idp:
        raise HTTPException(status_code=404, detail="OIDC identity provider not found or not enabled")
    return idp


@router.get("/{idp_id}/redirect")
async def oidc_redirect(
    idp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    idp = await _get_active_oidc_idp(db, idp_id)
    cfg = _cfg_from_idp(idp)

    metadata = await _get_oidc_metadata(cfg.issuer)
    state = _generate_state(idp_id)

    redirect_uri = f"{settings.INSTANCE_URL.rstrip('/')}/auth/oidc/{idp_id}/callback"

    url = build_authorization_url(
        config=cfg,
        redirect_uri=redirect_uri,
        state=state,
        authorization_endpoint=metadata["authorization_endpoint"],
    )
    return RedirectResponse(url=url, status_code=302)


@router.get("/{idp_id}/callback")
async def oidc_callback(
    idp_id: uuid.UUID,
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    if not _verify_state(state, idp_id):
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    idp = await _get_active_oidc_idp(db, idp_id)
    cfg = _cfg_from_idp(idp)
    cfg.auto_provision = idp.config.get("auto_provision", False)

    redirect_uri = f"{settings.INSTANCE_URL.rstrip('/')}/auth/oidc/{idp_id}/callback"

    try:
        userinfo = await exchange_code_for_userinfo(cfg, code, redirect_uri)
    except Exception:
        _log.exception("OIDC token exchange failed for idp_id=%s", idp_id)
        raise HTTPException(status_code=400, detail="OIDC token exchange failed")

    email = userinfo.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="No email in OIDC userinfo")

    result = await db.execute(
        select(User).where(
            User.email == email,
            User.organization_id == idp.org_id,
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        if not cfg.auto_provision:
            raise HTTPException(
                status_code=403,
                detail="No local account found for this email. Contact your admin.",
            )
        user = User(
            id=uuid.uuid4(),
            organization_id=idp.org_id,
            email=email,
            name=userinfo.get("name", email),
            role=UserRole.security_operator,
            hashed_password=hash_password(secrets.token_urlsafe(32)),
        )
        from sqlalchemy.exc import IntegrityError
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        await db.refresh(user)

    token = create_access_token(str(user.id))
    return {"access_token": token, "token_type": "bearer"}
