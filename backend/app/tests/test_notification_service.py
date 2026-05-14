import pytest
from unittest.mock import AsyncMock, MagicMock
from app.services.notification_service import NotificationService, NotificationEvent
import uuid


@pytest.mark.asyncio
async def test_emit_cr_awaiting_approval_creates_notification():
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    svc = NotificationService(mock_db)
    await svc.emit(NotificationEvent(
        event_type="cr.awaiting_approval",
        organization_id=str(uuid.uuid4()),
        actor_id=str(uuid.uuid4()),
        resource_id=str(uuid.uuid4()),
        resource_type="change_request",
        message="CR 'Patch nginx' is awaiting your approval",
        recipients=[str(uuid.uuid4())],
    ))
    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()
