# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "action": "discover_update_policies",
            "policies": [
                {
                    "id": "mock-ring-id-001",
                    "displayName": "Mock Update Ring — Pilot",
                    "qualityUpdatesDeferralPeriodInDays": 7,
                    "featureUpdatesDeferralPeriodInDays": 30,
                    "deadlineForQualityUpdatesInDays": 14,
                }
            ],
            "total": 1,
        }

    from ._client import get_token, graph_get

    token = get_token(creds)
    data = graph_get(
        token,
        "/deviceManagement/deviceConfigurations"
        "?$filter=isof('microsoft.graph.windowsUpdateForBusinessConfiguration')",
    )
    policies = data.get("value", [])
    return {
        "action": "discover_update_policies",
        "policies": policies,
        "total": len(policies),
    }
