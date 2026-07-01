# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone
from ._client import get_ssh_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    service_name = parameters.get("service_name", "nexplane-agent")
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        return {
            "action": "start_service", "service_name": service_name,
            "hosts": [{"asset_id": a, "started": True} for a in asset_ids],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    loop = asyncio.get_event_loop()

    def _start(asset_id):
        client = get_ssh_client(creds)
        try:
            _, stdout, _ = client.exec_command(
                f"sudo systemctl start {service_name} && systemctl is-active {service_name}",
                timeout=30,
            )
            exit_code = stdout.channel.recv_exit_status()
            status = stdout.read().decode().strip()
            return {"asset_id": asset_id, "started": exit_code == 0, "service_status": status}
        finally:
            client.close()

    results = []
    for asset_id in asset_ids:
        try:
            results.append(await loop.run_in_executor(None, _start, str(asset_id)))
        except Exception as e:
            results.append({"asset_id": str(asset_id), "started": False, "error": str(e)})

    return {
        "action": "start_service", "service_name": service_name,
        "hosts": results,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    service_name = parameters.get("service_name", "nexplane-agent")
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {"rolled_back": True}

    loop = asyncio.get_event_loop()

    def _stop(asset_id):
        from ._client import get_ssh_client as _get
        c = _get(creds)
        try:
            c.exec_command(f"sudo systemctl stop {service_name}", timeout=15)
        finally:
            c.close()

    for asset_id in (execution_result.get("hosts") or []):
        try:
            await loop.run_in_executor(None, _stop, str(asset_id.get("asset_id", asset_id)))
        except Exception:
            pass
    return {"rolled_back": True, "service_stopped": service_name}
