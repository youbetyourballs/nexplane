# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: blacklist and unload a vulnerable Linux kernel module."""
from typing import Any

ROLLBACK_CAPABILITY = "full"

_BLACKLIST_FILE = "/etc/modprobe.d/nexplane-disable.conf"


async def execute(parameters: dict, asset_ids: list[str], connector: Any) -> dict:
    feature = parameters["feature"]
    await connector.run_command(
        f"echo 'blacklist {feature}' >> {_BLACKLIST_FILE}"
    )
    await connector.run_command(
        f"rmmod '{feature}' 2>/dev/null || true"
    )
    return {"success": True, "feature": feature}


async def rollback(parameters: dict, execution_result: dict, connector: Any) -> dict:
    feature = parameters["feature"]
    await connector.run_command(
        f"sed -i '/^blacklist {feature}$/d' {_BLACKLIST_FILE}"
    )
    await connector.run_command(
        f"modprobe '{feature}' 2>/dev/null || true"
    )
    return {"rolled_back": True, "feature": feature}
