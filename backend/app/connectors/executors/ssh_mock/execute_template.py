import random
from datetime import datetime, timezone
from app.services.safety_engine import APPROVED_COMMAND_TEMPLATES

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    template_id = parameters.get("template_id")
    if not template_id or template_id not in APPROVED_COMMAND_TEMPLATES:
        raise ValueError(f"Command template '{template_id}' is not approved")
    if parameters.get("freeform_command"):
        raise ValueError("Freeform commands are not permitted")
    host_results = [{"asset_id": a, "exit_code": 0, "stdout": f"[mock] Executed '{template_id}'", "stderr": "", "duration_ms": random.randint(50, 500)} for a in asset_ids]
    return {"action": "execute_template", "template_id": template_id, "parameters": parameters.get("parameters", {}), "host_results": host_results, "completed_at": datetime.now(timezone.utc).isoformat()}

async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "command execution rollback is manual"}
