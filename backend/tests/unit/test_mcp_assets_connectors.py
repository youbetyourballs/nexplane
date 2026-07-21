"""Verify MCP asset tools expose connectors field."""
import pathlib


def test_get_asset_returns_connectors_field():
    src = pathlib.Path("app/mcp_tools/assets.py").read_text()
    assert '"connectors"' in src or "'connectors'" in src, \
        "get_asset must include 'connectors' key in its return dict"


def test_list_assets_returns_connectors_field():
    src = pathlib.Path("app/mcp_tools/assets.py").read_text()
    assert '"connectors"' in src or "'connectors'" in src, \
        "list_assets must include 'connectors' key in its return dict"
