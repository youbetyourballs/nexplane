# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json
import os

def test_windows_parallel_migration_in_catalog():
    catalog_path = os.path.join(
        os.path.dirname(__file__), "../../app/connectors/catalog/nexplane_agent_migration.json"
    )
    with open(catalog_path) as f:
        catalog = json.load(f)
    action_ids = [a["action_id"] for a in catalog.get("actions", [])]
    assert "windows_parallel_migration" in action_ids, f"windows_parallel_migration not in catalog: {action_ids}"
    action = next(a for a in catalog["actions"] if a["action_id"] == "windows_parallel_migration")
    assert action["executor"] == "nexplane_agent.windows_parallel_migration"
    assert action["rollback_supported"] is True
