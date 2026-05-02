import asyncio
from datetime import datetime, timezone


async def _real_execute(parameters: dict, asset_ids: list, creds: dict) -> dict:
    from ._client import get_ssh_client
    agent_type = parameters.get("agent_type", "")
    service_name = parameters.get("service_name", agent_type)
    loop = asyncio.get_event_loop()

    def _sync():
        client = get_ssh_client(creds)
        cmd = f"systemctl status {service_name} --no-pager"
        stdin, stdout, stderr = client.exec_command(cmd, timeout=30)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode()
        client.close()
        # exit 0 = active, exit 3 = inactive/not-found
        return "active" if exit_code == 0 else "inactive"

    status = await loop.run_in_executor(None, _sync)
    return {
        "action": "start_service",
        "agent_type": agent_type,
        "service_status": status,
        "hosts": [{"asset_id": a, "service_status": status} for a in asset_ids],
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {"action": "start_service", "agent_type": parameters.get("agent_type"), "service_status": "active", "hosts": [{"asset_id": a, "service_status": "active"} for a in asset_ids], "completed_at": datetime.now(timezone.utc).isoformat()}
    return await _real_execute(parameters, asset_ids, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "service start has no automatic rollback"}
