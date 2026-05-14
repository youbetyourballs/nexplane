import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_emergency_lockout_disables_iam_user():
    from app.connectors.executors.nexplane_agent.emergency_user_lockout import execute

    mock_iam_execute = AsyncMock(return_value={
        "action": "lock_iam_user", "user": "test@acme", "status": "locked"
    })
    with patch("app.connectors.executors.aws.lock_iam_user.execute", mock_iam_execute):
        result = await execute(
            {"user_identifier": "test@acme", "systems": ["aws_iam"]},
            ["asset-uuid-1"], None
        )
    assert result["lockout_status"]["aws_iam"] == "locked"
    mock_iam_execute.assert_called_once()


@pytest.mark.asyncio
async def test_lockout_tolerates_partial_system_failure():
    from app.connectors.executors.nexplane_agent.emergency_user_lockout import execute

    with patch("app.connectors.executors.aws.lock_iam_user.execute",
               AsyncMock(side_effect=Exception("AWS unreachable"))):
        result = await execute(
            {"user_identifier": "test@acme", "systems": ["aws_iam"]},
            ["asset-uuid-1"], None
        )
    assert result["lockout_status"]["aws_iam"] == "failed"
    assert len(result["errors"]) == 1
