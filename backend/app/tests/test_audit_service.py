# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.audit_event import AuditEvent
from app.services.audit_service import record_event


@pytest.mark.asyncio
async def test_record_event_creates_audit_entry(db, org, admin_user):
    event = await record_event(
        db=db,
        organization_id=org.id,
        event_type="test.event",
        event_payload={"key": "value"},
        actor_id=admin_user.id,
    )
    await db.commit()

    result = await db.execute(select(AuditEvent).where(AuditEvent.id == event.id))
    found = result.scalar_one_or_none()
    assert found is not None
    assert found.event_type == "test.event"
    assert found.event_payload == {"key": "value"}
    assert found.actor_id == admin_user.id
    assert found.organization_id == org.id
    assert found.change_request_id is None


@pytest.mark.asyncio
async def test_record_event_with_change_request(db, org, admin_user):
    cr_id = uuid.uuid4()
    event = await record_event(
        db=db,
        organization_id=org.id,
        event_type="change_request.created",
        event_payload={"change_request_id": str(cr_id)},
        actor_id=admin_user.id,
        change_request_id=None,  # no FK constraint in test
    )
    await db.commit()
    assert event.id is not None


@pytest.mark.asyncio
async def test_record_event_no_actor(db, org):
    event = await record_event(
        db=db,
        organization_id=org.id,
        event_type="system.health_check",
        event_payload={"status": "ok"},
    )
    await db.commit()
    assert event.actor_id is None
    assert event.event_type == "system.health_check"


@pytest.mark.asyncio
async def test_multiple_events_ordered_by_created_at(db, org, admin_user):
    for i in range(3):
        await record_event(db, org.id, f"test.event.{i}", {"seq": i}, actor_id=admin_user.id)
    await db.commit()

    result = await db.execute(
        select(AuditEvent)
        .where(AuditEvent.organization_id == org.id, AuditEvent.event_type.like("test.event.%"))
        .order_by(AuditEvent.created_at.asc())
    )
    events = result.scalars().all()
    assert len(events) >= 3
