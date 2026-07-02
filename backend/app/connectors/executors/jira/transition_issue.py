# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    issue_key = parameters["issue_key"]
    transition_name = parameters["transition_name"]
    if not creds:
        return {"action": "transition_issue", "issue_key": issue_key, "new_status": transition_name}
    from ._client import get_client
    async with await get_client(connector) as client:
        resp = await client.get(f"/issue/{issue_key}/transitions")
        resp.raise_for_status()
        transitions = {t["name"]: t["id"] for t in resp.json().get("transitions", [])}
        transition_id = transitions.get(transition_name)
        if not transition_id:
            return {"action": "transition_issue", "error": f"Transition '{transition_name}' not found. Available: {list(transitions.keys())}"}
        resp2 = await client.post(f"/issue/{issue_key}/transitions", json={"transition": {"id": transition_id}})
        resp2.raise_for_status()
    return {"action": "transition_issue", "issue_key": issue_key, "new_status": transition_name}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "previous transition state not captured"}
