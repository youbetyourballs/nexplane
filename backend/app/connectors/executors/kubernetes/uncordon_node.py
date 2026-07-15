# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from ._client import get_k8s_clients


async def execute(parameters, asset_ids, connector):
    creds = getattr(connector, "credentials", {})
    node_name = parameters["node_name"]

    if not creds:
        return {
            "uncordoned": True,
            "node_name": node_name,
            "mock": True,
            "pre_state": {"was_unschedulable": False},
        }

    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    api = clients["core"]

    # 1. Capture pre-state
    def _get_node():
        node = api.read_node(node_name)
        return node.spec.unschedulable or False

    was_unschedulable = await loop.run_in_executor(None, _get_node)
    pre_state = {"was_unschedulable": was_unschedulable}

    # 2. Uncordon (set unschedulable=False)
    def _uncordon():
        api.patch_node(node_name, {"spec": {"unschedulable": False}})

    await loop.run_in_executor(None, _uncordon)
    return {
        "uncordoned": True,
        "node_name": node_name,
        "pre_state": pre_state,
    }


async def rollback(parameters, execution_result, connector):
    creds = getattr(connector, "credentials", {})
    pre_state = execution_result.get("pre_state", {})

    if not pre_state:
        return {"rolled_back": False, "reason": "no pre_state captured"}

    if not creds:
        return {"rolled_back": True, "mock": True}

    node_name = execution_result.get("node_name") or parameters.get("node_name")
    was_unschedulable = pre_state.get("was_unschedulable", False)

    loop = asyncio.get_event_loop()
    clients = get_k8s_clients(creds)
    api = clients["core"]

    try:
        if was_unschedulable:
            # Node was cordoned before uncordon — re-cordon it
            def _recordon():
                api.patch_node(node_name, {"spec": {"unschedulable": True}})
            await loop.run_in_executor(None, _recordon)
            return {"rolled_back": True, "node_name": node_name, "action": "re-cordoned"}
        else:
            # Node was already uncordoned before — no-op rollback is correct
            return {"rolled_back": True, "node_name": node_name, "action": "no-op: was already uncordoned"}
    except Exception as e:
        return {"rolled_back": False, "error": str(e)}
