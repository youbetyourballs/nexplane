# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
import json
import logging
import pathlib
import pytest
from app.connectors.catalog_service import ActionCatalogService


def _write_catalog(tmp_path: pathlib.Path, connector_type: str, actions: list) -> pathlib.Path:
    data = {
        "connector_type": connector_type,
        "display_name": connector_type,
        "credential_fields": [],
        "actions": actions,
    }
    (tmp_path / f"{connector_type}.json").write_text(json.dumps(data))
    return tmp_path


def test_commercial_mutating_without_smoke_verified_is_skipped(tmp_path, caplog):
    """Commercial non-read-only action without smoke_verified=true must not load."""
    _write_catalog(tmp_path, "ops_test", [
        {"action_id": "provision", "domain": "commercial", "read_only": False},
    ])
    with caplog.at_level(logging.ERROR, logger="app.connectors.catalog_service"):
        svc = ActionCatalogService(tmp_path)
    loaded = [a["action_id"] for a in svc._catalog.get("ops_test", [])]
    assert "provision" not in loaded
    assert "not smoke-verified" in caplog.text


def test_commercial_mutating_with_smoke_verified_true_loads(tmp_path):
    """Commercial non-read-only action with smoke_verified=true must load."""
    _write_catalog(tmp_path, "ops_test", [
        {"action_id": "provision", "domain": "commercial", "read_only": False, "smoke_verified": True},
    ])
    svc = ActionCatalogService(tmp_path)
    loaded = [a["action_id"] for a in svc._catalog.get("ops_test", [])]
    assert "provision" in loaded


def test_commercial_mutating_smoke_verified_false_is_skipped(tmp_path, caplog):
    """Explicit smoke_verified=false must also be skipped."""
    _write_catalog(tmp_path, "ops_test", [
        {"action_id": "terminate_instance", "domain": "commercial", "read_only": False, "smoke_verified": False},
    ])
    with caplog.at_level(logging.ERROR, logger="app.connectors.catalog_service"):
        svc = ActionCatalogService(tmp_path)
    loaded = [a["action_id"] for a in svc._catalog.get("ops_test", [])]
    assert "terminate_instance" not in loaded
    assert "not smoke-verified" in caplog.text


def test_commercial_read_only_loads_without_smoke_verified(tmp_path):
    """Commercial read-only action must load even without smoke_verified (exempt)."""
    _write_catalog(tmp_path, "ops_test", [
        {"action_id": "list_customers", "domain": "commercial", "read_only": True},
    ])
    svc = ActionCatalogService(tmp_path)
    loaded = [a["action_id"] for a in svc._catalog.get("ops_test", [])]
    assert "list_customers" in loaded


def test_core_mutating_loads_without_smoke_verified(tmp_path):
    """Core domain mutating action is exempt — must load without smoke_verified."""
    _write_catalog(tmp_path, "aws_test", [
        {"action_id": "tag_resource", "domain": "core", "read_only": False},
    ])
    svc = ActionCatalogService(tmp_path)
    loaded = [a["action_id"] for a in svc._catalog.get("aws_test", [])]
    assert "tag_resource" in loaded
