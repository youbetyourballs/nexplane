# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    command = parameters.get("command", "")
    if not command:
        return {"status": "error", "message": "command is required"}

    if not creds:
        return {
            "action": "collect_output",
            "command": command,
            "hosts": [{"asset_id": a, "stdout": f"[mock] {command}", "exit_code": 0} for a in asset_ids],
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }

    from ._client import get_ssh_client, is_allowed, prepare_ssh_target
    if not is_allowed(command):
        raise ValueError(f"Command not in SSH read allowlist: {command!r}")

    creds = await prepare_ssh_target(connector, creds)
    loop = asyncio.get_event_loop()
    host_results = []

    def _run(asset_id: str):
        client = get_ssh_client(creds)
        try:
            _, stdout, stderr = client.exec_command(command, timeout=60)
            exit_code = stdout.channel.recv_exit_status()
            return {
                "asset_id": asset_id,
                "stdout": stdout.read().decode(),
                "stderr": stderr.read().decode(),
                "exit_code": exit_code,
            }
        finally:
            client.close()

    for asset_id in asset_ids:
        try:
            result = await loop.run_in_executor(None, _run, str(asset_id))
        except Exception as exc:
            result = {"asset_id": str(asset_id), "exit_code": -1, "error": str(exc)}
        host_results.append(result)

    return {
        "action": "collect_output",
        "command": command,
        "hosts": host_results,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "output collection has no rollback"}
