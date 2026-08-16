# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest


@pytest.fixture(scope="session", autouse=True)
def create_tables():
    """Override the session-scoped create_tables fixture from parent conftest.
    Executor tests don't need the database, so skip table creation."""
    yield
