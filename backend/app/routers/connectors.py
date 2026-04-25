import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.connector import Connector
from app.models.user import User
from app.routers import current_user
from app.schemas.connector import ConnectorCreate, ConnectorRead, ConnectorTestResult
from app.services.connector_service import test_connector
from app.services.audit_service import record_event

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
