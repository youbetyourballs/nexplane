# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import json

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    host_id = parameters["host_id"]
    variables = parameters["variables"]
    if not creds:
        return {"action": "update_host_variables", "host_id": host_id, "updated": True}
    from ._client import get_client
    async with get_client(creds) as client:
        # Capture current variables before overwriting
        get_resp = await client.get(f"/hosts/{host_id}/")
        get_resp.raise_for_status()
        host_data = get_resp.json()
        current_vars_raw = host_data.get("variables") or "{}"
        try:
            current_vars = json.loads(current_vars_raw)
        except Exception:
            current_vars = {}

        from app.services.pre_state_store import PreStateStore
        from app.database import AsyncSessionLocal
        import uuid as _uuid
        async with AsyncSessionLocal() as db:
            await PreStateStore.capture(
                db,
                _uuid.UUID(str(parameters["cr_id"])),
                str(parameters.get("step_id", "step_0")),
                _uuid.UUID(str(parameters["org_id"])),
                {"previous_variables": current_vars, "host_id": host_id},
            )
            await db.commit()

        resp = await client.patch(f"/hosts/{host_id}/", json={"variables": json.dumps(variables)})
        resp.raise_for_status()
    return {"action": "update_host_variables", "host_id": host_id, "updated": True}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal
    import uuid as _uuid

    async with AsyncSessionLocal() as db:
        pre_state = await PreStateStore.retrieve(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
        )
    if not pre_state:
        return {"rolled_back": False, "reason": "previous host variables not captured — restore manually"}

    previous_variables = pre_state.get("previous_variables", {})
    host_id = pre_state.get("host_id") or parameters.get("host_id")

    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"rolled_back": False, "reason": "no connector credentials"}

    from ._client import get_client
    async with get_client(creds) as client:
        resp = await client.patch(f"/hosts/{host_id}/", json={"variables": json.dumps(previous_variables)})
        resp.raise_for_status()
    return {"rolled_back": True, "host_id": host_id, "restored_variables": previous_variables}
