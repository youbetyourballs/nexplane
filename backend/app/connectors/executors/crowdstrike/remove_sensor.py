# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


def _get_hosts_api(creds: dict):
    from ._client import get_hosts_api
    return get_hosts_api(creds)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    if not creds.get("client_id"):
        return {"status": "error", "error": "CrowdStrike connector missing client_id credential"}

    hostname = parameters.get("hostname", "")
    loop = asyncio.get_running_loop()

    def _remove():
        hosts = _get_hosts_api(creds)

        # Find device_ids by hostname filter
        fql = f"hostname:'{hostname}'" if hostname else ""
        resp = hosts.QueryDevicesByFilterScroll(filter=fql, limit=100)
        if resp["status_code"] != 200:
            raise RuntimeError(f"CrowdStrike query failed: {resp['body']}")

        device_ids = resp["body"].get("resources", [])
        if not device_ids:
            return {
                "status": "skipped",
                "reason": f"No CrowdStrike devices found for hostname={hostname!r}",
                "skipped": True,
            }

        # Hide/delete hosts (triggers sensor removal workflow)
        hide_resp = hosts.hide_hosts(ids=device_ids)
        if hide_resp["status_code"] not in (200, 202):
            raise RuntimeError(f"hide_hosts failed: {hide_resp['body']}")

        return {
            "status": "removed",
            "device_ids": device_ids,
            "hostname": hostname,
            "removed_at": datetime.now(timezone.utc).isoformat(),
        }

    try:
        return await loop.run_in_executor(None, _remove)
    except Exception as e:
        return {"status": "error", "error": str(e), "action": "remove_sensor"}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": "Sensor removal triggers uninstall on the endpoint. Re-deploy via deploy_sensor to restore.",
        "data_loss_warning": "CrowdStrike agent was removed from the host. Endpoint is now unprotected until re-deployed.",
    }
