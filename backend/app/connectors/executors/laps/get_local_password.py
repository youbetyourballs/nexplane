# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    device_id = parameters["device_id"]
    if not creds:
        return {
            "action": "get_local_password",
            "device_id": device_id,
            "localAdminPassword": "Mock-P@ssw0rd-123",
            "status": "retrieved",
        }

    from ._client import get_token, graph_get

    token = get_token(creds)
    data = graph_get(token, f"/deviceLocalCredentials/{device_id}?$select=credentials")
    credentials = data.get("credentials", [{}])
    password = credentials[0].get("passwordBase64", "") if credentials else ""

    import base64
    try:
        decoded_password = base64.b64decode(password).decode("utf-8") if password else ""
    except Exception:
        decoded_password = password

    return {
        "action": "get_local_password",
        "device_id": device_id,
        "localAdminPassword": decoded_password,
        "status": "retrieved",
    }
