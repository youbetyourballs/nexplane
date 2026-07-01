# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    ring_name = parameters["ring_name"]
    quality_deferral_days = parameters.get("quality_deferral_days", 7)
    feature_deferral_days = parameters.get("feature_deferral_days", 30)
    deadline_days = parameters.get("deadline_days", 14)

    if not creds:
        return {
            "action": "create_update_ring",
            "ring_id": "mock-ring-id-new",
            "ring_name": ring_name,
            "quality_deferral_days": quality_deferral_days,
            "feature_deferral_days": feature_deferral_days,
            "deadline_days": deadline_days,
            "status": "created",
        }

    from ._client import get_token, graph_post

    token = get_token(creds)
    payload = {
        "@odata.type": "#microsoft.graph.windowsUpdateForBusinessConfiguration",
        "displayName": ring_name,
        "qualityUpdatesDeferralPeriodInDays": quality_deferral_days,
        "featureUpdatesDeferralPeriodInDays": feature_deferral_days,
        "deadlineForQualityUpdatesInDays": deadline_days,
    }
    result = graph_post(token, "/deviceManagement/deviceConfigurations", json=payload)
    return {
        "action": "create_update_ring",
        "ring_id": result.get("id", ""),
        "ring_name": ring_name,
        "quality_deferral_days": quality_deferral_days,
        "feature_deferral_days": feature_deferral_days,
        "deadline_days": deadline_days,
        "status": "created",
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    ring_id = execution_result.get("ring_id")
    if not ring_id:
        return {"action": "delete_update_ring", "status": "no_ring_id_to_rollback"}
    from app.connectors.executors.wufb.delete_update_ring import execute as delete
    return await delete({"ring_id": ring_id}, [], connector)
