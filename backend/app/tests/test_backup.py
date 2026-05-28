# backend/app/tests/test_backup.py
import pytest
from app.models.backup_target import BackupTarget, BackupTargetStatus


def test_backup_target_status_enum():
    assert BackupTargetStatus.healthy == "healthy"
    assert BackupTargetStatus.overdue == "overdue"
    assert BackupTargetStatus.unprotected == "unprotected"
