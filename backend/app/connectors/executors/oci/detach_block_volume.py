# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")
    volume_id = parameters.get("volume_id", "")

    if not creds:
        return {
            "action": "detach_block_volume",
            "instance_id": instance_id,
            "volume_id": volume_id,
            "mock": True,
        }

    from ._client import get_oci_config
    import oci
    config = get_oci_config(creds)
    compute_client = oci.core.ComputeClient(config)
    compartment_id = creds.get("compartment_id", "")
    loop = asyncio.get_running_loop()

    def _find_and_detach():
        attachments = compute_client.list_volume_attachments(
            compartment_id=compartment_id, instance_id=instance_id
        ).data
        target = next((a for a in attachments if a.volume_id == volume_id and a.lifecycle_state == "ATTACHED"), None)
        if not target:
            raise ValueError(f"No ATTACHED volume attachment found for volume '{volume_id}' on instance '{instance_id}'")
        compute_client.detach_volume(volume_attachment_id=target.id)
        return target.id

    attachment_id = await loop.run_in_executor(None, _find_and_detach)

    # Poll until DETACHED
    for _ in range(60):
        await asyncio.sleep(5)
        attachment = await loop.run_in_executor(
            None, lambda: compute_client.get_volume_attachment(volume_attachment_id=attachment_id).data
        )
        if attachment.lifecycle_state == "DETACHED":
            break
        if attachment.lifecycle_state == "FAULTY":
            raise RuntimeError(f"Detachment reached FAULTY state")

    return {
        "action": "detach_block_volume",
        "instance_id": instance_id,
        "volume_id": volume_id,
        "volume_attachment_id": attachment_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.attach_block_volume import execute as attach
    return await attach(
        {
            "instance_id": execution_result.get("instance_id", parameters.get("instance_id", "")),
            "volume_id": execution_result.get("volume_id", parameters.get("volume_id", "")),
        },
        [],
        connector,
    )
