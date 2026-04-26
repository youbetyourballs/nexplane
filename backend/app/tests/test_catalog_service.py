import pathlib
import pytest
from app.connectors.catalog_service import ActionCatalogService, ActionOption

CATALOG_DIR = pathlib.Path(__file__).parent.parent / "connectors" / "catalog"


def test_load_builds_connector_index():
    svc = ActionCatalogService(CATALOG_DIR)
    assert "cloudflare_mock" in svc._catalog
    assert len(svc._catalog["cloudflare_mock"]) == 5


def test_load_builds_generic_index():
    svc = ActionCatalogService(CATALOG_DIR)
    assert "update_dns_record" in svc._generic_index
    options = svc._generic_index["update_dns_record"]
    assert len(options) == 1
    assert options[0].connector_type == "cloudflare_mock"
    assert options[0].action_id == "update_dns_record"
    assert options[0].execution_tier == 1


def test_load_indexes_all_connectors():
    svc = ActionCatalogService(CATALOG_DIR)
    assert set(svc._catalog.keys()) == {
        "cloudflare_mock", "aws_mock", "okta_mock", "ssh_mock", "paloalto_mock"
    }
