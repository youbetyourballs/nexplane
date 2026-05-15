from __future__ import annotations
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.routers import current_user
from app.models.user import User

router = APIRouter(prefix="/credentials", tags=["Credentials"])


class DiscoverRequest(BaseModel):
    access_key_id: str
    lookback_days: int = 90


@router.post("/discover-consumers")
async def discover_consumers(
    body: DiscoverRequest,
    user: User = Depends(current_user),
):
    """Find which services use a given IAM access key (CloudTrail analysis)."""
    import os
    from app.services.credential_consumer_discovery import discover_iam_key_consumers
    result = await discover_iam_key_consumers(
        access_key_id=body.access_key_id,
        lookback_days=body.lookback_days,
        aws_access_key=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
    )
    return result
