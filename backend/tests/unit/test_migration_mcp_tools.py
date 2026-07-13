# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest


def test_migration_mcp_tools_importable():
    from app.mcp_tools import migration
    assert hasattr(migration, "list_application_profiles")
    assert hasattr(migration, "get_application_profile")
    assert hasattr(migration, "list_database_instances")
