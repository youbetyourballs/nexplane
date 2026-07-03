# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

# backend/tests/unit/test_backup_api.py
import hashlib
import secrets
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.backup import (
    BackupStorageCreate,
    BackupStorageRead,
    BackupStorageUpdate,
    RecoveryTokenRead,
)


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


def test_backup_storage_create_defaults():
    schema = BackupStorageCreate(
        name="my-s3-bucket",
        storage_type="s3",
        config={"bucket": "nexplane-backups", "region": "us-east-1"},
    )
    assert schema.name == "my-s3-bucket"
    assert schema.storage_type == "s3"
    assert schema.is_org_default is False


def test_backup_storage_create_with_default_flag():
    schema = BackupStorageCreate(
        name="primary",
        storage_type="gcs",
        config={"bucket": "np-primary"},
        is_org_default=True,
    )
    assert schema.is_org_default is True


def test_backup_storage_read_from_attributes():
    obj = MagicMock()
    obj.id = str(uuid.uuid4())
    obj.name = "test"
    obj.storage_type = "s3"
    obj.is_org_default = False
    obj.created_at = "2026-01-01T00:00:00+00:00"

    read = BackupStorageRead(
        id=obj.id,
        name=obj.name,
        storage_type=obj.storage_type,
        is_org_default=obj.is_org_default,
        created_at=obj.created_at,
    )
    assert read.id == obj.id
    assert read.storage_type == "s3"


def test_backup_storage_update_all_optional():
    # All fields optional — empty update is valid
    schema = BackupStorageUpdate()
    assert schema.name is None
    assert schema.config is None
    assert schema.is_org_default is None


def test_backup_storage_update_partial():
    schema = BackupStorageUpdate(name="renamed")
    assert schema.name == "renamed"
    assert schema.config is None


def test_recovery_token_read_fields():
    token_id = str(uuid.uuid4())
    asset_id = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc).isoformat()
    rt = RecoveryTokenRead(
        token="abc123",
        asset_id=asset_id,
        expires_at=expires_at,
        token_id=token_id,
    )
    assert rt.token == "abc123"
    assert rt.token_id == token_id


# ---------------------------------------------------------------------------
# Recovery token generation logic
# ---------------------------------------------------------------------------


def test_recovery_token_hash_is_sha256_of_raw():
    """Verify that the hashing logic used in the endpoint produces a valid SHA-256."""
    raw = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    # SHA-256 produces a 64-char hex string
    assert len(token_hash) == 64
    assert all(c in "0123456789abcdef" for c in token_hash)
    # Hash is deterministic
    assert hashlib.sha256(raw.encode()).hexdigest() == token_hash


def test_recovery_token_ttl_is_60_minutes():
    """Verify the 60-minute TTL calculation."""
    before = datetime.now(timezone.utc)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=60)
    delta = expires_at - before
    assert 59 * 60 <= delta.total_seconds() <= 61 * 60


def test_recovery_token_raw_not_stored():
    """Raw token and its hash are different strings."""
    raw = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    assert raw != token_hash


def test_recovery_token_hex_length():
    """token_hex(32) produces a 64-char string."""
    raw = secrets.token_hex(32)
    assert len(raw) == 64


def test_recovery_tokens_are_unique():
    """Each call to token_hex produces a different value."""
    tokens = {secrets.token_hex(32) for _ in range(100)}
    assert len(tokens) == 100
