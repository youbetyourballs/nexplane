# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""MongoDB client wrapper using pymongo."""
from __future__ import annotations

from app.tunnel.routing import tcp_endpoint


class MongoClient:
    def __init__(self, uri: str, auth_db: str = "admin",
                 tls_allow_invalid_hostnames: bool = False):
        self.uri = uri
        self.auth_db = auth_db
        self.tls_allow_invalid_hostnames = tls_allow_invalid_hostnames

    def _connect(self):
        from pymongo import MongoClient as PyMongoClient  # type: ignore
        kw: dict = dict(serverSelectionTimeoutMS=10000)
        if self.tls_allow_invalid_hostnames:
            kw["tlsAllowInvalidHostnames"] = True
        return PyMongoClient(self.uri, **kw)

    def rotate_user_password(self, username: str, new_password: str,
                             db_name: str = "admin") -> None:
        """Call updateUser on the specified database to change the password."""
        client = self._connect()
        try:
            db = client[db_name]
            db.command("updateUser", username, pwd=new_password)
        finally:
            client.close()

    def verify_login(self, username: str, password: str,
                     db_name: str = "admin") -> bool:
        """Return True if the given credentials can authenticate."""
        try:
            from pymongo import MongoClient as PyMongoClient  # type: ignore
            host = self.uri.split("@")[-1].split("/")[0] if "@" in self.uri else self.uri
            # Build a clean URI with the given username/password
            test_uri = f"mongodb://{username}:{password}@{host}/{db_name}"
            c = PyMongoClient(test_uri, serverSelectionTimeoutMS=5000)
            c[db_name].command("ping")
            c.close()
            return True
        except Exception:
            return False


async def get_mongo_client(connector) -> "MongoClient | None":
    creds = getattr(connector, "credentials", None) or {}
    host = creds.get("host") or creds.get("hostname")
    if not host:
        return None
    port = int(creds.get("port", 27017))
    user = creds.get("user") or creds.get("username", "")
    password = creds.get("password", "")
    auth_db = creds.get("auth_db") or creds.get("authSource", "admin")
    has_tls = bool(creds.get("tls"))

    ep_host, ep_port = await tcp_endpoint(connector, host, port)

    tls_allow_invalid_hostnames = False

    if (ep_host, ep_port) != (host, port):
        # Routed via forwarder: rebuild URI with forwarded host:port
        host = ep_host
        port = ep_port
        if has_tls and getattr(connector, "network_tls_skip_verify", False):
            tls_allow_invalid_hostnames = True

    if user and password:
        uri = f"mongodb://{user}:{password}@{host}:{port}/{auth_db}"
    else:
        uri = f"mongodb://{host}:{port}/"

    return MongoClient(uri=uri, auth_db=auth_db,
                       tls_allow_invalid_hostnames=tls_allow_invalid_hostnames)
