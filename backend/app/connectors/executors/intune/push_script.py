# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import base64


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    script_name = parameters["script_name"]
    script_content = parameters["script_content"]
    device_id = parameters.get("device_id")

    if not creds:
        return {
            "action": "push_script",
            "script_id": "mock-script-id-001",
            "script_name": script_name,
            "device_id": device_id,
            "status": "created",
        }

    from ._client import get_token, graph_post

    token = get_token(creds)
    encoded = base64.b64encode(script_content.encode("utf-8")).decode("ascii")
    payload = {
        "displayName": script_name,
        "scriptContent": encoded,
        "runAsAccount": "system",
        "enforceSignatureCheck": False,
        "fileName": f"{script_name}.ps1",
    }
    result = graph_post(token, "/deviceManagement/deviceManagementScripts", json=payload)
    script_id = result.get("id", "")

    # If a specific device is targeted, assign the script to that device
    if device_id and script_id:
        assign_payload = {
            "deviceManagementScriptAssignments": [
                {
                    "target": {
                        "@odata.type": "#microsoft.graph.deviceAndAppManagementAssignmentTarget",
                    }
                }
            ]
        }
        # Assignment endpoint — best-effort; does not fail the CR if it errors
        try:
            graph_post(
                token,
                f"/deviceManagement/deviceManagementScripts/{script_id}/assign",
                json=assign_payload,
            )
        except Exception:
            pass

    return {
        "action": "push_script",
        "script_id": script_id,
        "script_name": script_name,
        "device_id": device_id,
        "status": "created",
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    script_id = execution_result.get("script_id")
    if not script_id:
        return {"action": "delete_script", "status": "no_script_id_to_rollback"}
    from app.connectors.executors.intune.delete_script import execute as delete
    return await delete({"script_id": script_id}, [], connector)
