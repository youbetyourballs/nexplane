# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "discover_compute_instances", "instances": [
            {"name": "mock-vm-1", "zone": "us-central1-a", "machine_type": "e2-medium", "status": "RUNNING",
             "internal_ip": "10.0.0.1", "external_ip": "34.1.2.3", "labels": {}}
        ], "count": 1}
    from ._client import get_credentials, get_project_id
    from google.cloud import compute_v1
    credentials = get_credentials(creds)
    project = get_project_id(creds)
    loop = asyncio.get_event_loop()
    client = compute_v1.InstancesClient(credentials=credentials)
    instances = []
    agg = await loop.run_in_executor(None, lambda: client.aggregated_list(project=project))
    for zone_name, zone_data in agg:
        for inst in zone_data.instances:
            ni = inst.network_interfaces[0] if inst.network_interfaces else None
            instances.append({
                "name": inst.name, "zone": zone_name,
                "machine_type": inst.machine_type.split("/")[-1],
                "status": inst.status,
                "internal_ip": ni.network_i_p if ni else None,
                "external_ip": ni.access_configs[0].nat_i_p if ni and ni.access_configs else None,
                "labels": dict(inst.labels),
                "service_account": inst.service_accounts[0].email if inst.service_accounts else None,
            })
    return {
        "action": "discover_compute_instances",
        "instances": instances,
        "count": len(instances),
        "_auto_asset": {
            "name": f"GCP · {project} (Google Cloud Platform)",
            "asset_type": "cloud_account",
            "asset_metadata": {"project_id": project, "provider": "gcp"},
            "tags": ["gcp", "cloud-account", "live"],
        },
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "discover has no rollback"}
