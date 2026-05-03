import asyncio
import time
from datetime import datetime, timezone


def _get_rds_client(creds: dict):
    from ._client import get_boto3_client
    return get_boto3_client(creds, "rds")


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "action":      "restore_rds_snapshot",
            "snapshot_id": parameters.get("snapshot_id"),
            "temp_instance_id": f"nexplane-restore-mock-{int(time.time())}",
            "mock":        True,
        }
    return await _real_execute(creds, parameters)


async def _real_execute(creds: dict, parameters: dict) -> dict:
    rds = _get_rds_client(creds)
    loop = asyncio.get_event_loop()
    snapshot_id = parameters["snapshot_id"]
    temp_id = parameters.get("target_instance_id", f"nexplane-restore-{int(time.time())}")

    def _call():
        return rds.restore_db_instance_from_db_snapshot(
            DBInstanceIdentifier=temp_id,
            DBSnapshotIdentifier=snapshot_id,
            DBInstanceClass=parameters.get("db_instance_class", "db.t3.micro"),
            MultiAZ=False,
            PubliclyAccessible=False,
            Tags=[{"Key": "ManagedBy", "Value": "nexplane-restore"}],
        )

    await loop.run_in_executor(None, _call)
    return {
        "action":           "restore_rds_snapshot",
        "snapshot_id":      snapshot_id,
        "temp_instance_id": temp_id,
        "executed_at":      datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete the temporary restored instance."""
    creds = getattr(connector, "credentials", {})
    temp_id = execution_result.get("temp_instance_id")
    if not temp_id or not creds:
        return {"rolled_back": False, "reason": "no temp_instance_id or no credentials"}
    rds = _get_rds_client(creds)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: rds.delete_db_instance(
        DBInstanceIdentifier=temp_id,
        SkipFinalSnapshot=True,
    ))
    return {"rolled_back": True, "deleted_instance": temp_id}
