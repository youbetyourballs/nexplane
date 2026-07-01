# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import random
import string
from datetime import datetime, timezone


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Upload and install a SELinux policy module (.te file content or .pp path) via semodule."""
    creds = getattr(connector, "credentials", {}) or {}
    policy_name = parameters.get("policy_name", "")
    policy_content = parameters.get("policy_content", "")  # .te source
    policy_pp_url = parameters.get("policy_pp_url", "")    # pre-compiled .pp download URL

    if not policy_name:
        return {"status": "error", "message": "policy_name is required"}
    if not policy_content and not policy_pp_url:
        return {"status": "error", "message": "policy_content or policy_pp_url is required"}

    policy_id = "pol-" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))

    if not creds:
        return {
            "action": "apply_selinux_policy",
            "policy_name": policy_name,
            "policy_id": policy_id,
            "hosts": [{"asset_id": a, "applied": True} for a in asset_ids],
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "mock": True,
        }

    from ._client import get_ssh_client
    loop = asyncio.get_event_loop()
    host_results = []

    def _apply(asset_id: str):
        client = get_ssh_client(creds)
        try:
            if policy_pp_url:
                pp_path = f"/tmp/{policy_name}.pp"
                _, stdout, stderr = client.exec_command(
                    f"curl -fsSL '{policy_pp_url}' -o '{pp_path}' && sudo semodule -i '{pp_path}'",
                    timeout=120,
                )
            else:
                # Compile from .te source: write file, run checkmodule + semodule_package + semodule
                te_path = f"/tmp/{policy_name}.te"
                mod_path = f"/tmp/{policy_name}.mod"
                pp_path = f"/tmp/{policy_name}.pp"
                sftp = client.open_sftp()
                with sftp.open(te_path, "w") as f:
                    f.write(policy_content)
                sftp.close()
                compile_cmd = (
                    f"checkmodule -M -m -o '{mod_path}' '{te_path}' && "
                    f"semodule_package -o '{pp_path}' -m '{mod_path}' && "
                    f"sudo semodule -i '{pp_path}'"
                )
                _, stdout, stderr = client.exec_command(compile_cmd, timeout=120)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                return {"asset_id": asset_id, "applied": False, "error": stderr.read().decode()}
            return {"asset_id": asset_id, "applied": True}
        finally:
            client.close()

    for asset_id in asset_ids:
        try:
            result = await loop.run_in_executor(None, _apply, str(asset_id))
        except Exception as exc:
            result = {"asset_id": str(asset_id), "applied": False, "error": str(exc)}
        host_results.append(result)

    return {
        "action": "apply_selinux_policy",
        "policy_name": policy_name,
        "policy_id": policy_id,
        "hosts": host_results,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from .revert_selinux_policy import execute as revert
    return await revert(
        {"policy_name": execution_result.get("policy_name", parameters.get("policy_name", ""))},
        execution_result.get("hosts", []),
        connector,
    )
