from __future__ import annotations

from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Enroll a TOTP factor for an Okta user. Rollback unenrolls it."""
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    factor_type = parameters.get("factor_type", "token:software:totp")
    if not creds:
        return {
            "action": "enforce_mfa",
            "user_id": user_id,
            "factor_type": factor_type,
            "factor_id": "mock-factor-id",
            "mock": True,
        }
    import httpx
    from ._client import okta_headers, okta_base
    base = okta_base(creds)
    headers = okta_headers(creds)
    # provider mapping for TOTP
    provider_map = {
        "token:software:totp": "GOOGLE",
        "token:software:totp:okta": "OKTA",
    }
    provider = provider_map.get(factor_type, "GOOGLE")
    payload = {"factorType": factor_type, "provider": provider}
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{base}/users/{user_id}/factors", headers=headers, json=payload)
        resp.raise_for_status()
        factor = resp.json()
    return {
        "action": "enforce_mfa",
        "user_id": user_id,
        "factor_type": factor_type,
        "factor_id": factor.get("id"),
        "status": factor.get("status"),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Unenroll (delete) the factor that was enrolled."""
    creds = getattr(connector, "credentials", {})
    user_id = execution_result.get("user_id") or parameters.get("user_id")
    factor_id = execution_result.get("factor_id")
    if not factor_id:
        return {"rolled_back": False, "reason": "no factor_id to unenroll"}
    if not creds:
        return {"rolled_back": True, "factor_id": factor_id, "mock": True}
    import httpx
    from ._client import okta_headers, okta_base
    base = okta_base(creds)
    headers = okta_headers(creds)
    async with httpx.AsyncClient() as client:
        resp = await client.delete(f"{base}/users/{user_id}/factors/{factor_id}", headers=headers)
        resp.raise_for_status()
    return {"rolled_back": True, "factor_id": factor_id, "action": "unenroll_factor"}
