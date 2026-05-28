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


# ---------------------------------------------------------------------------
# recurring_job_service — cron helpers
# ---------------------------------------------------------------------------
from app.services.recurring_job_service import compute_next_run


def test_compute_next_run_returns_future():
    next_run = compute_next_run("0 2 * * *")
    assert next_run > datetime.now(tz=timezone.utc)


def test_compute_next_run_daily_2am():
    next_run = compute_next_run("0 2 * * *")
    assert next_run.hour == 2
    assert next_run.minute == 0
