import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    volume_id = parameters.get("volume_id", "")

    if not creds:
        return {"action": "delete_block_volume", "volume_id": volume_id, "mock": True}

    from ._client import get_blockstorage_client, get_oci_config
    import oci
    client = get_blockstorage_client(creds)
    compartment_id = creds.get("compartment_id", "")
    loop = asyncio.get_running_loop()

    def _call():
        # Preflight: check not attached
        config = get_oci_config(creds)
        compute_client = oci.core.ComputeClient(config)
        attachments = compute_client.list_volume_attachments(
            compartment_id=compartment_id, volume_id=volume_id
        ).data
        active = [a for a in attachments if a.lifecycle_state == "ATTACHED"]
        if active:
            raise ValueError(
                f"Volume '{volume_id}' is currently attached to instance '{active[0].instance_id}'. "
                "Detach the volume before deleting."
            )
        client.delete_volume(volume_id=volume_id)

    await loop.run_in_executor(None, _call)
    return {
        "action": "delete_block_volume",
        "volume_id": volume_id,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "delete_block_volume is destructive — no rollback"}
