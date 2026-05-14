import pytest
from unittest.mock import MagicMock, patch


@pytest.mark.asyncio
async def test_enforce_mfa_attaches_condition_policy():
    mock_iam = MagicMock()
    with patch("boto3.Session") as mock_session:
        mock_session.return_value.client.return_value = mock_iam
        from app.connectors.executors.aws.enforce_mfa_iam import execute
        result = await execute({"user_name": "test-user"}, ["asset-1"], None)
    mock_iam.put_user_policy.assert_called_once()
    assert "mfa" in mock_iam.put_user_policy.call_args[1]["PolicyName"].lower()
    assert result["status"] == "mfa_required"
