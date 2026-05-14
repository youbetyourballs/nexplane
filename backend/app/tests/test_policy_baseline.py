import pytest
from unittest.mock import AsyncMock, MagicMock
import uuid


@pytest.mark.asyncio
async def test_store_seccomp_baseline():
    from app.services.policy_baseline_service import PolicyBaselineService

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    svc = PolicyBaselineService(mock_db)

    result = await svc.store(
        asset_id=str(uuid.uuid4()),
        policy_type="seccomp",
        cr_id=str(uuid.uuid4()),
        observation={"observed_syscalls": ["read", "write", "epoll_wait"]},
        organization_id=str(uuid.uuid4()),
    )
    assert result[0] is not None
    mock_db.add.assert_called_once()


@pytest.mark.asyncio
async def test_drift_detection_finds_new_syscall():
    from app.services.policy_baseline_service import PolicyBaselineService

    mock_db = AsyncMock()
    svc = PolicyBaselineService(mock_db)

    baseline_id = str(uuid.uuid4())
    asset_id = str(uuid.uuid4())

    import unittest.mock as _mock
    with _mock.patch.object(svc, 'get_latest', AsyncMock(return_value={
        "id": baseline_id,
        "observation": {"observed_syscalls": ["read", "write", "epoll_wait"]},
        "created_at": "2026-01-01T00:00:00",
    })):
        drift = await svc.detect_drift(asset_id, "seccomp",
            {"observed_syscalls": ["read", "write", "epoll_wait", "mprotect"]})

    assert len(drift["new_behaviors"]) == 1
    assert "mprotect" in drift["new_behaviors"]
