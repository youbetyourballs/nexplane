# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
from datetime import datetime, timezone
from ._client import get_keycloak_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    username = parameters.get("username") or parameters.get("user_identifier", "")
    client = get_keycloak_client(connector)
    if not client:
        # Check inline credentials
        if parameters.get("keycloak_url"):
            from ._client import KeycloakClient
            client = KeycloakClient(
                url=parameters["keycloak_url"],
                realm=parameters.get("keycloak_realm", "master"),
                client_id=parameters.get("keycloak_client_id", "admin-cli"),
                username=parameters.get("keycloak_admin"),
                password=parameters.get("keycloak_password"),
            )
        else:
            return {"action": "keycloak_disable_user", "status": "skipped",
                    "reason": "no_keycloak_credentials", "username": username}
    result = await client.disable_user(username)
    try:
        await client.revoke_sessions(username)
    except Exception:
        pass
    return {
        "action": "keycloak_disable_user", **result,
        "disabled_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    username = parameters.get("username") or parameters.get("user_identifier", "")
    client = get_keycloak_client(connector)
    if not client and parameters.get("keycloak_url"):
        from ._client import KeycloakClient
        client = KeycloakClient(
            url=parameters["keycloak_url"],
            realm=parameters.get("keycloak_realm", "master"),
            client_id=parameters.get("keycloak_client_id", "admin-cli"),
            username=parameters.get("keycloak_admin"),
            password=parameters.get("keycloak_password"),
        )
    if not client:
        return {"rolled_back": False, "reason": "no_keycloak_credentials"}
    result = await client.enable_user(username)
    return {"rolled_back": result.get("success", False), **result}
