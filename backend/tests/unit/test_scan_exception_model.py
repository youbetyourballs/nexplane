# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
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
    se = ScanException.__new__(ScanException)
    se.status = "pending"
    assert se.status == "pending"
