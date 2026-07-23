# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

def test_change_type_enum_has_db_major_version_upgrade():
    from app.models.change_request import ChangeType
    assert ChangeType.db_major_version_upgrade == "db_major_version_upgrade"


def test_catalog_definition_exists():
    import json, os
    path = os.path.join(
        os.path.dirname(__file__),
        "../../../../app/connectors/change_type_definitions/db_major_version_upgrade.json",
    )
    assert os.path.exists(path), f"Catalog definition not found at {path}"
    with open(path) as f:
        data = json.load(f)
    assert data["change_type"] == "db_major_version_upgrade"
    assert data["rollback_capability"] == "full"
    required_params = {"engine", "target_version"}
    for p in required_params:
        assert p in data["parameters"], f"Missing required param: {p}"
        assert data["parameters"][p]["required"] is True
