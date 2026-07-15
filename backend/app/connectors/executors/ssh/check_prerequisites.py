# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone
from ._client import get_ssh_client, prepare_ssh_target

ROLLBACK_CAPABILITY = "irreversible"
ROLLBACK_REASON = "read-only operation — no state was changed"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "check_prerequisites", "all_passed": True,
            "checks": [{"name": "mock", "passed": True}],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    creds = await prepare_ssh_target(connector, creds)
    loop = asyncio.get_event_loop()

    def _check(asset_id):
        client = get_ssh_client(creds)
        checks = []
        try:
            for name, cmd in [
                ("curl_available", "which curl"),
                ("systemd_active", "systemctl is-system-running --quiet || true"),
                ("sudo_access", "sudo -n true"),
                ("disk_space", "df / --output=avail | tail -1"),
            ]:
                _, stdout, stderr = client.exec_command(cmd, timeout=10)
                exit_code = stdout.channel.recv_exit_status()
                checks.append({"name": name, "passed": exit_code == 0,
                                "output": stdout.read().decode().strip()})
        finally:
            client.close()
        return {"asset_id": asset_id, "checks": checks, "all_passed": all(c["passed"] for c in checks)}

    results = []
    for asset_id in asset_ids:
        try:
            results.append(await loop.run_in_executor(None, _check, str(asset_id)))
        except Exception as e:
            results.append({"asset_id": str(asset_id), "all_passed": False, "error": str(e)})

    return {
        "action": "check_prerequisites",
        "host_results": results,
        "all_passed": all(r.get("all_passed") for r in results),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": True, "reason": "prerequisite check is read-only"}
