# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
import json
from datetime import datetime, timezone

# Smart Card Required flag in userAccountControl
_SC_REQUIRED_BIT = 0x40000  # 262144


def _get_winrm_session(creds: dict):
    from ._client import get_winrm_session
    return get_winrm_session(creds)


def _run_winrm_ps(session, script: str):
    """Run PowerShell via the session object. Session holds creds internally."""
    result = session.run_ps(script)
    stdout = result.std_out.decode("utf-8", errors="replace").strip() if result.std_out else ""
    stderr = result.std_err.decode("utf-8", errors="replace").strip() if result.std_err else ""
    return stdout, stderr, result.status_code


async def _run_ps_async(session, script: str):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _run_winrm_ps, session, script)


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    if not creds.get("winrm_hostname"):
        return {"status": "error", "error": "AD connector missing winrm_hostname credential"}

    username = parameters.get("username", "").strip()
    if not username:
        return {"status": "error", "error": "username parameter required"}

    session = _get_winrm_session(creds)

    ps_script = f"""
Import-Module ActiveDirectory
$user = Get-ADUser -Identity '{username}' -Properties userAccountControl
if (-not $user) {{ Write-Output '{{"error":"user not found"}}'; exit 1 }}
$uacBefore = $user.userAccountControl
$uacAfter  = $uacBefore -bor {_SC_REQUIRED_BIT}
Set-ADUser -Identity '{username}' -Replace @{{userAccountControl=$uacAfter}}
Write-Output (ConvertTo-Json @{{success=$true; uac_before=$uacBefore; uac_after=$uacAfter}})
"""
    try:
        stdout, stderr, rc = await _run_ps_async(session, ps_script)
        if rc != 0:
            return {"status": "error", "error": stderr or stdout, "action": "enforce_mfa"}
        data = json.loads(stdout.strip())
        if data.get("error"):
            return {"status": "error", "error": data["error"], "action": "enforce_mfa"}
        return {
            "action": "enforce_mfa",
            "username": username,
            "mfa_required": True,
            "uac_before": data.get("uac_before"),
            "uac_after": data.get("uac_after"),
            "enforced_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "action": "enforce_mfa"}


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {}) or {}
    if not creds.get("winrm_hostname"):
        return {"rolled_back": False, "reason": "AD connector missing winrm_hostname credential"}

    username = execution_result.get("username") or parameters.get("username", "")
    uac_before = execution_result.get("uac_before")
    if uac_before is None:
        uac_expr = f"$uacAfter = $user.userAccountControl -band (-bnot {_SC_REQUIRED_BIT})"
    else:
        uac_expr = f"$uacAfter = {uac_before}"

    session = _get_winrm_session(creds)
    ps_script = f"""
Import-Module ActiveDirectory
$user = Get-ADUser -Identity '{username}' -Properties userAccountControl
{uac_expr}
Set-ADUser -Identity '{username}' -Replace @{{userAccountControl=$uacAfter}}
Write-Output (ConvertTo-Json @{{success=$true}})
"""
    try:
        stdout, stderr, rc = await _run_ps_async(session, ps_script)
        return {
            "rolled_back": rc == 0,
            "username": username,
            "reason": stderr if rc != 0 else None,
        }
    except Exception as e:
        return {"rolled_back": False, "reason": str(e)}
