# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from app.connectors.executors.nexplane_agent import _dispatch


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Rotate SSH authorized_keys on target hosts via the agent.

    Parameters:
        username: OS user whose authorized_keys to modify
        old_key_fingerprint: SHA256 fingerprint of key to remove (without 'SHA256:' prefix)
        new_public_key: Full public key string to add
        auth_keys_path: Optional override for authorized_keys path
    """
    username = parameters.get("username", "")
    if not username:
        raise ValueError("username is required")

    # Build auth_keys_path if not explicitly provided
    if not parameters.get("auth_keys_path"):
        home = "/root" if username == "root" else f"/home/{username}"
        parameters = {**parameters, "auth_keys_path": f"{home}/.ssh/authorized_keys"}

    result = await _dispatch.dispatch_agent_job(
        command="rotate_ssh_keys",
        parameters={**parameters, "action": "backup"},
        asset_ids=list(asset_ids),
        timeout_seconds=30,
    )
    backup_path = result.get("backup_path", "")

    # Remove old key
    result = await _dispatch.dispatch_agent_job(
        command="rotate_ssh_keys",
        parameters={**parameters, "action": "remove_old"},
        asset_ids=list(asset_ids),
        timeout_seconds=30,
    )

    # Add new key
    result = await _dispatch.dispatch_agent_job(
        command="rotate_ssh_keys",
        parameters={**parameters, "action": "add_new"},
        asset_ids=list(asset_ids),
        timeout_seconds=30,
    )

    return {
        "action": "rotate_ssh_keys",
        "username": username,
        "backup_path": backup_path,
        "status": "rotated",
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    backup_path = execution_result.get("backup_path", "")
    if not backup_path:
        return {"rolled_back": False, "reason": "no backup_path in execution result"}

    result = await _dispatch.dispatch_agent_job(
        command="rotate_ssh_keys",
        parameters={**parameters, "action": "restore", "backup_path": backup_path},
        asset_ids=asset_ids,
        timeout_seconds=30,
    )
    return {"rolled_back": True, "restored_from": backup_path}
