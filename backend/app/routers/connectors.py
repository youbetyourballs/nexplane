# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.connector import Connector
from app.models.user import User
from app.routers import current_user
from app.schemas.connector import ConnectorCreate, ConnectorRead, ConnectorTestResult, IngestResponse
from app.schemas.asset import AssetRead
from app.schemas.credential import CredentialRead, CredentialWrite, CredentialField
from app.schemas.scheduled_ingest import ScheduledIngestRead, ScheduledIngestWrite
from app.services.connector_service import test_connector
from app.services.audit_service import record_event
from app.services.ingest_service import IngestService
from app.services import scheduler_service
from app.connectors.catalog_service import get_catalog_service
from app.models.connector_credential import ConnectorCredential
from app.models.scheduled_ingest import ScheduledIngest

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


@router.delete("/{connector_id}", status_code=204)
async def delete_connector(
    connector_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Connector).where(
            Connector.id == connector_id,
            Connector.organization_id == user.organization_id,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")

    # Clean up stored secret before cascade-deleting the credential row
    from app.services.secret_backend_factory import get_secret_backend
    cred_result = await db.execute(
        select(ConnectorCredential).where(ConnectorCredential.connector_id == connector_id)
    )
    cred_row = cred_result.scalar_one_or_none()
    if cred_row:
        try:
            get_secret_backend().delete_secret(cred_row.credentials_encrypted)
        except Exception:
            pass  # Non-fatal: DB row still removed; secret may need manual cleanup

    await db.delete(connector)
    await db.flush()
    await record_event(
        db,
        user.organization_id,
        "connector.deleted",
        {"connector_id": str(connector_id)},
        actor_id=user.id,
    )
    await db.commit()


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


@router.get("/{connector_id}/credentials", response_model=CredentialRead)
async def get_credentials(
    connector_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Connector).where(
            Connector.id == connector_id,
            Connector.organization_id == user.organization_id,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")

    catalog_svc = get_catalog_service()
    catalog = catalog_svc.get_connector_catalog(connector.connector_type.value)
    fields = [CredentialField(**f) for f in catalog.get("credential_fields", [])]

    cred_result = await db.execute(
        select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
    )
    cred_row = cred_result.scalar_one_or_none()
    return CredentialRead(
        configured=cred_row is not None,
        fields=fields,
        updated_at=cred_row.updated_at if cred_row else None,
    )


@router.put("/{connector_id}/credentials", status_code=200)
async def upsert_credentials(
    connector_id: uuid.UUID,
    body: CredentialWrite,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Connector).where(
            Connector.id == connector_id,
            Connector.organization_id == user.organization_id,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")

    from app.services.secret_backend_factory import get_secret_backend

    catalog_svc = get_catalog_service()
    catalog = catalog_svc.get_connector_catalog(connector.connector_type.value)
    required_fields = [f["name"] for f in catalog.get("credential_fields", []) if f.get("required")]
    missing = [f for f in required_fields if not body.credentials.get(f)]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required credential fields: {missing}")

    backend = get_secret_backend()
    connector_id_str = str(connector_id)
    encrypted = backend.encrypt_json(body.credentials, connector_id=connector_id_str)

    cred_result = await db.execute(
        select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
    )
    cred_row = cred_result.scalar_one_or_none()
    if cred_row:
        cred_row.credentials_encrypted = encrypted
        cred_row.updated_by = user.id
    else:
        cred_row = ConnectorCredential(
            connector_id=connector.id,
            organization_id=user.organization_id,
            credentials_encrypted=encrypted,
            updated_by=user.id,
        )
        db.add(cred_row)

    await db.commit()
    return {"status": "ok"}


@router.get("/{connector_id}/schedule", response_model=ScheduledIngestRead | None)
async def get_schedule(
    connector_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    connector = await db.get(Connector, connector_id)
    if not connector or connector.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Connector not found")
    result = await db.execute(
        select(ScheduledIngest).where(ScheduledIngest.connector_id == connector_id)
    )
    return result.scalar_one_or_none()


@router.put("/{connector_id}/schedule", response_model=ScheduledIngestRead)
async def upsert_schedule(
    connector_id: uuid.UUID,
    body: ScheduledIngestWrite,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    connector = await db.get(Connector, connector_id)
    if not connector or connector.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Connector not found")
    result = await db.execute(
        select(ScheduledIngest).where(ScheduledIngest.connector_id == connector_id)
    )
    schedule = result.scalar_one_or_none()
    if schedule:
        schedule.interval_hours = body.interval_hours
        schedule.action_id = body.action_id
        schedule.next_run_at = datetime.now(timezone.utc) + timedelta(hours=body.interval_hours)
    else:
        schedule = ScheduledIngest(
            connector_id=connector_id,
            organization_id=user.organization_id,
            action_id=body.action_id,
            interval_hours=body.interval_hours,
            next_run_at=datetime.now(timezone.utc) + timedelta(hours=body.interval_hours),
        )
        db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    await scheduler_service.upsert_schedule(schedule)
    return schedule


@router.delete("/{connector_id}/schedule", status_code=204)
async def delete_schedule(
    connector_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    connector = await db.get(Connector, connector_id)
    if not connector or connector.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Connector not found")
    result = await db.execute(
        select(ScheduledIngest).where(ScheduledIngest.connector_id == connector_id)
    )
    schedule = result.scalar_one_or_none()
    if schedule:
        await scheduler_service.remove_schedule(schedule.id)
        await db.delete(schedule)
        await db.commit()


@router.delete("/{connector_id}/credentials", status_code=204)
async def delete_credentials(
    connector_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Connector).where(
            Connector.id == connector_id,
            Connector.organization_id == user.organization_id,
        )
    )
    connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(status_code=404, detail="Connector not found")

    cred_result = await db.execute(
        select(ConnectorCredential).where(ConnectorCredential.connector_id == connector.id)
    )
    cred_row = cred_result.scalar_one_or_none()
    if cred_row:
        from app.services.secret_backend_factory import get_secret_backend
        get_secret_backend().delete_secret(cred_row.credentials_encrypted)
        await db.delete(cred_row)
        await db.commit()
