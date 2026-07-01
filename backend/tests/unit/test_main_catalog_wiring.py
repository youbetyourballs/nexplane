# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pathlib
from app.config import Settings


def test_init_catalog_service_commercial_path_conversion(tmp_path, monkeypatch):
    """Test that NEXPLANE_COMMERCIAL_CATALOG_PATH is correctly converted to Path."""
    monkeypatch.setenv("NEXPLANE_COMMERCIAL_CATALOG_PATH", str(tmp_path))

    # Reload Settings to pick up the env var
    s = Settings()
    assert s.NEXPLANE_COMMERCIAL_CATALOG_PATH == str(tmp_path)

    # Test the conversion logic that main.py uses
    commercial_path = (
        pathlib.Path(s.NEXPLANE_COMMERCIAL_CATALOG_PATH)
        if s.NEXPLANE_COMMERCIAL_CATALOG_PATH
        else None
    )
    assert commercial_path == tmp_path
    assert isinstance(commercial_path, pathlib.Path)


def test_init_catalog_service_commercial_path_none_when_unset():
    """When NEXPLANE_COMMERCIAL_CATALOG_PATH is not set, commercial_catalog_dir is None."""
    # Use a fresh Settings instance with no env var set
    import os
    old_val = os.environ.pop("NEXPLANE_COMMERCIAL_CATALOG_PATH", None)
    try:
        s = Settings()
        commercial_path = (
            pathlib.Path(s.NEXPLANE_COMMERCIAL_CATALOG_PATH)
            if s.NEXPLANE_COMMERCIAL_CATALOG_PATH
            else None
        )
        assert commercial_path is None
    finally:
        if old_val is not None:
            os.environ["NEXPLANE_COMMERCIAL_CATALOG_PATH"] = old_val
