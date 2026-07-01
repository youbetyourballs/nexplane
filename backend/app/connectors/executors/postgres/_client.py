# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""PostgreSQL client wrapper.

Uses psycopg2 if available; falls back to subprocess psql if not installed.
Credentials come from connector.credentials dict with keys:
  host, port (default 5432), dbname (default postgres),
  user (admin user), password
"""
from __future__ import annotations


class PostgresClient:
    def __init__(self, host: str, port: int, dbname: str, user: str, password: str):
        self.host = host
        self.port = port
        self.dbname = dbname
        self.user = user
        self.password = password

    def alter_user_password(self, username: str, new_password: str) -> None:
        """Execute ALTER USER {username} PASSWORD '{new_password}' as admin."""
        try:
            import psycopg2  # type: ignore
            conn = psycopg2.connect(
                host=self.host, port=self.port, dbname=self.dbname,
                user=self.user, password=self.password,
                connect_timeout=10,
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
        result = subprocess.run(
            ["psql", "-h", self.host, "-p", str(self.port),
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
                host=self.host, port=self.port, dbname=self.dbname,
                user=username, password=password,
                connect_timeout=5,
            )
            conn.close()
            return True
        except Exception:
            return False


def get_postgres_client(connector) -> "PostgresClient | None":
    creds = getattr(connector, "credentials", None) or {}
    host = creds.get("host") or creds.get("hostname")
    if not host:
        return None
    return PostgresClient(
        host=host,
        port=int(creds.get("port", 5432)),
        dbname=creds.get("dbname") or creds.get("database", "postgres"),
        user=creds.get("user") or creds.get("username", "postgres"),
        password=creds.get("password", ""),
    )
