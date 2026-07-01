# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    user_id = parameters["user_id"]
    if not creds:
        return {"action": "reset_mfa", "user_id": user_id, "methods_deleted": 0}
    from ._client import get_access_token, get_graph_client
    token = await get_access_token(creds)
    async with get_graph_client(token) as client:
        resp = await client.get(f"/users/{user_id}/authentication/methods")
        resp.raise_for_status()
        methods = resp.json().get("value", [])
        deleted = 0
        for method in methods:
            method_type = method.get("@odata.type", "")
            method_id = method.get("id")
            if "password" not in method_type.lower() and method_id:
                try:
                    await client.delete(f"/users/{user_id}/authentication/methods/{method_id}")
                    deleted += 1
                except Exception:
                    pass
    return {"action": "reset_mfa", "user_id": user_id, "methods_deleted": deleted}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "cannot restore deleted MFA methods"}
