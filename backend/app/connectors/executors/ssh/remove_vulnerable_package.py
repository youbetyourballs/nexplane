# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: remove a package that has no available patch and poses unacceptable risk."""
from typing import Any

ROLLBACK_CAPABILITY = "full"

_VERSION_CMD = (
    "rpm -q '{pkg}' 2>/dev/null || "
    "dpkg -s '{pkg}' 2>/dev/null | grep '^Version' | awk '{{print $2}}' || "
    "echo ''"
)
_REMOVE_CMD = (
    "apt-get remove -y '{pkg}' 2>/dev/null || "
    "yum remove -y '{pkg}' 2>/dev/null || "
    "dnf remove -y '{pkg}' 2>/dev/null"
)
_INSTALL_CMD = (
    "apt-get install -y '{pkg}={ver}' 2>/dev/null || "
    "yum install -y '{pkg}-{ver}' 2>/dev/null || "
    "dnf install -y '{pkg}-{ver}' 2>/dev/null"
)


async def execute(parameters: dict, asset_ids: list[str], connector: Any) -> dict:
    pkg = parameters["package_name"]
    ver_raw = await connector.run_command(_VERSION_CMD.format(pkg=pkg))
    installed_version = ver_raw.strip() or None
    await connector.run_command(_REMOVE_CMD.format(pkg=pkg))
    return {"success": True, "pre_state": {"installed_version": installed_version}}


async def rollback(parameters: dict, execution_result: dict, connector: Any) -> dict:
    pkg = parameters["package_name"]
    pre_state = execution_result.get("pre_state", {})
    ver = pre_state.get("installed_version")
    if not ver:
        return {"rolled_back": False, "reason": "No version captured in pre_state — cannot reinstall pinned version"}
    await connector.run_command(_INSTALL_CMD.format(pkg=pkg, ver=ver))
    return {"rolled_back": True}
