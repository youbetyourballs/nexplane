# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Executor: set a Windows registry value via PowerShell (delivered through SSH/WinRM connector)."""
from typing import Any


async def execute(parameters: dict, asset_ids: list[str], connector: Any) -> dict:
    key_path = parameters["key_path"]
    value_name = parameters["value_name"]
    value_data = parameters["value_data"]
    value_type = parameters["value_type"]

    read_cmd = (
        f"$v = Get-ItemProperty -Path '{key_path}' -Name '{value_name}' -ErrorAction SilentlyContinue; "
        f"if ($v) {{ $v.{value_name} }} else {{ '' }}"
    )
    original_raw = await connector.run_command(read_cmd)
    original_value = original_raw.strip() or None
    existed = original_value is not None

    apply_cmd = (
        f"New-ItemProperty -Path '{key_path}' -Name '{value_name}' "
        f"-Value '{value_data}' -PropertyType '{value_type}' -Force | Out-Null"
    )
    await connector.run_command(apply_cmd)

    return {
        "success": True,
        "pre_state": {"existed": existed, "original_value": original_value},
    }


async def rollback(parameters: dict, execution_result: dict, connector: Any) -> dict:
    key_path = parameters["key_path"]
    value_name = parameters["value_name"]
    value_type = parameters["value_type"]
    pre_state = execution_result.get("pre_state", {})

    if pre_state.get("existed"):
        original_value = pre_state["original_value"]
        restore_cmd = (
            f"New-ItemProperty -Path '{key_path}' -Name '{value_name}' "
            f"-Value '{original_value}' -PropertyType '{value_type}' -Force | Out-Null"
        )
        await connector.run_command(restore_cmd)
    else:
        remove_cmd = (
            f"Remove-ItemProperty -Path '{key_path}' -Name '{value_name}' -ErrorAction SilentlyContinue"
        )
        await connector.run_command(remove_cmd)

    return {"rolled_back": True}
