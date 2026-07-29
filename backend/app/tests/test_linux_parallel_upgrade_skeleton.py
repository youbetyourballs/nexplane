# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from app.connectors.executors.nexplane_agent import linux_parallel_upgrade as lpu


def test_definition_has_required_keys():
    assert lpu.DEFINITION["name"] == "linux_parallel_upgrade"
    assert lpu.DEFINITION["rollback_supported"] is True


def test_execute_is_callable():
    assert callable(lpu.execute)


def test_rollback_is_callable():
    assert callable(lpu.rollback)
