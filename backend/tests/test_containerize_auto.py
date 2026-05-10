import json
import pathlib
import pytest


def _load_ct(name: str) -> dict:
    path = (
        pathlib.Path(__file__).parent.parent
        / "app"
        / "connectors"
        / "change_type_definitions"
        / f"{name}.json"
    )
    return json.loads(path.read_text())


def test_change_type_enum_has_agent_containerize_auto():
    from app.models.change_request import ChangeType
    assert ChangeType.agent_containerize_auto == "agent_containerize_auto"


def test_change_type_definition_uses_ai():
    ct = _load_ct("agent_containerize_auto")
    assert ct.get("uses_ai") is True


def test_change_type_definition_has_required_parameters():
    ct = _load_ct("agent_containerize_auto")
    params = ct["parameters"]
    assert "registry" in params
    assert "target_cluster_id" in params
    assert params["registry"]["required"] is True
    assert params["target_cluster_id"]["required"] is True


def test_change_type_definition_has_preflight_checks():
    ct = _load_ct("agent_containerize_auto")
    assert "asset_exists" in ct["preflight_checks"]
    assert "agent_registered" in ct["preflight_checks"]
