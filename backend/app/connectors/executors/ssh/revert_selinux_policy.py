# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Remove a named SELinux policy module, reverting the system to the state before it was applied."""
    creds = getattr(connector, "credentials", {}) or {}
    policy_name = parameters.get("policy_name", "")
    previous_policy_id = parameters.get("previous_policy_id", "")

    if not policy_name and not previous_policy_id:
        return {"status": "error", "message": "policy_name or previous_policy_id is required"}

    target = policy_name or previous_policy_id

    if not creds:
        return {
            "action": "revert_selinux_policy",
            "policy_name": target,
            "hosts": [{"asset_id": a, "reverted": True} for a in asset_ids],
            "reverted_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }

    from ._client import get_ssh_client
    loop = asyncio.get_event_loop()
    host_results = []

    def _revert(asset_id: str):
        client = get_ssh_client(creds)
        try:
            _, stdout, stderr = client.exec_command(
                f"sudo semodule -r {target} 2>&1; echo EXIT:$?",
                timeout=60,
            )
            out = stdout.read().decode()
            exit_code = 0 if "EXIT:0" in out else 1
            if exit_code != 0 and "No such file" not in out and "not found" not in out.lower():
                return {"asset_id": asset_id, "reverted": False, "error": out.strip()}
            return {"asset_id": asset_id, "reverted": True}
        finally:
            client.close()

    for asset_id in asset_ids:
        try:
            result = await loop.run_in_executor(None, _revert, str(asset_id))
        except Exception as exc:
            result = {"asset_id": str(asset_id), "reverted": False, "error": str(exc)}
        host_results.append(result)

    return {
        "action": "revert_selinux_policy",
        "policy_name": target,
        "hosts": host_results,
        "reverted_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "revert_selinux_policy has no rollback"}
