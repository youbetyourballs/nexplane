# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
from app.models.change_request import ChangeType

def test_catalog_action_value_exists():
    assert ChangeType.catalog_action.value == "catalog_action"
