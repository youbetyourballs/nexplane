import asyncio
import time


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    resource_group = parameters["resource_group"]
    vm_name = parameters["vm_name"]
    target_state = parameters.get("target_state", "running")
    timeout = parameters.get("timeout_seconds", 300)

    if not creds:
        return {
            "action": "wait_vm_state",
            "resource_group": resource_group,
            "vm_name": vm_name,
            "target_state": target_state,
            "reached": True,
        }

    from ._client import get_compute_client
    client = get_compute_client(creds)
    loop = asyncio.get_running_loop()
    target_code = f"PowerState/{target_state}"
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        vm = await loop.run_in_executor(
            None, lambda: client.virtual_machines.get(resource_group, vm_name, expand="instanceView")
        )
        statuses = {s.code for s in (vm.instance_view.statuses or []) if s.code}
        if target_code in statuses:
            return {
                "action": "wait_vm_state",
                "resource_group": resource_group,
                "vm_name": vm_name,
                "target_state": target_state,
                "reached": True,
            }
        await asyncio.sleep(10)

    raise RuntimeError(f"Timed out waiting for {vm_name} to reach {target_code}")


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "wait_vm_state is read-only"}
