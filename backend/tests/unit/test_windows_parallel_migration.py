# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.connectors.executors.nexplane_agent.windows_parallel_migration import (
    _detect_current_version,
    _check_upgrade_path,
    _determine_rollback_status,
    DEFINITION,
)


def test_detect_current_version():
    assert _detect_current_version("Microsoft Windows Server 2022 Datacenter") == "2022"
    assert _detect_current_version("Microsoft Windows Server 2019 Standard") == "2019"
    assert _detect_current_version("Microsoft Windows Server 2016 Datacenter") == "2016"
    assert _detect_current_version("Unknown OS") == "unknown"


def test_check_upgrade_path_valid():
    assert _check_upgrade_path("Microsoft Windows Server 2016 Datacenter", "2019") is None
    assert _check_upgrade_path("Microsoft Windows Server 2019 Standard", "2022") is None


def test_check_upgrade_path_invalid():
    err = _check_upgrade_path("Microsoft Windows Server 2016 Datacenter", "2022")
    assert err is not None
    assert "2016" in err and "2022" in err


def test_check_upgrade_path_already_target():
    err = _check_upgrade_path("Microsoft Windows Server 2022 Datacenter", "2022")
    assert err is not None
    assert "already" in err.lower()


def test_determine_rollback_status_success():
    # No "error" key = success
    assert _determine_rollback_status({"actions": ["reversed_cutover"]}) is True


def test_determine_rollback_status_failure():
    # "error" key present = failure
    assert _determine_rollback_status({"actions": [], "error": "something failed"}) is False


def test_definition_has_required_fields():
    assert DEFINITION["name"] == "windows_parallel_migration"
    assert DEFINITION["rollback_supported"] is True
