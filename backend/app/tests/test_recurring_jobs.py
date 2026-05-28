import pytest
from app.models.recurring_job import RecurringJob, RecurringJobType


def test_recurring_job_type_enum():
    assert RecurringJobType.backup == "backup"
    assert RecurringJobType.scheduled_restore == "scheduled_restore"
    assert RecurringJobType.scheduled_op == "scheduled_op"
