"""Active Directory service account password rotation via LDAP."""

from typing import Any


async def update_ad_password(
    connector_config: Any,
    username: str,
    domain: str,
    new_password: str,
) -> None:
    """
    Sets the unicodePwd attribute on the AD user object using ldap3.
    Raises RuntimeError if the LDAP modify operation returns a non-zero result code.
    """
    import ldap3

    server = ldap3.Server(connector_config.ldap_host, use_ssl=True)
    conn = ldap3.Connection(
        server,
        user=connector_config.bind_dn,
        password=connector_config.bind_password,
        auto_bind=True,
    )
    dn = f"CN={username},{connector_config.users_base_dn}"
    encoded = (f'"{new_password}"').encode("utf-16-le")
    conn.modify(dn, {"unicodePwd": [(ldap3.MODIFY_REPLACE, [encoded])]})
    if conn.result["result"] != 0:
        raise RuntimeError(
            f"AD password update failed: {conn.result['description']}"
        )
