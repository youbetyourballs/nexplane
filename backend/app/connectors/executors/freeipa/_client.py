from __future__ import annotations
import httpx


class FreeIPAClient:
    """FreeIPA JSON-RPC client.  Authenticates with username/password to get
    a session cookie, then calls JSON-RPC endpoints at /ipa/session/json."""

    def __init__(self, url, username, password, verify_ssl=False):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self._session_cookie = None

    async def _login(self):
        async with httpx.AsyncClient(verify=self.verify_ssl) as c:
            resp = await c.post(
                f"{self.url}/ipa/session/login_password",
                data={"user": self.username, "password": self.password},
                headers={"Content-Type": "application/x-www-form-urlencoded",
                         "Accept": "text/plain"},
            )
            resp.raise_for_status()
            self._session_cookie = resp.headers.get("Set-Cookie", "")

    async def _call(self, method, args=None, params=None):
        if not self._session_cookie:
            await self._login()
        payload = {
            "id": 0,
            "method": method,
            "params": [args or [], params or {}],
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Cookie": self._session_cookie,
            "Referer": f"{self.url}/ipa",
        }
        async with httpx.AsyncClient(verify=self.verify_ssl) as c:
            resp = await c.post(f"{self.url}/ipa/session/json",
                                json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            error = data.get("error")
            if error:
                raise RuntimeError(f"FreeIPA error {error.get('code')}: {error.get('message')}")
            return data.get("result", {})

    async def user_show(self, username):
        result = await self._call("user_show", [username], {"all": True})
        return result.get("result", {})

    async def user_disable(self, username):
        await self._call("user_disable", [username])
        return {"success": True, "username": username, "action": "disabled"}

    async def user_enable(self, username):
        await self._call("user_enable", [username])
        return {"success": True, "username": username, "action": "enabled"}


def get_freeipa_client(connector):
    creds = getattr(connector, "credentials", None) or {}
    url = creds.get("url") or creds.get("base_url")
    username = creds.get("username")
    password = creds.get("password")
    if not url or not username or not password:
        return None
    return FreeIPAClient(
        url=url,
        username=username,
        password=password,
        verify_ssl=creds.get("verify_ssl", False),
    )
