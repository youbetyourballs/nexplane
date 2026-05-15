"""LDAP client wrapper using ldap3."""
from __future__ import annotations
from ldap3 import Server, Connection, ALL, MODIFY_REPLACE, SUBTREE
from ldap3.core.exceptions import LDAPException


class LDAPClient:
    def __init__(self, host: str, port: int, bind_dn: str, bind_password: str,
                 base_dn: str, use_ssl: bool = False):
        self.host = host
        self.port = port
        self.bind_dn = bind_dn
        self.bind_password = bind_password
        self.base_dn = base_dn
        self.use_ssl = use_ssl

    def _connect(self) -> Connection:
        server = Server(self.host, port=self.port, use_ssl=self.use_ssl, get_info=ALL)
        conn = Connection(server, user=self.bind_dn, password=self.bind_password, auto_bind=True)
        return conn

    def disable_user(self, username: str) -> dict:
        """Disable a user by setting pwdAccountLockedTime or loginShell."""
        conn = self._connect()
        try:
            # Search for the user
            conn.search(self.base_dn, f"(uid={username})", SUBTREE, attributes=["dn", "uid"])
            if not conn.entries:
                # Try sAMAccountName for Active Directory
                conn.search(self.base_dn, f"(sAMAccountName={username})", SUBTREE, attributes=["dn"])
            if not conn.entries:
                return {"success": False, "error": f"User {username} not found"}
            user_dn = conn.entries[0].entry_dn
            # For OpenLDAP with ppolicy overlay: set pwdAccountLockedTime to epoch (locked)
            result = conn.modify(user_dn, {"pwdAccountLockedTime": [(MODIFY_REPLACE, ["000001010000Z"])]})
            if not result:
                # Fallback: set loginShell to nologin
                conn.modify(user_dn, {"loginShell": [(MODIFY_REPLACE, ["/sbin/nologin"])]})
            return {"success": True, "user_dn": user_dn, "username": username, "action": "disabled"}
        finally:
            conn.unbind()

    def enable_user(self, username: str) -> dict:
        """Re-enable a user by clearing the lock."""
        conn = self._connect()
        try:
            conn.search(self.base_dn, f"(|(uid={username})(sAMAccountName={username}))",
                        SUBTREE, attributes=["dn"])
            if not conn.entries:
                return {"success": False, "error": f"User {username} not found"}
            user_dn = conn.entries[0].entry_dn
            conn.modify(user_dn, {"pwdAccountLockedTime": [(MODIFY_REPLACE, [])]})
            return {"success": True, "user_dn": user_dn, "username": username, "action": "enabled"}
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
                        SUBTREE, attributes=["dn"])
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
            server = Server(self.host, port=self.port, use_ssl=self.use_ssl)
            admin_conn = self._connect()
            admin_conn.search(self.base_dn, f"(|(uid={username})(sAMAccountName={username}))",
                              SUBTREE, attributes=["dn"])
            if not admin_conn.entries:
                return False
            user_dn = admin_conn.entries[0].entry_dn
            admin_conn.unbind()
            test_conn = Connection(server, user=user_dn, password=password)
            return test_conn.bind()
        except Exception:
            return False


def get_ldap_client(connector) -> LDAPClient | None:
    creds = getattr(connector, "credentials", None) or {}
    host = creds.get("host") or creds.get("hostname")
    if not host:
        return None
    return LDAPClient(
        host=host,
        port=int(creds.get("port", 389)),
        bind_dn=creds.get("bind_dn", ""),
        bind_password=creds.get("bind_password") or creds.get("password", ""),
        base_dn=creds.get("base_dn", "dc=example,dc=com"),
        use_ssl=creds.get("use_ssl", False),
    )
