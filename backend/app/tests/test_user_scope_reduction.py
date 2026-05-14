import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.asyncio
async def test_demote_to_readonly_attaches_deny_write_policy():
    mock_iam = MagicMock()
    with patch("boto3.Session") as mock_session:
        mock_session.return_value.client.return_value = mock_iam
        from app.connectors.executors.aws.scope_reduction_iam import execute
        result = await execute(
            {"user_name": "test-admin", "mode": "demote_to_readonly"},
            ["asset-1"], None
        )
    mock_iam.put_user_policy.assert_called_once()
    assert "deny-write" in mock_iam.put_user_policy.call_args[1]["PolicyName"]
    assert result["mode"] == "demote_to_readonly"
