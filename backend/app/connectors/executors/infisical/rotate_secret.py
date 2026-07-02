# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
"""Infisical — rotate (update) a secret value, storing old value for rollback."""
import secrets
import string
from datetime import datetime, timezone
from typing import Optional


def _generate_value(length: int = 32) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(chars) for _ in range(length))


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    from ._client import get_infisical_client

    workspace_id = parameters.get("workspace_id", "")
    environment = parameters.get("environment", "")
    secret_name = parameters.get("secret_name", "")
    if not workspace_id or not environment or not secret_name:
        raise ValueError("workspace_id, environment, and secret_name are required")

    client = await get_infisical_client(connector)
    if client is None:
        return {
            "action": "rotate_infisical_secret",
            "status": "skipped",
            "reason": "no_infisical_credentials",
            "secret_name": secret_name,
        }

    # Read current value
    old_value: Optional[str] = None
    try:
        current = client.get_secret(workspace_id, environment, secret_name)
        old_value = current.get("secretValue")
    except Exception:
        old_value = None

    # Generate or use provided new value
    new_value = parameters.get("new_value") or _generate_value(32)

    client.update_secret(workspace_id, environment, secret_name, new_value)

    return {
        "action": "rotate_infisical_secret",
        "secret_name": secret_name,
        "workspace_id": workspace_id,
        "environment": environment,
        "old_value": old_value,
        "rotated_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from ._client import get_infisical_client

    secret_name = execution_result.get("secret_name") or parameters.get("secret_name", "")
    workspace_id = execution_result.get("workspace_id") or parameters.get("workspace_id", "")
    environment = execution_result.get("environment") or parameters.get("environment", "")
    old_value = execution_result.get("old_value")

    if not secret_name or not workspace_id or not environment:
        return {"rolled_back": False, "reason": "missing_identifiers"}
    if old_value is None:
        return {"rolled_back": False, "reason": "no_old_value_stored"}

    client = await get_infisical_client(connector)
    if client is None:
        return {"rolled_back": False, "reason": "no_infisical_credentials"}

    client.update_secret(workspace_id, environment, secret_name, old_value)

    return {
        "rolled_back": True,
        "secret_name": secret_name,
        "action": "rotate_infisical_secret_rollback",
    }
