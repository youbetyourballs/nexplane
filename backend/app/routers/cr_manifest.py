from typing import Optional
from fastapi import APIRouter, Depends
from app.routers import current_user
from app.services.manifest_builder import get_manifest

router = APIRouter(prefix="/cr-manifest", tags=["cr-manifest"])


@router.get("")
async def list_cr_manifest(
    domain: Optional[str] = None,
    action_class: Optional[str] = None,
    touches: Optional[str] = None,
    rollback_type: Optional[str] = None,
    user=Depends(current_user),
):
    entries = get_manifest(
        domain=domain,
        action_class=action_class,
        touches=touches,
        rollback_type=rollback_type,
    )
    return {"count": len(entries), "entries": entries}
