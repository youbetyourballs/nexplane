import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.connectors.executors.santa_sync_server._client import SantaSyncClient


@pytest.fixture
def moroz_creds():
    return {
        "sync_server_url": "http://moroz.internal",
        "auth_token": "test-token",
        "tls_verify": False,
    }


@pytest.fixture
def zentral_creds():
    return {
        "sync_server_url": "https://zentral.internal",
        "auth_token": "ztest-token",
        "tls_verify": True,
    }


@pytest.mark.asyncio
async def test_moroz_get_rules_returns_list(moroz_creds):
    client = SantaSyncClient(moroz_creds)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [
        {"sha256": "abc123", "policy": 1, "rule_type": 1}
    ]
    with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
        rules = await client.get_rules("default")
    assert len(rules) == 1
    assert rules[0]["identifier"] == "abc123"


@pytest.mark.asyncio
async def test_zentral_get_rules_returns_list(zentral_creds):
    client = SantaSyncClient(zentral_creds)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "results": [
            {"target": {"sha256": "def456", "type": "BINARY"}, "policy": "BLOCKLIST"}
        ]
    }
    with patch.object(client._http, "get", new_callable=AsyncMock, return_value=mock_resp):
        rules = await client.get_rules("mygroup")
    assert len(rules) == 1
    assert rules[0]["identifier"] == "def456"


@pytest.mark.asyncio
async def test_push_rules_moroz(moroz_creds):
    client = SantaSyncClient(moroz_creds)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {}
    with patch.object(client._http, "post", new_callable=AsyncMock, return_value=mock_resp):
        result = await client.push_rules("default", [{"rule_type": "denylist", "identifier_type": "binary", "identifier": "abc123"}], mode="merge")
    assert result["pushed"] >= 0
