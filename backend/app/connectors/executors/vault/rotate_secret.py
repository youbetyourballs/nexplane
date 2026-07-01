# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Rotate a Vault secret — reads current value, generates new one, writes back."""
from __future__ import annotations
import secrets
import string
from datetime import datetime, timezone
from ._client import get_vault_client


def _generate_value(length: int = 32) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%"
    return "".join(secrets.choice(chars) for _ in range(length))


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    path = parameters.get("secret_path") or parameters.get("path", "")
    mount_point = parameters.get("mount_point", "secret")
    if not path:
        raise ValueError("secret_path is required")

    client = get_vault_client(connector)
    if not client:
        return {
            "action": "rotate_vault_secret",
            "status": "skipped",
            "reason": "no_vault_credentials",
            "path": path,
        }

    # Read current secret to preserve non-password fields
    try:
        current_data = client.read_secret(path, mount_point)
    except Exception:
        current_data = {}

    # Generate new value for the target field
    target_field = parameters.get("field", "password")
    new_value = parameters.get("new_value") or _generate_value(32)
    new_data = {**current_data, target_field: new_value}

    result = client.write_secret(path, new_data, mount_point)

    return {
        "action": "rotate_vault_secret",
        "path": path,
        "mount_point": mount_point,
        "field": target_field,
        "version": result.get("version"),
        "rotated_at": datetime.now(timezone.utc).isoformat(),
        "_asset_ids": [str(a) for a in asset_ids],
    }


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {"rolled_back": False, "reason": "Vault secret rotation is append-only in KV v2; restore via Vault version history"}
