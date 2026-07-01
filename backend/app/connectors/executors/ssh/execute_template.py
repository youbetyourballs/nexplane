# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import random
import time
from datetime import datetime, timezone
from app.services.safety_engine import APPROVED_COMMAND_TEMPLATES


async def _real_execute(parameters: dict, asset_ids: list, creds: dict) -> dict:
    from ._client import get_ssh_client, is_allowed
    template_id = parameters.get("template_id")
    template = APPROVED_COMMAND_TEMPLATES.get(template_id, {})
    command = template.get("command", "")
    if not command or not is_allowed(command):
        raise ValueError(f"Command template '{template_id}' is not allowed for SSH execution")

    loop = asyncio.get_event_loop()

    def _sync():
        client = get_ssh_client(creds)
        start = time.monotonic()
        stdin, stdout, stderr = client.exec_command(command, timeout=60)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode()
        err = stderr.read().decode()
        duration_ms = int((time.monotonic() - start) * 1000)
        client.close()
        return exit_code, out, err, duration_ms

    exit_code, out, err, duration_ms = await loop.run_in_executor(None, _sync)
    host_results = [{"asset_id": a, "exit_code": exit_code, "stdout": out, "stderr": err, "duration_ms": duration_ms} for a in asset_ids]
    return {"action": "execute_template", "template_id": template_id, "parameters": parameters.get("parameters", {}), "host_results": host_results, "completed_at": datetime.now(timezone.utc).isoformat()}


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    template_id = parameters.get("template_id")
    if not template_id or template_id not in APPROVED_COMMAND_TEMPLATES:
        raise ValueError(f"Command template '{template_id}' is not approved")
    if parameters.get("freeform_command"):
        raise ValueError("Freeform commands are not permitted")
    creds = getattr(connector, "credentials", {})
    if not creds:
        host_results = [{"asset_id": a, "exit_code": 0, "stdout": f"[mock] Executed '{template_id}'", "stderr": "", "duration_ms": random.randint(50, 500)} for a in asset_ids]
        return {"action": "execute_template", "template_id": template_id, "parameters": parameters.get("parameters", {}), "host_results": host_results, "completed_at": datetime.now(timezone.utc).isoformat()}
    return await _real_execute(parameters, asset_ids, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "command execution rollback is manual"}
