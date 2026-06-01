import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid


@pytest.mark.asyncio
async def test_execute_cr_rollback_no_plan_steps():
    """Falls back to executor module when no plan rollback steps defined."""
    from app.services.rollback_executor import execute_cr_rollback

    cr_id = uuid.uuid4()
    mock_db = AsyncMock()

    mock_cr = MagicMock()
    mock_cr.id = cr_id
    mock_cr.change_type = MagicMock()
    mock_cr.change_type.value = "configure_selinux"
    mock_cr.desired_outcome = {}
    mock_cr.organization_id = uuid.uuid4()

    mock_run = MagicMock()
    mock_run.result = {"step": "done"}
    mock_run.status.value = "completed"

    mock_plan = MagicMock()
    mock_plan.generated_steps = []  # no rollback_action steps

    with patch("app.services.rollback_executor._load_cr_and_run",
               new_callable=AsyncMock, return_value=(mock_cr, mock_run, mock_plan)):
        with patch("app.services.rollback_executor._executor_fallback",
                   new_callable=AsyncMock, return_value={"rolled_back": True}) as mock_fallback:
            result = await execute_cr_rollback(cr_id, mock_db)
            mock_fallback.assert_called_once()
            assert result["rolled_back"] is True
