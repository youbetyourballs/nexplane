import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Delete an OCI Log Group (and contained logs) — rollback for oci_logging_enable."""
    creds = getattr(connector, "credentials", {})
    log_group_id = parameters.get("log_group_id", "")
    log_id = parameters.get("log_id", "")

    if not creds:
        return {"action": "disable_logging", "log_group_id": log_group_id, "mock": True}

    if not log_group_id:
        return {"action": "disable_logging", "skipped": True, "reason": "no log_group_id"}

    import oci
    config = {
        "user": creds["user"],
        "key_content": creds["private_key"],
        "fingerprint": creds["fingerprint"],
        "tenancy": creds["tenancy"],
        "region": creds.get("region", "us-ashburn-1"),
    }
    logging_client = oci.logging.LoggingManagementClient(config)
    loop = asyncio.get_running_loop()

    # Delete log first if we have its ID
    if log_id:
        try:
            await loop.run_in_executor(
                None, lambda: logging_client.delete_log(log_group_id, log_id)
            )
        except Exception:
            pass

    await loop.run_in_executor(
        None, lambda: logging_client.delete_log_group(log_group_id)
    )
    return {
        "action": "disable_logging",
        "log_group_id": log_group_id,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "disable_logging has no rollback"}
