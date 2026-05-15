"""Redis client wrapper using redis-py."""
from __future__ import annotations


class RedisClient:
    def __init__(self, host: str, port: int, password: str = ""):
        self.host = host
        self.port = port
        self.password = password

    def _connect(self):
        import redis  # type: ignore
        return redis.Redis(
            host=self.host, port=self.port,
            password=self.password or None,
            socket_timeout=10,
            socket_connect_timeout=10,
            decode_responses=True,
        )

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


def get_redis_client(connector) -> "RedisClient | None":
    creds = getattr(connector, "credentials", None) or {}
    host = creds.get("host") or creds.get("hostname")
    if not host:
        return None
    return RedisClient(
        host=host,
        port=int(creds.get("port", 6379)),
        password=creds.get("password") or creds.get("auth", ""),
    )
