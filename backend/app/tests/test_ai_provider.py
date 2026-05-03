import pytest


@pytest.mark.asyncio
async def test_set_provider_stores_model(auth_client):
    resp = await auth_client.put(
        "/settings/ai-providers/anthropic",
        json={"api_key": "sk-ant-test-key-for-model-test", "model": "claude-opus-4-7"},
    )
    assert resp.status_code == 200

    get_resp = await auth_client.get("/settings/ai-providers")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["providers"]["anthropic"]["model"] == "claude-opus-4-7"


@pytest.mark.asyncio
async def test_set_provider_without_model_returns_none(auth_client):
    resp = await auth_client.put(
        "/settings/ai-providers/anthropic",
        json={"api_key": "sk-ant-test-key-no-model"},
    )
    assert resp.status_code == 200

    get_resp = await auth_client.get("/settings/ai-providers")
    data = get_resp.json()
    assert data["providers"]["anthropic"]["model"] is None
