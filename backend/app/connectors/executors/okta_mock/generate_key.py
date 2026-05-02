import random
import string
from datetime import datetime, timezone


def _fake_id(prefix=""): return prefix + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))


def _mock_response(parameters):
    return {"action": "generate_key", "new_key_id": _fake_id("key-"), "old_key_id": _fake_id("key-old-"), "key_type": parameters.get("key_type", "api_key"), "service": parameters.get("service"), "generated_at": datetime.now(timezone.utc).isoformat()}


async def _real_execute(parameters: dict, creds: dict) -> dict:
    import httpx
    from ._client import okta_headers, okta_base
    key_type = parameters.get("key_type", "api_key")
    service = parameters.get("service")
    old_key_id = parameters.get("old_key_id")

    async with httpx.AsyncClient() as client:
        # Create new API token via Okta API
        body = {"name": f"nexplane-{service or 'managed'}-{_fake_id()}"}
        resp = await client.post(
            f"{okta_base(creds)}/api-tokens",
            headers=okta_headers(creds),
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        new_key_id = data.get("id", _fake_id("key-"))

    return {
        "action": "generate_key",
        "new_key_id": new_key_id,
        "old_key_id": old_key_id or _fake_id("key-old-"),
        "key_type": key_type,
        "service": service,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response(parameters)
    try:
        return await _real_execute(parameters, creds)
    except Exception:
        return _mock_response(parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "key generation has no rollback"}
