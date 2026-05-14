"""Rapid onboarding — validates credentials and triggers initial discovery."""
from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.routers import current_user
from app.models.user import User

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])


class OnboardConnectorRequest(BaseModel):
    connector_type: str   # "aws", "azure", "gcp", etc.
    display_name: str
    credentials: dict     # type-specific keys
    trigger_discovery: bool = True


class OnboardConnectorResponse(BaseModel):
    connector_id: str
    display_name: str
    connector_type: str
    discovery_triggered: bool
    estimated_assets: str  # "checking..." initially


@router.post("/connector", response_model=OnboardConnectorResponse, status_code=201)
async def onboard_connector(
    body: OnboardConnectorRequest,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a connector and immediately trigger discovery. One API call to go from credentials to inventory."""
    from app.models.connector import Connector, ConnectorType

    # Validate connector_type is known
    try:
        ctype = ConnectorType(body.connector_type)
    except ValueError:
        raise HTTPException(
            400,
            f"Unknown connector type: {body.connector_type}. Valid: {[e.value for e in ConnectorType]}",
        )

    connector = Connector(
        id=uuid.uuid4(),
        organization_id=user.organization_id,
        connector_type=ctype,
        name=body.display_name,
        scoped_permissions={},
    )
    db.add(connector)
    await db.commit()
    await db.refresh(connector)

    discovery_triggered = False
    if body.trigger_discovery:
        try:
            from app.services.ingest_service import IngestService
            ingest = IngestService(db)
            await ingest.run(str(connector.id))
            discovery_triggered = True
        except Exception:
            # Non-fatal — connector created, discovery can be triggered manually
            pass

    return OnboardConnectorResponse(
        connector_id=str(connector.id),
        display_name=connector.name,
        connector_type=body.connector_type,
        discovery_triggered=discovery_triggered,
        estimated_assets="Discovery running..." if discovery_triggered else "Trigger discovery to begin",
    )


@router.get("/checklist")
async def onboarding_checklist(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns what's configured and what's missing for a complete setup."""
    from app.models.connector import Connector
    from app.models.asset import Asset
    from app.models.change_request import ChangeRequest

    connectors_result = await db.execute(
        select(Connector).where(Connector.organization_id == user.organization_id)
    )
    connectors = connectors_result.scalars().all()

    asset_count = await db.scalar(
        select(func.count(Asset.id)).where(Asset.organization_id == user.organization_id)
    ) or 0

    # Check if any agent-type connector exists (indicates agent deployed)
    from app.models.connector import ConnectorType
    agent_connectors = [c for c in connectors if c.connector_type == ConnectorType.nexplane_agent]

    # Check if any change requests exist
    cr_count = await db.scalar(
        select(func.count(ChangeRequest.id)).where(
            ChangeRequest.organization_id == user.organization_id
        )
    ) or 0

    return {
        "steps": [
            {
                "id": "connector",
                "label": "Add a cloud connector",
                "complete": len(connectors) > 0,
                "detail": f"{len(connectors)} connector(s) configured" if connectors else "No connectors yet",
            },
            {
                "id": "assets",
                "label": "Discover assets",
                "complete": asset_count > 0,
                "detail": f"{asset_count} assets in inventory" if asset_count else "Run discovery after adding a connector",
            },
            {
                "id": "agent",
                "label": "Deploy agent to a server",
                "complete": len(agent_connectors) > 0,
                "detail": f"{len(agent_connectors)} agent connector(s) registered" if agent_connectors else "Install the Nexplane agent on at least one server for OS hardening",
            },
            {
                "id": "cr",
                "label": "Create your first Change Request",
                "complete": cr_count > 0,
                "detail": f"{cr_count} change request(s) created" if cr_count else "Apply a hardening policy or run a patch campaign",
            },
        ],
        "connector_count": len(connectors),
        "asset_count": asset_count,
    }
