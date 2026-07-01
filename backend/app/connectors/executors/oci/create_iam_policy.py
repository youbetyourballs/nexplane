# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone
from ._client import get_identity_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    compartment_id = (
        parameters.get("compartment_id")
        or creds.get("compartment_id")
        or creds.get("tenancy_id")
        or creds.get("tenancy", "")
    )
    name = parameters.get("name", "nexplane-policy")
    description = parameters.get("description", "Created by Nexplane")
    statements = parameters.get(
        "statements",
        ["Allow group nexplane-group to read all-resources in tenancy"],
    )

    auto_asset = {
        "name": name,
        "asset_type": "application",
        "environment": "prod",
        "criticality": "high",
        "asset_metadata": {"name": name, "compartment_id": compartment_id, "provider": "oci"},
        "tags": ["oci", "oci-iam-policy", "nexplane-managed"],
    }

    if not creds:
        return {
            "action": "create_iam_policy",
            "name": name,
            "policy_id": "ocid1.policy.oc1..mock",
            "mock": True,
            "_auto_asset": auto_asset,
        }

    client = get_identity_client(creds)
    loop = asyncio.get_running_loop()

    def _call():
        import oci
        details = oci.identity.models.CreatePolicyDetails(
            compartment_id=compartment_id,
            name=name,
            description=description,
            statements=statements,
        )
        return client.create_policy(details).data

    policy = await loop.run_in_executor(None, _call)
    auto_asset["asset_metadata"]["policy_id"] = policy.id
    auto_asset["asset_metadata"]["statements"] = list(policy.statements)
    return {
        "action": "create_iam_policy",
        "name": name,
        "policy_id": policy.id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "_auto_asset": auto_asset,
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.delete_iam_policy import execute as delete
    return await delete(
        {"policy_id": execution_result.get("policy_id"), "name": execution_result.get("name")},
        [],
        connector,
    )
