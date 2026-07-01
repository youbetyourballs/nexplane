# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""LDAP client wrapper using ldap3."""
from __future__ import annotations
import ssl

from ldap3 import Server, Connection, ALL, MODIFY_REPLACE, SUBTREE, Tls
from ldap3.core.exceptions import LDAPException

from app.tunnel.routing import tcp_endpoint


class LDAPClient:
    def __init__(self, host: str, port: int, bind_dn: str, bind_password: str,
                 base_dn: str, use_ssl: bool = False, tls: "Tls | None" = None):
        self.host = host
        self.port = port
        self.bind_dn = bind_dn
        self.bind_password = bind_password
        self.base_dn = base_dn
        self.use_ssl = use_ssl
        # When routed via a localhost forwarder over LDAPS the real cert host
        # cannot be verified, so callers may pass a Tls(validate=CERT_NONE).
        self.tls = tls

    def _connect(self) -> Connection:
        server = Server(self.host, port=self.port, use_ssl=self.use_ssl,
                        get_info=ALL, tls=self.tls)
        conn = Connection(server, user=self.bind_dn, password=self.bind_password, auto_bind=True)
        return conn

    def _find_user_dn(self, conn: Connection, username: str) -> str | None:
        """Search by uid (OpenLDAP) then sAMAccountName (AD), return DN or None."""
        conn.search(self.base_dn, f"(uid={username})", SUBTREE, attributes=["uid"])
        if not conn.entries:
            conn.search(self.base_dn, f"(sAMAccountName={username})", SUBTREE,
                        attributes=["sAMAccountName"])
        return conn.entries[0].entry_dn if conn.entries else None

    def disable_user(self, username: str) -> dict:
        """Disable a user. Uses userAccountControl (AD) or pwdAccountLockedTime (OpenLDAP)."""
        conn = self._connect()
        try:
            user_dn = self._find_user_dn(conn, username)
            if not user_dn:
                return {"success": False, "error": f"User {username} not found"}
            # Try AD-style first (userAccountControl bit 0x2 = ACCOUNTDISABLE)
            # 514 = 0x202 = NORMAL_ACCOUNT | ACCOUNTDISABLE
            if conn.modify(user_dn, {"userAccountControl": [(MODIFY_REPLACE, [514])]}):
                return {"success": True, "user_dn": user_dn, "username": username,
                        "action": "disabled", "method": "userAccountControl"}
            # OpenLDAP ppolicy: set pwdAccountLockedTime to epoch
            if conn.modify(user_dn, {"pwdAccountLockedTime": [(MODIFY_REPLACE, ["000001010000Z"])]}):
                return {"success": True, "user_dn": user_dn, "username": username,
                        "action": "disabled", "method": "pwdAccountLockedTime"}
            # Last resort: set loginShell to nologin
            conn.modify(user_dn, {"loginShell": [(MODIFY_REPLACE, ["/sbin/nologin"])]})
            return {"success": True, "user_dn": user_dn, "username": username,
                    "action": "disabled", "method": "loginShell"}
        finally:
            conn.unbind()

    def enable_user(self, username: str) -> dict:
        """Re-enable a user. Uses userAccountControl (AD) or clears pwdAccountLockedTime (OpenLDAP)."""
        conn = self._connect()
        try:
            user_dn = self._find_user_dn(conn, username)
            if not user_dn:
                return {"success": False, "error": f"User {username} not found"}
            # Try AD-style first: 512 = 0x200 = NORMAL_ACCOUNT (enabled)
            if conn.modify(user_dn, {"userAccountControl": [(MODIFY_REPLACE, [512])]}):
                return {"success": True, "user_dn": user_dn, "username": username,
                        "action": "enabled", "method": "userAccountControl"}
            # OpenLDAP: clear the lock
            conn.modify(user_dn, {"pwdAccountLockedTime": [(MODIFY_REPLACE, [])]})
            return {"success": True, "user_dn": user_dn, "username": username,
                    "action": "enabled", "method": "pwdAccountLockedTime"}
        finally:
            conn.unbind()

    def create_user(self, username: str, display_name: str, password: str,
                    ou: str | None = None) -> dict:
        """Create a new LDAP user."""
        conn = self._connect()
        try:
            user_dn = f"uid={username},{ou or 'ou=users,' + self.base_dn}"
            attrs = {
                "objectClass": ["inetOrgPerson", "posixAccount", "shadowAccount"],
                "uid": username,
                "cn": display_name,
                "sn": username,
                "userPassword": password,
                "uidNumber": "10001",
                "gidNumber": "10001",
                "homeDirectory": f"/home/{username}",
                "loginShell": "/bin/bash",
            }
            result = conn.add(user_dn, attributes=attrs)
            if not result:
                return {"success": False, "error": str(conn.result)}
            return {"success": True, "user_dn": user_dn, "username": username}
        finally:
            conn.unbind()

    def delete_user(self, username: str) -> dict:
        conn = self._connect()
        try:
            conn.search(self.base_dn, f"(|(uid={username})(sAMAccountName={username}))",
                        SUBTREE, attributes=["sAMAccountName"])
            if not conn.entries:
                return {"success": False, "error": f"User {username} not found"}
            user_dn = conn.entries[0].entry_dn
            conn.delete(user_dn)
            return {"success": True, "username": username}
        finally:
            conn.unbind()

    def verify_bind(self, username: str, password: str) -> bool:
        """Try to bind as the user — returns True if auth succeeds."""
        try:
            server = Server(self.host, port=self.port, use_ssl=self.use_ssl, tls=self.tls)
            admin_conn = self._connect()
            admin_conn.search(self.base_dn, f"(|(uid={username})(sAMAccountName={username}))",
                              SUBTREE, attributes=["sAMAccountName"])
            if not admin_conn.entries:
                return False
            user_dn = admin_conn.entries[0].entry_dn
            admin_conn.unbind()
            test_conn = Connection(server, user=user_dn, password=password)
            return test_conn.bind()
        except Exception:
            return False


async def get_ldap_client(connector) -> LDAPClient | None:
    creds = getattr(connector, "credentials", None) or {}
    host = creds.get("host") or creds.get("hostname") or creds.get("server")
    if not host:
        return None
    raw_ssl = creds.get("use_ssl", False)
    use_ssl = raw_ssl if isinstance(raw_ssl, bool) else str(raw_ssl).lower() not in ("false", "0", "no", "")
    port = int(creds.get("port", 389))

    # Route the raw LDAP TCP connection through the agent tunnel when configured.
    ep_host, ep_port = await tcp_endpoint(connector, host, port)
    tls = None
    if (ep_host, ep_port) != (host, port):
        host, port = ep_host, ep_port
        # A routed LDAPS connection targets a localhost forwarder, so the real
        # server cert host cannot be verified — same limitation as winrm and the
        # postgres subprocess path. Honor skip-verify by disabling validation.
        if use_ssl and getattr(connector, "network_tls_skip_verify", False):
            tls = Tls(validate=ssl.CERT_NONE)

    return LDAPClient(
        host=host,
        port=port,
        bind_dn=creds.get("bind_dn", ""),
        bind_password=creds.get("bind_password") or creds.get("password", ""),
        base_dn=creds.get("base_dn", "dc=example,dc=com"),
        use_ssl=use_ssl,
        tls=tls,
    )
