# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""PostgreSQL client wrapper.

Uses psycopg2 if available; falls back to subprocess psql if not installed.
Credentials come from connector.credentials dict with keys:
  host, port (default 5432), dbname (default postgres),
  user (admin user), password

Routing: for via_agent connectors the connection is tunneled through an
in-process localhost forwarder (see app.tunnel.routing.tcp_endpoint). We connect
to the forwarder via ``hostaddr`` while keeping the REAL hostname in ``host`` so
that TLS SNI and ``sslmode=verify-full`` still validate the real server cert.
"""
from __future__ import annotations

from app.tunnel.routing import tcp_endpoint


class PostgresClient:
    def __init__(
        self,
        host: str,
        port: int,
        dbname: str,
        user: str,
        password: str,
        hostaddr: str | None = None,
        sslmode: str | None = None,
    ):
        self.host = host
        self.port = port
        self.dbname = dbname
        self.user = user
        self.password = password
        # When set, the actual TCP target (the localhost forwarder); ``host``
        # stays the real hostname for SNI / cert verification.
        self.hostaddr = hostaddr
        self.sslmode = sslmode

    def _connect_kwargs(self, *, user: str, password: str, connect_timeout: int) -> dict:
        kw = {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": user,
            "password": password,
            "connect_timeout": connect_timeout,
        }
        if self.hostaddr is not None:
            kw["hostaddr"] = self.hostaddr
        if self.sslmode is not None:
            kw["sslmode"] = self.sslmode
        return kw

    def alter_user_password(self, username: str, new_password: str) -> None:
        """Execute ALTER USER {username} PASSWORD '{new_password}' as admin."""
        try:
            import psycopg2  # type: ignore
            conn = psycopg2.connect(
                **self._connect_kwargs(
                    user=self.user, password=self.password, connect_timeout=10
                )
            )
            conn.autocommit = True
            try:
                with conn.cursor() as cur:
                    # Use parameterized identifier quoting for username safety
                    cur.execute(
                        f"ALTER USER {psycopg2.extensions.quote_ident(username, conn)}"
                        f" PASSWORD %s",
                        (new_password,),
                    )
            finally:
                conn.close()
        except ImportError:
            self._alter_via_subprocess(username, new_password)

    def _alter_via_subprocess(self, username: str, new_password: str) -> None:
        """Fallback: run psql via subprocess."""
        import subprocess, shlex, os
        # Escape the password for single-quoted SQL string
        escaped = new_password.replace("'", "''")
        sql = f"ALTER USER \"{username}\" PASSWORD '{escaped}';"
        env = {**os.environ, "PGPASSWORD": self.password}
        # psql has no "hostaddr" flag; when routed, connect to the forwarder
        # address directly (cert verification via psql is not used on this path).
        connect_host = self.hostaddr or self.host
        result = subprocess.run(
            ["psql", "-h", connect_host, "-p", str(self.port),
             "-U", self.user, "-d", self.dbname, "-c", sql],
            env=env, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            raise RuntimeError(f"psql ALTER USER failed: {result.stderr.strip()}")

    def verify_login(self, username: str, password: str) -> bool:
        """Return True if the given username+password can authenticate."""
        try:
            import psycopg2  # type: ignore
            conn = psycopg2.connect(
                **self._connect_kwargs(
                    user=username, password=password, connect_timeout=5
                )
            )
            conn.close()
            return True
        except Exception:
            return False


async def get_postgres_client(connector) -> "PostgresClient | None":
    creds = getattr(connector, "credentials", None) or {}
    host = creds.get("host") or creds.get("hostname")
    if not host:
        return None
    port = int(creds.get("port", 5432))
    sslmode = creds.get("sslmode")

    # Resolve the routing endpoint asynchronously so the app event loop stays
    # free to service the in-process forwarder. Executors then run the blocking
    # psycopg2 calls off the loop via asyncio.to_thread (avoiding a deadlock
    # where a blocking connect starves the forwarder's accept coroutine).
    ep_host, ep_port = await tcp_endpoint(connector, host, port)

    hostaddr: str | None = None
    if (ep_host, ep_port) != (host, port):
        # Routed via the forwarder: connect to it (hostaddr) but keep the real
        # hostname (host) for SNI / cert verification.
        hostaddr = ep_host
        port = ep_port
        if getattr(connector, "network_tls_skip_verify", False) and (
            sslmode or ""
        ).startswith("verify"):
            # The real cert can't be validated when tunneled to a localhost
            # forwarder; downgrade to an encrypted-but-unverified mode.
            sslmode = "require"

    return PostgresClient(
        host=host,
        port=port,
        dbname=creds.get("dbname") or creds.get("database", "postgres"),
        user=creds.get("user") or creds.get("username", "postgres"),
        password=creds.get("password", ""),
        hostaddr=hostaddr,
        sslmode=sslmode,
    )
