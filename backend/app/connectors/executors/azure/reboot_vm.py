import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]

    if not creds:
        return {"action": "reboot_vm", "resource_group": resource_group, "vm_name": vm_name}

    from ._client import get_compute_client
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, lambda: client.virtual_machines.begin_restart(resource_group, vm_name).result()
    )
    return {
        "action": "reboot_vm",
        "resource_group": resource_group,
        "vm_name": vm_name,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "reboot_vm has no meaningful inverse"}
