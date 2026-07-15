# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    agent_type = parameters.get("agent_type", "nexplane")

    if not creds:
        return {
            "action": "uninstall_agent",
            "agent_type": agent_type,
            "hosts": [{"asset_id": a, "uninstalled": True} for a in asset_ids],
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }

    from ._client import get_ssh_client, prepare_ssh_target
    creds = await prepare_ssh_target(connector, creds)
    loop = asyncio.get_event_loop()
    host_results = []

    def _uninstall(asset_id: str):
        client = get_ssh_client(creds)
        try:
            commands = [
                "sudo systemctl stop nexplane-agent || true",
                "sudo systemctl disable nexplane-agent || true",
                "sudo rm -f /tmp/nexplane-agent /usr/local/bin/nexplane-agent",
                "sudo rm -f /etc/systemd/system/nexplane-agent.service",
                "sudo systemctl daemon-reload || true",
            ]
            for cmd in commands:
                _, stdout, _ = client.exec_command(cmd, timeout=30)
                stdout.channel.recv_exit_status()
            return {"asset_id": asset_id, "uninstalled": True}
        finally:
            client.close()

    for asset_id in asset_ids:
        try:
            result = await loop.run_in_executor(None, _uninstall, str(asset_id))
        except Exception as exc:
            result = {"asset_id": str(asset_id), "uninstalled": False, "error": str(exc)}
        host_results.append(result)

    return {
        "action": "uninstall_agent",
        "agent_type": agent_type,
        "hosts": host_results,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "uninstall has no rollback — reinstall via install_agent"}
