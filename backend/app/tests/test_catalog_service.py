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
        "cloudflare_mock", "aws_mock", "okta_mock", "ssh_mock", "paloalto_mock",
        "active_directory_mock", "crowdstrike_mock", "tenable_mock", "azure_mock",
    }


def test_get_options_for_action_returns_sorted_by_tier():
    svc = ActionCatalogService(CATALOG_DIR)
    options = svc.get_options_for_action("execute_template")
    assert len(options) == 1
    assert options[0].connector_type == "ssh_mock"
    assert options[0].execution_tier == 5


def test_get_options_filters_by_asset_type():
    svc = ActionCatalogService(CATALOG_DIR)
    options = svc.get_options_for_action("update_dns_record", asset_types=["server"])
    assert len(options) == 0

    options = svc.get_options_for_action("update_dns_record", asset_types=["dns_zone"])
    assert len(options) == 1


def test_get_options_filters_by_active_connectors():
    svc = ActionCatalogService(CATALOG_DIR)
    options = svc.get_options_for_action("update_dns_record", active_connector_types=["aws_mock"])
    assert len(options) == 0

    options = svc.get_options_for_action("update_dns_record", active_connector_types=["cloudflare_mock"])
    assert len(options) == 1


def test_get_action_def_returns_correct_def():
    svc = ActionCatalogService(CATALOG_DIR)
    defn = svc.get_action_def("cloudflare_mock", "update_dns_record")
    assert defn["executor"] == "cloudflare_mock.update_dns_record"
    assert defn["rollback_action"] == "restore_dns_record"


def test_get_action_def_raises_for_unknown():
    svc = ActionCatalogService(CATALOG_DIR)
    with pytest.raises(KeyError):
        svc.get_action_def("cloudflare_mock", "does_not_exist")


def test_list_generic_actions_change_only():
    svc = ActionCatalogService(CATALOG_DIR)
    actions = svc.list_generic_actions(action_type="change")
    assert "update_dns_record" in actions
    assert "capture_dns_record" in actions


def test_list_generic_actions_all():
    svc = ActionCatalogService(CATALOG_DIR)
    all_actions = svc.list_generic_actions()
    assert len(all_actions) > 0


def test_get_executor_returns_module():
    svc = ActionCatalogService(CATALOG_DIR)
    mod = svc.get_executor("cloudflare_mock", "update_dns_record")
    assert hasattr(mod, "execute")
    assert hasattr(mod, "rollback")


def test_get_options_returns_copy_not_internal_list():
    svc = ActionCatalogService(CATALOG_DIR)
    options = svc.get_options_for_action("update_dns_record")
    original_len = len(svc._generic_index["update_dns_record"])
    options.append(None)  # mutate the returned list
    assert len(svc._generic_index["update_dns_record"]) == original_len  # internal list unchanged


def test_list_generic_actions_unknown_type_returns_empty():
    svc = ActionCatalogService(CATALOG_DIR)
    assert svc.list_generic_actions(action_type="nonexistent") == []


def test_singleton_init_and_get():
    from app.connectors.catalog_service import init_catalog_service, get_catalog_service
    init_catalog_service(CATALOG_DIR)
    svc = get_catalog_service()
    assert isinstance(svc, ActionCatalogService)
    assert "cloudflare_mock" in svc._catalog


def test_all_catalog_executors_resolve():
    import pathlib
    catalog_dir = pathlib.Path(__file__).parent.parent / "connectors" / "catalog"
    svc = ActionCatalogService(catalog_dir)
    for connector_type, actions in svc._catalog.items():
        for action in actions:
            mod = svc.get_executor(connector_type, action["action_id"])
            assert hasattr(mod, "execute"), (
                f"{connector_type}.{action['action_id']} executor missing execute()"
            )
