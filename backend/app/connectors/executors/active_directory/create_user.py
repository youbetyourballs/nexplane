# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import asyncio
from datetime import datetime, timezone

ROLLBACK_CAPABILITY = "full"


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return {
            "action": "create_ad_account",
            "username": parameters.get("username"),
            "simulated": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    from ._client import has_ssm_transport, prepare_ad_target
    if has_ssm_transport(creds):
        return await _ssm_execute(parameters, creds)
    creds = await prepare_ad_target(connector, creds)
    return await _real_execute(parameters, creds)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    dn = execution_result.get("dn")
    if not dn:
        return {"rolled_back": False, "reason": "no dn in execution_result — cannot delete"}
    if not creds:
        return {"rolled_back": True, "simulated": True, "dn": dn}
    from ._client import has_ssm_transport, prepare_ad_target
    if has_ssm_transport(creds):
        return await _ssm_rollback(dn, creds)
    creds = await prepare_ad_target(connector, creds)
    return await _real_rollback(dn, creds)


async def _ssm_execute(parameters: dict, creds: dict) -> dict:
    from ._client import run_ssm_powershell
    username = parameters["username"]
    first_name = parameters["first_name"]
    last_name = parameters["last_name"]
    temp_password = parameters["temp_password"]
    ou = parameters.get("ou") or creds.get("base_dn", "DC=corp,DC=local")
    dn = f"CN={first_name} {last_name},{ou}"
    output = await run_ssm_powershell(creds, [
        f"New-ADUser -Name '{first_name} {last_name}' -SamAccountName '{username}' "
        f"-GivenName '{first_name}' -Surname '{last_name}' "
        f"-AccountPassword (ConvertTo-SecureString '{temp_password}' -AsPlainText -Force) "
        f"-Enabled $true -PassThru | Out-Null",
        f"$u = Get-ADUser -Identity '{username}' -Properties DistinguishedName",
        "Write-Output \"DN:$($u.DistinguishedName)\"",
    ])
    import re as _re
    dn_match = _re.search(r"DN:(.+)", output)
    actual_dn = dn_match.group(1).strip() if dn_match else dn
    return {
        "action": "create_ad_account",
        "username": username,
        "dn": actual_dn,
        "created": bool(dn_match),
        "transport": "ssm",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def _ssm_rollback(dn: str, creds: dict) -> dict:
    from ._client import run_ssm_powershell
    # Parse sAMAccountName from DN (CN=First Last,OU=...)
    import re as _re
    sam = _re.search(r"CN=([^,]+)", dn)
    identity = f"'{sam.group(1)}'" if sam else f"'{dn}'"
    await run_ssm_powershell(creds, [
        f"Remove-ADUser -Identity {identity} -Confirm:$false",
    ])
    return {"rolled_back": True, "dn": dn, "transport": "ssm"}


async def _real_execute(parameters: dict, creds: dict) -> dict:
    from ._client import get_connection
    from ldap3 import MODIFY_REPLACE

    username = parameters["username"]
    first_name = parameters["first_name"]
    last_name = parameters["last_name"]
    ou = parameters.get("ou") or creds.get("base_dn", "DC=corp,DC=local")
    temp_password = parameters["temp_password"]
    dn = f"CN={first_name} {last_name},{ou}"

    # unicodePwd must be UTF-16-LE encoded and double-quoted
    encoded_pw = f'"{temp_password}"'.encode("utf-16-le")

    attrs = {
        "objectClass": ["top", "person", "organizationalPerson", "user"],
        "cn": f"{first_name} {last_name}",
        "sn": last_name,
        "givenName": first_name,
        "userPrincipalName": f"{username}@{_domain_from_base(creds)}",
        "sAMAccountName": username,
        "unicodePwd": encoded_pw,
        "userAccountControl": "512",  # NORMAL_ACCOUNT, enabled
    }

    def _sync():
        conn = get_connection(creds)
        conn.add(dn, attributes=attrs)
        result = conn.result
        conn.unbind()
        return result

    result = await asyncio.get_event_loop().run_in_executor(None, _sync)

    confirmed = await _verify_exists(username, creds)
    return {
        "action": "create_ad_account",
        "username": username,
        "dn": dn,
        "created": confirmed,
        "ldap_result": str(result),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def _real_rollback(dn: str, creds: dict) -> dict:
    from ._client import get_connection

    def _sync():
        conn = get_connection(creds)
        conn.delete(dn)
        result = conn.result
        conn.unbind()
        return result

    result = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {
        "rolled_back": True,
        "dn": dn,
        "ldap_result": str(result),
    }


async def _verify_exists(username: str, creds: dict, retries: int = 3, delay: float = 1.0) -> bool:
    from ._client import get_connection
    from ldap3 import SUBTREE
    base_dn = creds.get("base_dn", "DC=corp,DC=local")

    def _check():
        conn = get_connection(creds)
        conn.search(base_dn, f"(sAMAccountName={username})", SUBTREE, attributes=["sAMAccountName"])
        found = len(conn.entries) > 0
        conn.unbind()
        return found

    for _ in range(retries):
        if await asyncio.get_event_loop().run_in_executor(None, _check):
            return True
        await asyncio.sleep(delay)
    return False


def _domain_from_base(creds: dict) -> str:
    base_dn = creds.get("base_dn", "DC=corp,DC=local")
    parts = [p.split("=")[1] for p in base_dn.split(",") if p.upper().startswith("DC=")]
    return ".".join(parts)
