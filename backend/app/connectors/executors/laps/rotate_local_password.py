# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    device_id = parameters["device_id"]
    if not creds:
        return {
            "action": "rotate_local_password",
            "device_id": device_id,
            "status": "rotation_triggered",
        }

    from ._client import get_token, graph_post

    token = get_token(creds)
    graph_post(token, f"/deviceLocalCredentials/{device_id}/rotateLocalAdminPassword")
    return {
        "action": "rotate_local_password",
        "device_id": device_id,
        "status": "rotation_triggered",
    }
