# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.models.change_request import ChangeType

def test_scan_for_references_change_type_exists():
    assert ChangeType.scan_for_references == "scan_for_references"

def test_update_reference_change_type_exists():
    assert ChangeType.update_reference == "update_reference"
