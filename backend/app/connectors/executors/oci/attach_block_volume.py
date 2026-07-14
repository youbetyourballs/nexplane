# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    instance_id = parameters.get("instance_id", "")
    volume_id = parameters.get("volume_id", "")
    display_name = parameters.get("display_name", "nexplane-attachment")
    attach_type = parameters.get("type", "paravirtualized")
    is_read_only = parameters.get("is_read_only", False)

    if not creds:
        return {
            "action": "attach_block_volume",
            "instance_id": instance_id,
            "volume_id": volume_id,
            "volume_attachment_id": "ocid1.volumeattachment.oc1..mock",
            "mock": True,
        }

    from ._client import get_oci_config
    import oci
    config = get_oci_config(creds)
    compute_client = oci.core.ComputeClient(config)
    loop = asyncio.get_running_loop()

    def _attach():
        details = oci.core.models.AttachParavirtualizedVolumeDetails(
            instance_id=instance_id,
            volume_id=volume_id,
            display_name=display_name,
            is_read_only=is_read_only,
        )
        attachment = compute_client.attach_volume(attach_volume_details=details).data
        return attachment.id

    attachment_id = await loop.run_in_executor(None, _attach)

    # Poll until ATTACHED
    for _ in range(60):
        await asyncio.sleep(5)
        attachment = await loop.run_in_executor(
            None, lambda: compute_client.get_volume_attachment(volume_attachment_id=attachment_id).data
        )
        if attachment.lifecycle_state == "ATTACHED":
            break
        if attachment.lifecycle_state in ("DETACHED", "FAULTY"):
            raise RuntimeError(f"Attachment reached terminal state: {attachment.lifecycle_state}")

    return {
        "action": "attach_block_volume",
        "instance_id": instance_id,
        "volume_id": volume_id,
        "volume_attachment_id": attachment_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.connectors.executors.oci.detach_block_volume import execute as detach
    return await detach(
        {
            "instance_id": execution_result.get("instance_id", parameters.get("instance_id", "")),
            "volume_id": execution_result.get("volume_id", parameters.get("volume_id", "")),
        },
        [],
        connector,
    )
