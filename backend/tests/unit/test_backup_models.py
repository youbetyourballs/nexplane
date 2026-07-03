# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.models.backup_storage import BackupStorage, RecoveryToken
from app.models.backup_target import BackupTarget


def test_backup_storage_columns():
    cols = {c.key for c in BackupStorage.__table__.columns}
    for field in ("id", "organization_id", "name", "storage_type", "config", "is_org_default", "created_at"):
        assert field in cols, f"Missing column: {field}"


def test_recovery_token_columns():
    cols = {c.key for c in RecoveryToken.__table__.columns}
    for field in ("id", "token_hash", "organization_id", "asset_id", "restore_cr_id", "expires_at", "used", "created_at"):
        assert field in cols, f"Missing column: {field}"


def test_backup_target_gains_storage_id_and_asset_type():
    cols = {c.key for c in BackupTarget.__table__.columns}
    assert "storage_id" in cols, "Missing column: storage_id"
    assert "asset_type" in cols, "Missing column: asset_type"


def test_backup_storage_importable_from_app_models():
    import app.models  # noqa: F401 — triggers __init__ imports
    from app.models.backup_storage import BackupStorage as BS, RecoveryToken as RT
    assert BS.__tablename__ == "backup_storage"
    assert RT.__tablename__ == "recovery_tokens"
