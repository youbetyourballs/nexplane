# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations

import asyncio
from datetime import datetime, timezone


async def execute(parameters, asset_ids, connector):
    # type: (dict, list, object) -> dict
    creds = getattr(connector, "credentials", {}) or {}
    if not creds:
        return {
            "action": "winrm_check_prerequisites",
            "all_passed": True,
            "checks": [{"name": "mock", "passed": True, "output": "no credentials — mock mode"}],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    from ._client import prepare_winrm_client

    client = await prepare_winrm_client(connector)
    loop = asyncio.get_event_loop()

    def _check():
        checks = []

        # OS version — requires Windows Server 2016+ (build >= 14393)
        stdout, stderr, rc = client.run_ps(
            "(Get-WmiObject Win32_OperatingSystem).Caption + ' build ' + "
            "(Get-WmiObject Win32_OperatingSystem).BuildNumber"
        )
        os_info = stdout.strip()
        build_str = ""
        for part in os_info.split():
            if part.isdigit():
                build_str = part
        os_ok = int(build_str) >= 14393 if build_str else False
        checks.append({"name": "os_version", "passed": os_ok, "output": os_info})

        # Disk space — free on C: must be > 500 MB (512 000 000 bytes)
        stdout2, _, rc2 = client.run_ps(
            "(Get-PSDrive C).Free"
        )
        try:
            free_bytes = int(stdout2.strip())
            disk_ok = free_bytes > 512000000
        except ValueError:
            free_bytes = 0
            disk_ok = False
        checks.append({"name": "disk_space_c", "passed": disk_ok,
                        "output": "{} bytes free".format(free_bytes)})

        # .NET version — look for 4.x or later in registry
        stdout3, _, rc3 = client.run_ps(
            "(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\NET Framework Setup\\NDP\\v4\\Full' "
            "-ErrorAction SilentlyContinue).Release"
        )
        try:
            release = int(stdout3.strip())
            dotnet_ok = release >= 394802  # .NET 4.6.2+
        except ValueError:
            release = 0
            dotnet_ok = False
        checks.append({"name": "dotnet_4x", "passed": dotnet_ok,
                        "output": "release key {}".format(release)})

        # WinRM accessible — if we got here the session connected
        checks.append({"name": "winrm_accessible", "passed": True, "output": "session established"})

        return checks

    try:
        checks = await loop.run_in_executor(None, _check)
    except Exception as e:
        return {
            "action": "winrm_check_prerequisites",
            "all_passed": False,
            "error": str(e),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    return {
        "action": "winrm_check_prerequisites",
        "checks": checks,
        "all_passed": all(c["passed"] for c in checks),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


async def rollback(parameters, execution_result, connector):
    # type: (dict, dict, object) -> dict
    return {"rolled_back": True, "reason": "check_prerequisites is read-only"}
