# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json
import os
import pathlib
import pytest
from app.config import Settings


def test_nexplane_edition_defaults_to_core():
    s = Settings()
    assert s.NEXPLANE_EDITION == "core"


def test_nexplane_edition_reads_from_env(monkeypatch):
    monkeypatch.setenv("NEXPLANE_EDITION", "commercial")
    s = Settings()
    assert s.NEXPLANE_EDITION == "commercial"


def test_commercial_catalog_path_defaults_to_none():
    s = Settings()
    assert s.NEXPLANE_COMMERCIAL_CATALOG_PATH is None


def test_commercial_catalog_path_reads_from_env(monkeypatch):
    monkeypatch.setenv("NEXPLANE_COMMERCIAL_CATALOG_PATH", "/mnt/commercial/catalog")
    s = Settings()
    assert s.NEXPLANE_COMMERCIAL_CATALOG_PATH == "/mnt/commercial/catalog"


def _make_catalog(tmpdir, connector_type, action_id, executor):
    catalog_dir = pathlib.Path(tmpdir)
    catalog_dir.mkdir(parents=True, exist_ok=True)
    action = {
        "action_id": action_id,
        "name": action_id,
        "description": "test",
        "executor": executor,
        "parameters": []
    }
    catalog = {
        "connector_type": connector_type,
        "actions": [action]
    }
    (catalog_dir / f"{connector_type}.json").write_text(json.dumps(catalog))
    return catalog_dir


def test_commercial_catalog_loaded_alongside_core(tmp_path):
    from app.connectors.catalog_service import ActionCatalogService
    core_dir = _make_catalog(tmp_path / "core", "aws", "list_instances", "aws.list_instances")
    commercial_dir = _make_catalog(tmp_path / "commercial", "ops_provision", "provision_instance", "commercial.ops_provision.provision_instance")
    svc = ActionCatalogService(core_dir, commercial_catalog_dir=commercial_dir)
    types = svc.list_connector_types()
    assert "aws" in types
    assert "ops_provision" in types


def test_commercial_catalog_dir_missing_is_ignored(tmp_path):
    from app.connectors.catalog_service import ActionCatalogService
    core_dir = _make_catalog(tmp_path / "core", "aws", "list_instances", "aws.list_instances")
    missing = tmp_path / "nonexistent"
    svc = ActionCatalogService(core_dir, commercial_catalog_dir=missing)
    types = svc.list_connector_types()
    assert "aws" in types  # core loaded fine


def test_commercial_actions_accessible_via_get_action_def(tmp_path):
    from app.connectors.catalog_service import ActionCatalogService
    core_dir = _make_catalog(tmp_path / "core", "aws", "list_instances", "aws.list_instances")
    commercial_dir = _make_catalog(tmp_path / "commercial", "ops_provision", "provision_instance", "commercial.ops_provision.provision_instance")
    svc = ActionCatalogService(core_dir, commercial_catalog_dir=commercial_dir)
    action = svc.get_action_def("ops_provision", "provision_instance")
    assert action["action_id"] == "provision_instance"
    assert action["executor"] == "commercial.ops_provision.provision_instance"


def test_get_executor_commercial_loads_from_filesystem(tmp_path):
    import types as pytypes
    from app.connectors.catalog_service import ActionCatalogService

    # Build: pkg/catalog/ops_provision.json (with commercial.ops_provision.provision_instance executor ref)
    #        pkg/executors/ops_provision/provision_instance.py
    pkg = tmp_path / "pkg"
    pkg_catalog = pkg / "catalog"
    pkg_executors = pkg / "executors" / "ops_provision"
    pkg_catalog.mkdir(parents=True)
    pkg_executors.mkdir(parents=True)

    # Create the catalog JSON
    catalog_dir = _make_catalog(
        pkg_catalog,
        "ops_provision",
        "provision_instance",
        "commercial.ops_provision.provision_instance",
    )

    # Create the executor Python file
    (pkg_executors / "provision_instance.py").write_text(
        "EXECUTOR_NAME = 'provision_instance'\n"
    )

    # Create core catalog
    core_dir = _make_catalog(tmp_path / "core", "aws", "list_instances", "aws.list_instances")

    # Initialize service with commercial_catalog_dir pointing to pkg/catalog
    # executors will be found at pkg/catalog.parent / "executors" = pkg/executors
    svc = ActionCatalogService(core_dir, commercial_catalog_dir=pkg_catalog)
    mod = svc.get_executor("ops_provision", "provision_instance")
    assert isinstance(mod, pytypes.ModuleType)
    assert mod.EXECUTOR_NAME == "provision_instance"


def test_get_executor_commercial_missing_file_raises(tmp_path):
    from app.connectors.catalog_service import ActionCatalogService

    pkg = tmp_path / "pkg"
    pkg_catalog = pkg / "catalog"
    pkg_catalog.mkdir(parents=True)

    # Create the catalog JSON (but don't create the executor file)
    catalog_dir = _make_catalog(
        pkg_catalog,
        "ops_provision",
        "provision_instance",
        "commercial.ops_provision.provision_instance",
    )

    # Create core catalog
    core_dir = _make_catalog(tmp_path / "core", "aws", "list_instances", "aws.list_instances")

    svc = ActionCatalogService(core_dir, commercial_catalog_dir=pkg_catalog)
    with pytest.raises(ImportError):
        svc.get_executor("ops_provision", "provision_instance")
