import pytest
from app.models.recurring_job import RecurringJob, RecurringJobType
from app.schemas.recurring_job import RecurringJobCreate, RecurringJobRead, RecurringJobUpdate
import uuid
from datetime import datetime, timezone


def test_recurring_job_type_enum():
    assert RecurringJobType.backup == "backup"
    assert RecurringJobType.scheduled_restore == "scheduled_restore"
    assert RecurringJobType.scheduled_op == "scheduled_op"


def test_recurring_job_create_schema():
    data = RecurringJobCreate(
        name="Daily DB Backup",
        job_type=RecurringJobType.backup,
        action_id="ssm_command",
        parameters={"instance_id": "i-123", "commands": ["echo hi"]},
        target_description="nexplane postgres DB",
        cron_expression="0 2 * * *",
        schedule_preset="daily",
        schedule_hour=2,
    )
    assert data.name == "Daily DB Backup"
    assert data.job_type == RecurringJobType.backup
    assert data.connector_id is None


def test_recurring_job_update_schema_partial():
    update = RecurringJobUpdate(enabled=False)
    dumped = update.model_dump(exclude_none=True)
    assert dumped == {"enabled": False}
