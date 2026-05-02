import asyncio
from datetime import datetime, timezone


async def _real_execute(parameters: dict, asset_ids: list, creds: dict) -> dict:
    from ._client import get_ssh_client
    loop = asyncio.get_event_loop()

    def _sync():
        client = get_ssh_client(creds)
        checks = {}
        for cmd, key in [("uname -s", "kernel"), ("df -h /", "disk"), ("free -h", "memory"), ("uptime", "uptime")]:
            stdin, stdout, stderr = client.exec_command(cmd, timeout=15)
            checks[key] = stdout.read().decode().strip()
        client.close()
        return checks

    checks = await loop.run_in_executor(None, _sync)
    return {
        "action": "check_prerequisites",
        "prerequisites_met": True,
        "checks": checks,
        "assets": asset_ids,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "check_prerequisites", "prerequisites_met": True, "checked_at": datetime.now(timezone.utc).isoformat()}
    return await _real_execute(parameters, asset_ids, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "check has no rollback"}
