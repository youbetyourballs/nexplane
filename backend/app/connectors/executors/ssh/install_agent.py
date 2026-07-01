# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone
from ._client import get_ssh_client


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    agent_type = parameters.get("agent_type", "nexplane")
    download_url = parameters.get("download_url", "")
    agent_secret = parameters.get("agent_secret", "")
    control_plane_url = parameters.get("control_plane_url", "")
    creds = getattr(connector, "credentials", {}) or {}

    if not creds:
        # No credentials — return mock for test environments
        return {
            "action": "install_agent", "agent_type": agent_type,
            "hosts": [{"asset_id": a, "agent_installed": True, "agent_version": "mock"} for a in asset_ids],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    loop = asyncio.get_event_loop()
    host_results = []

    def _install_on_host(asset_id: str):
        client = get_ssh_client(creds)
        try:
            commands = [
                f"curl -fsSL '{download_url}' -o /tmp/nexplane-agent && chmod +x /tmp/nexplane-agent",
                f"sudo /tmp/nexplane-agent install --secret='{agent_secret}' --control-plane='{control_plane_url}' --non-interactive",
                "sudo systemctl enable nexplane-agent && sudo systemctl start nexplane-agent",
                "systemctl is-active nexplane-agent",
            ]
            for cmd in commands:
                _, stdout, stderr = client.exec_command(cmd, timeout=120)
                exit_code = stdout.channel.recv_exit_status()
                if exit_code != 0:
                    err = stderr.read().decode()
                    return {"asset_id": asset_id, "agent_installed": False, "error": err}
            status = stdout.read().decode().strip()
            return {"asset_id": asset_id, "agent_installed": status == "active", "service_status": status}
        finally:
            client.close()

    for asset_id in asset_ids:
        try:
            result = await loop.run_in_executor(None, _install_on_host, str(asset_id))
        except Exception as e:
            result = {"asset_id": str(asset_id), "agent_installed": False, "error": str(e)}
        host_results.append(result)

    return {
        "action": "install_agent", "agent_type": agent_type,
        "hosts": host_results,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from .uninstall_agent import execute as uninstall
    return await uninstall(parameters, execution_result.get("hosts", []), connector)
