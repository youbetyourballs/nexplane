# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    """Delete an AD user account. Called as the rollback action for create_user.

    Expects 'dn' in parameters (stored from create_user execution result).
    Falls back to 'username' for SSM path if dn unavailable.
    """
    creds = getattr(connector, "credentials", {}) if connector else {}
    dn = parameters.get("dn")
    username = parameters.get("username")

    if not dn and not username:
        return {"rolled_back": False, "reason": "no dn or username in parameters"}

    if not creds:
        return {"rolled_back": True, "simulated": True, "dn": dn, "username": username}

    from ._client import has_ssm_transport, prepare_ad_target
    if has_ssm_transport(creds):
        return await _ssm_execute(dn or username, creds)
    creds = await prepare_ad_target(connector, creds)
    return await _real_execute(dn, creds)


async def _ssm_execute(identity: str, creds: dict) -> dict:
    from ._client import run_ssm_powershell
    import re as _re
    if identity.upper().startswith("CN="):
        sam = _re.search(r"CN=([^,]+)", identity)
        identity_arg = f"'{sam.group(1)}'" if sam else f"'{identity}'"
    else:
        identity_arg = f"'{identity}'"
    await run_ssm_powershell(creds, [
        f"Remove-ADUser -Identity {identity_arg} -Confirm:$false",
    ])
    return {"rolled_back": True, "identity": identity, "transport": "ssm"}


async def _real_execute(dn: str, creds: dict) -> dict:
    import asyncio
    from ._client import get_connection

    def _sync():
        conn = get_connection(creds)
        conn.delete(dn)
        result = conn.result
        conn.unbind()
        return result

    result = await asyncio.get_event_loop().run_in_executor(None, _sync)
    return {"rolled_back": True, "dn": dn, "ldap_result": str(result)}
