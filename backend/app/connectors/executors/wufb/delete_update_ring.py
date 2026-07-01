# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    ring_id = parameters["ring_id"]
    if not creds:
        return {"action": "delete_update_ring", "ring_id": ring_id, "status": "deleted"}

    from ._client import get_token, graph_delete

    token = get_token(creds)
    graph_delete(token, f"/deviceManagement/deviceConfigurations/{ring_id}")
    return {"action": "delete_update_ring", "ring_id": ring_id, "status": "deleted"}
