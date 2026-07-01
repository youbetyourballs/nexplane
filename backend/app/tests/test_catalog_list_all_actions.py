# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Run with --noconftest."""
from app.connectors.catalog_service import CatalogService

def test_list_all_actions_flattens_with_connector_type():
    cs = CatalogService.__new__(CatalogService)
    cs._catalog = {"acme": [{"action_id": "do_x", "group": "G"}], "other": [{"action_id": "do_y"}]}
    out = cs.list_all_actions()
    keys = {(a["connector_type"], a["action_id"]) for a in out}
    assert ("acme", "do_x") in keys and ("other", "do_y") in keys
    got = next(a for a in out if a["action_id"] == "do_x")
    assert got["group"] == "G"
