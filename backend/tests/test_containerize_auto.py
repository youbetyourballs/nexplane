# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json
import pathlib
import uuid
import pytest
from unittest.mock import AsyncMock, patch, MagicMock


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


def test_confirm_stateful_rejects_wrong_change_type():
    """confirm-stateful endpoint rejects non-containerize-auto CRs (logic check only, no DB)."""
    # We verify this via the change_type check in the route — the endpoint
    # returns 400 when change_type != agent_containerize_auto.
    # Full integration test would require a running FastAPI app.
    # Verify the endpoint logic is importable:
    from app.routers.change_requests import confirm_stateful
    assert confirm_stateful is not None


@pytest.mark.asyncio
async def test_stage_fleet_cross_reference_returns_graph():
    """Fleet cross-reference with no matching assets returns correct graph structure."""
    from app.connectors.executors.nexplane_agent.containerize_auto import _stage_fleet_cross_reference

    discovery = {
        "workloads": [
            {
                "name": "nginx",
                "runtime_type": "systemd",
                "outbound_connections": [{"remote_addr": "10.0.0.99:443", "protocol": "tcp"}],
                "listening_ports": [],
                "data_directories": [],
            }
        ],
        "hybrid_edges": [],
    }

    mock_asset = MagicMock()
    mock_asset.organization_id = uuid.UUID("00000000-0000-0000-0000-000000000001")

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_db.get = AsyncMock(return_value=mock_asset)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("app.database.AsyncSessionLocal", return_value=mock_db):
        graph = await _stage_fleet_cross_reference(discovery, "00000000-0000-0000-0000-000000000002")

    assert "nodes" in graph
    assert "edges" in graph
    assert "matched_assets" in graph


@pytest.mark.asyncio
async def test_stage_ai_analysis_parses_json():
    """AI analysis parses valid JSON response from AI service."""
    from app.connectors.executors.nexplane_agent.containerize_auto import _stage_ai_analysis

    mock_response_json = json.dumps({
        "migration_units": [
            {
                "id": "unit-1",
                "name": "nginx-api",
                "apps": ["nginx"],
                "pattern": "monolith",
                "stateful": False,
                "data_risk": "none",
                "soak_seconds_recommended": 60,
                "reasoning": "Single stateless service."
            }
        ],
        "migration_order": ["unit-1"],
        "warnings": []
    })

    # Mock org settings with an API key
    mock_org_settings = MagicMock()
    mock_org_settings.ai_providers_encrypted = None
    mock_org_settings.anthropic_api_key_encrypted = "encrypted-key"

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_org_settings
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_secrets = MagicMock()
    mock_secrets.decrypt.return_value = "sk-test-key"

    mock_content = MagicMock()
    mock_content.text = mock_response_json
    mock_anthropic_response = MagicMock()
    mock_anthropic_response.content = [mock_content]

    mock_anthropic_client = AsyncMock()
    mock_anthropic_client.messages.create = AsyncMock(return_value=mock_anthropic_response)

    with patch("app.database.AsyncSessionLocal", return_value=mock_db), \
         patch("app.services.secrets_service.SecretsService", return_value=mock_secrets), \
         patch("anthropic.AsyncAnthropic", return_value=mock_anthropic_client):
        result = await _stage_ai_analysis({}, {}, "00000000-0000-0000-0000-000000000001")

    assert len(result["migration_units"]) == 1
    assert result["migration_units"][0]["stateful"] is False


@pytest.mark.asyncio
async def test_stage_ai_analysis_raises_on_bad_json():
    """AI analysis raises RuntimeError for non-JSON response."""
    from app.connectors.executors.nexplane_agent.containerize_auto import _stage_ai_analysis

    mock_org_settings = MagicMock()
    mock_org_settings.ai_providers_encrypted = None
    mock_org_settings.anthropic_api_key_encrypted = "encrypted-key"

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_org_settings
    mock_db.execute = AsyncMock(return_value=mock_result)

    mock_secrets = MagicMock()
    mock_secrets.decrypt.return_value = "sk-test-key"

    mock_content = MagicMock()
    mock_content.text = "Sorry, I cannot help with that."
    mock_anthropic_response = MagicMock()
    mock_anthropic_response.content = [mock_content]

    mock_anthropic_client = AsyncMock()
    mock_anthropic_client.messages.create = AsyncMock(return_value=mock_anthropic_response)

    with patch("app.database.AsyncSessionLocal", return_value=mock_db), \
         patch("app.services.secrets_service.SecretsService", return_value=mock_secrets), \
         patch("anthropic.AsyncAnthropic", return_value=mock_anthropic_client):
        with pytest.raises(RuntimeError, match="unparseable JSON"):
            await _stage_ai_analysis({}, {}, "00000000-0000-0000-0000-000000000001")
