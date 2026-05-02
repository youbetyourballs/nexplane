import asyncio
from datetime import datetime, timezone

_USERS = [
    ("jsmith", "user", ["Domain Users", "VPN-Access"]),
    ("aadmin", "admin", ["Domain Admins", "Enterprise Admins"]),
    ("svc-backup", "service_account", ["Backup Operators"]),
    ("svc-monitoring", "service_account", ["Monitoring"]),
    ("bjones", "user", ["Domain Users"]),
    ("cwhite", "user", ["Domain Users", "Finance-Read"]),
]


def _mock_response():
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for username, account_type, groups in _USERS:
        tag = f"ad-{account_type.replace('_', '-')}"
        results.append({
            "name": username,
            "asset_type": "identity",
            "environment": "prod",
            "criticality": "high" if account_type in ("admin", "service_account") else "medium",
            "tags": [tag, "ad-user"],
            "asset_metadata": {
                "account_type": account_type,
                "groups": groups,
                "enabled": True,
                "last_login": now,
                "ad_source": "active_directory",
            },
        })
    return results


async def _real_execute(creds: dict) -> list:
    from ._client import get_connection
    base_dn = creds.get("base_dn", "DC=corp,DC=local")

    def _sync():
        conn = get_connection(creds)
        conn.search(
            base_dn,
            "(objectClass=user)",
            attributes=["sAMAccountName", "memberOf", "userAccountControl", "lastLogon", "description"],
        )
        entries = list(conn.entries)
        conn.unbind()
        return entries

    entries = await asyncio.get_event_loop().run_in_executor(None, _sync)
    now = datetime.now(timezone.utc).isoformat()
    results = []
    for entry in entries:
        sam = str(entry.sAMAccountName) if hasattr(entry, "sAMAccountName") else "unknown"
        uac = int(str(entry.userAccountControl)) if hasattr(entry, "userAccountControl") and str(entry.userAccountControl).isdigit() else 512
        enabled = not bool(uac & 2)
        member_of = [str(g) for g in entry.memberOf] if hasattr(entry, "memberOf") else []
        is_admin = any("admin" in g.lower() for g in member_of)
        account_type = "admin" if is_admin else "user"
        tag = f"ad-{account_type}"
        results.append({
            "name": sam,
            "asset_type": "identity",
            "environment": "prod",
            "criticality": "high" if is_admin else "medium",
            "tags": [tag, "ad-user"],
            "asset_metadata": {
                "account_type": account_type,
                "groups": member_of,
                "enabled": enabled,
                "last_login": now,
                "ad_source": "active_directory",
            },
        })
    return results if results else _mock_response()


async def execute(parameters: dict, asset_ids: list, connector) -> list:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response()
    return await _real_execute(creds)

