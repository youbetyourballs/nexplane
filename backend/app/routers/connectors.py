import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.connector import Connector
from app.models.user import User
from app.routers import current_user
from app.schemas.connector import ConnectorCreate, ConnectorRead, ConnectorTestResult, IngestResponse
from app.schemas.asset import AssetRead
from app.services.connector_service import test_connector
from app.services.audit_service import record_event
from app.services.ingest_service import IngestService
from app.connectors.catalog_service import get_catalog_service

router = APIRouter(prefix="/connectors", tags=["Connectors"])


@router.get("", response_model=list[ConnectorRead])
async def list_connectors(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Connector).where(Connector.organization_id == user.organization_id))
    return result.scalars().all()


@router.post("", response_model=ConnectorRead, status_code=201)
async def create_connector(
    body: ConnectorCreate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    connector = Connector(organization_id=user.organization_id, **body.model_dump())
    db.add(connector)
    await db.flush()
    await record_event(db, user.organization_id, "connector.created",
                       {"connector_id": str(connector.id), "type": connector.connector_type.value},
                       actor_id=user.id)
    await db.commit()
    await db.refresh(connector)
    return connector


@router.post("/{connector_id}/test", response_model=ConnectorTestResult)
async def test_connector_endpoint(
    connector_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    connector = await db.get(Connector, connector_id)
    if not connector or connector.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Connector not found")
    result = await test_connector(connector.connector_type)
    return ConnectorTestResult(**result)


@router.post("/{connector_id}/ingest/{action_id}", response_model=IngestResponse)
async def run_ingest(
    connector_id: uuid.UUID,
    action_id: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    connector = await db.get(Connector, connector_id)
    if not connector or connector.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Connector not found")
    try:
        catalog = get_catalog_service()
        service = IngestService(catalog)
        result = await service.run(action_id, connector, user.organization_id, db)
        # Convert to schemas BEFORE commit — ORM objects are expired after commit
        asset_schemas = [AssetRead.model_validate(a) for a in result["assets"]]
        await db.commit()
        await record_event(db, user.organization_id, "connector.ingest_run",
                           {"connector_id": str(connector_id), "action_id": action_id,
                            "created": result["created"], "updated": result["updated"]},
                           actor_id=user.id)
        await db.commit()
        return IngestResponse(
            created=result["created"],
            updated=result["updated"],
            assets=asset_schemas,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except (KeyError, ImportError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
