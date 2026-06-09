import os
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
