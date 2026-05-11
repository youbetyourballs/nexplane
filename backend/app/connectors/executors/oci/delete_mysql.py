import asyncio
import time
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    db_system_id = parameters.get("db_system_id", "")

    if not creds:
        return {"action": "delete_mysql", "db_system_id": db_system_id, "status": "DELETED", "mock": True}

    from ._client import get_mysql_client
    mysql_client = get_mysql_client(creds)
    loop = asyncio.get_running_loop()

    def _delete_and_poll():
        mysql_client.delete_db_system(db_system_id=db_system_id)
        for _ in range(20):
            try:
                info = mysql_client.get_db_system(db_system_id=db_system_id).data
                if info.lifecycle_state == "DELETED":
                    return "DELETED"
            except Exception:
                return "DELETED"
            time.sleep(30)
        raise TimeoutError("MySQL DB System did not reach DELETED within 10 minutes")

    state = await loop.run_in_executor(None, _delete_and_poll)
    return {
        "action": "delete_mysql",
        "db_system_id": db_system_id,
        "status": state,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }
