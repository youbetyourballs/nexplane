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


def test_resolve_provider_config_returns_tuple():
    from app.services.ai_service import _resolve_provider_config
    from app.services.secrets_service import SecretsService

    svc = SecretsService("test-secret-key-32-chars-padding!!")

    class FakeSettings:
        anthropic_api_key_encrypted = None
        ai_providers_encrypted = svc.encrypt_json({
            "default": "openai",
            "providers": {
                "openai": {"api_key": "sk-test", "model": "gpt-4o-mini"},
            },
        })

    provider, api_key, model = _resolve_provider_config(FakeSettings(), svc)
    assert provider == "openai"
    assert api_key == "sk-test"
    assert model == "gpt-4o-mini"


def test_resolve_provider_config_defaults_model_for_anthropic():
    from app.services.ai_service import _resolve_provider_config
    from app.services.secrets_service import SecretsService

    svc = SecretsService("test-secret-key-32-chars-padding!!")

    class FakeSettings:
        anthropic_api_key_encrypted = None
        ai_providers_encrypted = svc.encrypt_json({
            "default": "anthropic",
            "providers": {"anthropic": {"api_key": "sk-ant-test"}},
        })

    provider, api_key, model = _resolve_provider_config(FakeSettings(), svc)
    assert provider == "anthropic"
    assert api_key == "sk-ant-test"
    assert model == "claude-sonnet-4-6"


def test_resolve_provider_config_falls_back_to_legacy_key():
    from app.services.ai_service import _resolve_provider_config
    from app.services.secrets_service import SecretsService

    svc = SecretsService("test-secret-key-32-chars-padding!!")

    class FakeSettings:
        ai_providers_encrypted = None
        anthropic_api_key_encrypted = svc.encrypt("sk-ant-legacy")

    provider, api_key, model = _resolve_provider_config(FakeSettings(), svc)
    assert provider == "anthropic"
    assert api_key == "sk-ant-legacy"
    assert model == "claude-sonnet-4-6"
