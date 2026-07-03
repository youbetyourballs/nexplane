# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.models.change_request import ChangeType


def test_new_change_types_exist():
    assert ChangeType("server_backup") == ChangeType.server_backup
    assert ChangeType("server_snapshot") == ChangeType.server_snapshot
    assert ChangeType("server_capture") == ChangeType.server_capture
    assert ChangeType("restore_server") == ChangeType.restore_server


def test_catalog_has_new_actions():
    import json, pathlib
    # tests/unit/test_backup_executors.py -> tests -> . (backend dir)
    test_file = pathlib.Path(__file__)
    backend_dir = test_file.parent.parent.parent
    catalog_path = backend_dir / "app/connectors/catalog/nexplane_agent.json"
    catalog = json.loads(catalog_path.read_text())
    action_ids = {a["action_id"] for a in catalog["actions"]}
    assert "server_backup" in action_ids
    assert "server_snapshot" in action_ids
    assert "server_capture" in action_ids
    assert "restore_server" in action_ids
