# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Redis client wrapper using redis-py."""
from __future__ import annotations

from app.tunnel.routing import tcp_endpoint


class RedisClient:
    def __init__(self, host: str, port: int, password: str = "",
                 ssl: bool = False, ssl_check_hostname: bool = True,
                 ssl_cert_reqs=None):
        self.host = host
        self.port = port
        self.password = password
        self.ssl = ssl
        self.ssl_check_hostname = ssl_check_hostname
        self.ssl_cert_reqs = ssl_cert_reqs

    def _connect(self):
        import redis  # type: ignore
        kw: dict = dict(
            host=self.host, port=self.port,
            password=self.password or None,
            socket_timeout=10,
            socket_connect_timeout=10,
            decode_responses=True,
        )
        if self.ssl:
            kw["ssl"] = True
            kw["ssl_check_hostname"] = self.ssl_check_hostname
            if self.ssl_cert_reqs is not None:
                kw["ssl_cert_reqs"] = self.ssl_cert_reqs
        return redis.Redis(**kw)

    def get_requirepass(self) -> str:
        """Return current requirepass value (empty string if none set)."""
        r = self._connect()
        try:
            result = r.config_get("requirepass")
            return result.get("requirepass", "")
        finally:
            r.close()

    def set_requirepass(self, new_password: str) -> None:
        """Set a new requirepass via CONFIG SET."""
        r = self._connect()
        try:
            r.config_set("requirepass", new_password)
        finally:
            try:
                r.close()
            except Exception:
                pass  # Connection may be dropped after auth change

    def ping_with_password(self, password: str) -> bool:
        """Return True if we can PING Redis with the given password."""
        try:
            import redis  # type: ignore
            r = redis.Redis(
                host=self.host, port=self.port,
                password=password or None,
                socket_timeout=5,
                socket_connect_timeout=5,
                decode_responses=True,
            )
            result = r.ping()
            r.close()
            return bool(result)
        except Exception:
            return False


async def get_redis_client(connector) -> "RedisClient | None":
    creds = getattr(connector, "credentials", None) or {}
    host = creds.get("host") or creds.get("hostname")
    if not host:
        return None
    port = int(creds.get("port", 6379))
    password = creds.get("password") or creds.get("auth", "")
    has_ssl = bool(creds.get("ssl"))

    ep_host, ep_port = await tcp_endpoint(connector, host, port)

    ssl_check_hostname = True
    ssl_cert_reqs = None

    if (ep_host, ep_port) != (host, port):
        # Routed via the in-process forwarder
        host = ep_host
        port = ep_port
        if has_ssl and getattr(connector, "network_tls_skip_verify", False):
            ssl_check_hostname = False
            ssl_cert_reqs = "none"  # string "none" disables cert validation in redis-py

    return RedisClient(
        host=host,
        port=port,
        password=password,
        ssl=has_ssl,
        ssl_check_hostname=ssl_check_hostname,
        ssl_cert_reqs=ssl_cert_reqs,
    )
