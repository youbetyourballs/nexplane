# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    process_name = parameters.get("process_name", "")
    if not process_name:
        return {"status": "error", "message": "process_name is required"}

    if not creds:
        return {
            "action": "restore_bare_metal_service",
            "process_name": process_name,
            "hosts": [{"asset_id": a, "restored": True, "service_status": "active"} for a in asset_ids],
            "restored_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }

    from ._client import get_ssh_client, prepare_ssh_target
    creds = await prepare_ssh_target(connector, creds)
    loop = asyncio.get_event_loop()
    host_results = []

    def _restore(asset_id: str):
        client = get_ssh_client(creds)
        try:
            _, stdout, stderr = client.exec_command(
                f"sudo systemctl restart {process_name} && systemctl is-active {process_name}",
                timeout=60,
            )
            exit_code = stdout.channel.recv_exit_status()
            status = stdout.read().decode().strip()
            if exit_code != 0:
                return {"asset_id": asset_id, "restored": False, "error": stderr.read().decode()}
            return {"asset_id": asset_id, "restored": True, "service_status": status}
        finally:
            client.close()

    for asset_id in asset_ids:
        try:
            result = await loop.run_in_executor(None, _restore, str(asset_id))
        except Exception as exc:
            result = {"asset_id": str(asset_id), "restored": False, "error": str(exc)}
        host_results.append(result)

    return {
        "action": "restore_bare_metal_service",
        "process_name": process_name,
        "hosts": host_results,
        "restored_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "restore_bare_metal_service has no rollback"}
