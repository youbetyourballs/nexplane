from datetime import datetime, timezone
from ._runner import run_playbook


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    instance_id = parameters.get('instance_id', '')
    playbook_content = parameters.get('playbook_content', '')
    extra_vars = parameters.get('extra_vars', {})

    if not playbook_content:
        return {"action": "ansible_run_local", "stdout": "mock: no playbook content", "mock": True}

    inventory_content = parameters.get('inventory_content', None)
    result = await run_playbook(
        instance_id, playbook_content, connector,
        check_mode=False, extra_vars=extra_vars, inventory_content=inventory_content,
    )
    return {
        "action": "ansible_run_local",
        "instance_id": instance_id,
        "stdout": result["stdout"],
        "rc": result["rc"],
        "mock": result.get("mock", False),
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "ansible_run_local has no automatic rollback — write an undo playbook"}
