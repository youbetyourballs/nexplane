import asyncio
import time
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    db_id = parameters.get("db_id", "")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    display_name = parameters.get("display_name", f"nexplane-backup-{ts}")

    if not creds:
        return {
            "action": "backup_adb",
            "db_id": db_id,
            "backup_id": "mock-backup-id",
            "status": "ACTIVE",
            "mock": True,
        }

    import oci
    from ._client import get_database_client
    db_client = get_database_client(creds)
    loop = asyncio.get_running_loop()

    def _backup_and_poll():
        details = oci.database.models.CreateAutonomousDatabaseBackupDetails(
            autonomous_database_id=db_id,
            display_name=display_name,
        )
        backup = db_client.create_autonomous_database_backup(
            create_autonomous_database_backup_details=details
        ).data
        backup_id = backup.id
        for _ in range(30):
            info = db_client.get_autonomous_database_backup(
                autonomous_database_backup_id=backup_id
            ).data
            if info.lifecycle_state == "ACTIVE":
                return backup_id, "ACTIVE"
            if info.lifecycle_state == "FAILED":
                raise RuntimeError("Backup failed")
            time.sleep(60)
        raise TimeoutError("Backup did not become ACTIVE within 30 minutes")

    backup_id, state = await loop.run_in_executor(None, _backup_and_poll)
    return {
        "action": "backup_adb",
        "db_id": db_id,
        "backup_id": backup_id,
        "display_name": display_name,
        "status": state,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    """Delete the backup that was created."""
    creds = getattr(connector, "credentials", {})
    backup_id = execution_result.get("backup_id", "")
    if not creds or not backup_id:
        return {"action": "rollback_backup_adb", "mock": True}

    from ._client import get_database_client
    import asyncio
    db_client = get_database_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: db_client.delete_autonomous_database_backup(
            autonomous_database_backup_id=backup_id
        ),
    )
    return {"action": "rollback_backup_adb", "backup_id": backup_id, "status": "DELETED"}
