# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.models.scan_exception import ScanException

def test_scan_exception_has_required_fields():
    required = [
        "id", "organization_id", "scan_cr_id", "consumer_asset_id",
        "matched_term", "location", "surface", "snippet",
        "reason", "suggested_action", "confidence", "status",
        "resolution_notes", "resolved_by_cr_id", "created_at", "updated_at",
    ]
    mapper_columns = {c.key for c in ScanException.__mapper__.column_attrs}
    for field in required:
        assert field in mapper_columns, f"Missing column: {field}"

def test_scan_exception_status_values():
    # Just verify the status column exists and has correct properties
    assert hasattr(ScanException, 'status')
    status_col = ScanException.__mapper__.columns['status']
    assert status_col.nullable is False
