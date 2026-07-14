# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

ROLLBACK_CAPABILITY = "full"


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
        # Capture pre-state via SSM PowerShell Get-ADUser before deletion
        user_data = await _capture_user_ssm(dn or username, creds)
        from app.services.pre_state_store import PreStateStore
        from app.database import AsyncSessionLocal
        import uuid as _uuid
        async with AsyncSessionLocal() as db:
            await PreStateStore.capture(
                db,
                _uuid.UUID(str(parameters["cr_id"])),
                str(parameters.get("step_id", "step_0")),
                _uuid.UUID(str(parameters["org_id"])),
                user_data,
            )
            await db.commit()
        return await _ssm_execute(dn or username, creds)
    creds = await prepare_ad_target(connector, creds)
    # Capture pre-state via LDAP before deletion
    user_data = await _capture_user_ldap(dn, creds)
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal
    import uuid as _uuid
    async with AsyncSessionLocal() as db:
        await PreStateStore.capture(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
            user_data,
        )
        await db.commit()
    return await _real_execute(dn, creds)


async def _capture_user_ssm(identity: str, creds: dict) -> dict:
    """Capture AD user attributes via SSM PowerShell before deletion."""
    import json as _json
    from ._client import run_ssm_powershell
    import re as _re
    if identity.upper().startswith("CN="):
        sam = _re.search(r"CN=([^,]+)", identity)
        identity_arg = f"'{sam.group(1)}'" if sam else f"'{identity}'"
    else:
        identity_arg = f"'{identity}'"
    try:
        out = await run_ssm_powershell(creds, [
            f"Get-ADUser -Identity {identity_arg} -Properties * | "
            f"Select-Object DistinguishedName,SamAccountName,GivenName,Surname,"
            f"UserPrincipalName,Description,Enabled,MemberOf | ConvertTo-Json",
        ])
        return _json.loads(out) if out else {"identity": identity}
    except Exception:
        return {"identity": identity}


async def _capture_user_ldap(dn: str, creds: dict) -> dict:
    """Capture AD user attributes via LDAP before deletion."""
    import asyncio
    from ._client import get_connection
    ATTRS = ["distinguishedName", "sAMAccountName", "givenName", "sn",
             "userPrincipalName", "description", "userAccountControl"]

    def _sync():
        conn = get_connection(creds)
        conn.search(dn, "(objectClass=user)", attributes=ATTRS)
        entries = conn.entries
        conn.unbind()
        if entries:
            entry = entries[0]
            return {attr: str(getattr(entry, attr, "")) for attr in ATTRS}
        return {"dn": dn}

    try:
        return await asyncio.get_event_loop().run_in_executor(None, _sync)
    except Exception:
        return {"dn": dn}


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


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    from app.services.pre_state_store import PreStateStore
    from app.database import AsyncSessionLocal
    import uuid as _uuid

    async with AsyncSessionLocal() as db:
        pre_state = await PreStateStore.retrieve(
            db,
            _uuid.UUID(str(parameters["cr_id"])),
            str(parameters.get("step_id", "step_0")),
            _uuid.UUID(str(parameters["org_id"])),
        )
    if not pre_state:
        return {"rolled_back": False, "reason": "no pre-state captured — cannot reconstitute AD user"}

    sam = pre_state.get("SamAccountName") or pre_state.get("sAMAccountName") or parameters.get("username", "")
    given = pre_state.get("GivenName") or pre_state.get("givenName") or ""
    surname = pre_state.get("Surname") or pre_state.get("sn") or ""
    upn = pre_state.get("UserPrincipalName") or pre_state.get("userPrincipalName") or ""
    dn = pre_state.get("DistinguishedName") or pre_state.get("distinguishedName") or ""
    ou = dn.split(",", 1)[-1] if "," in dn else "CN=Users,DC=example,DC=com"

    creds = getattr(connector, "credentials", {}) if connector else {}
    from ._client import has_ssm_transport, run_ssm_powershell
    if not creds:
        return {"rolled_back": False, "reason": "no connector credentials — cannot reconstitute AD user"}

    name = f"{given} {surname}".strip() or sam
    cmd = f"New-ADUser -SamAccountName '{sam}' -Name '{name}' -GivenName '{given}' -Surname '{surname}' -Path '{ou}'"
    if upn:
        cmd += f" -UserPrincipalName '{upn}'"
    if pre_state.get("Enabled") is False or pre_state.get("userAccountControl") == "514":
        cmd += " -Enabled $false"

    if has_ssm_transport(creds):
        try:
            await run_ssm_powershell(creds, [cmd])
            return {"rolled_back": True, "samaccountname": sam, "note": "AD user recreated. Password must be reset by an admin."}
        except Exception as exc:
            return {"rolled_back": False, "reason": f"New-ADUser failed: {exc}"}

    return {"rolled_back": False, "reason": "Non-SSM reconstitution not implemented; recreate user manually."}
