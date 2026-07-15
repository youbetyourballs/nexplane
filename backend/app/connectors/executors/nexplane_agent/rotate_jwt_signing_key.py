# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Rotate JWT signing key — generates new RSA/EC key pair, updates app config, returns new public key."""
from app.connectors.executors.nexplane_agent import _dispatch

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """
    Parameters:
      algorithm: "RS256" | "ES256" (default: RS256)
      config_path: path to app config file containing the JWT key
      key_field: field name in config (default: "jwt_private_key")
      service_name: systemd service to restart after rotation
    """
    result = await _dispatch.dispatch_agent_job(
        command="rotate_jwt_signing_key",
        parameters=parameters,
        asset_ids=list(asset_ids),
        timeout_seconds=60,
    )
    result["_asset_ids"] = [str(a) for a in asset_ids]
    return result


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    asset_ids = execution_result.get("_asset_ids") or []
    backup_path = execution_result.get("backup_path", "")
    if not backup_path:
        return {"rolled_back": False, "reason": "no backup_path in execution result"}
    return await _dispatch.dispatch_agent_job(
        command="rotate_jwt_signing_key",
        parameters={**parameters, "action": "restore", "backup_path": backup_path},
        asset_ids=asset_ids,
        timeout_seconds=30,
    )
