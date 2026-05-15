import httpx
from typing import Optional, List, Dict, Any


def get_rest_client(creds: dict) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=creds["base_url"].rstrip("/"),
        headers={"Authorization": f"Bearer {creds['token']}"},
        timeout=60.0,
        verify=False,
    )


def get_hec_client(creds: dict) -> httpx.AsyncClient:
    hec_url = creds.get("hec_url", creds["base_url"].replace(":8089", ":8088"))
    return httpx.AsyncClient(
        base_url=hec_url,
        headers={"Authorization": f"Splunk {creds['token']}"},
        timeout=30.0,
        verify=False,
    )


class SplunkClient:
    """Sync HTTP client for Splunk REST API using session key auth (port 8089)."""

    def __init__(self, base_url: str, username: str, password: str,
                 verify_ssl: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self._session_key: Optional[str] = None
        self._http = httpx.Client(verify=self.verify_ssl, timeout=60.0)

    def _get_session_key(self) -> str:
        if self._session_key:
            return self._session_key
        resp = self._http.post(
            f"{self.base_url}/services/auth/login",
            data={"username": self.username, "password": self.password,
                  "output_mode": "json"},
        )
        resp.raise_for_status()
        self._session_key = resp.json()["sessionKey"]
        return self._session_key

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Splunk {self._get_session_key()}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def search(self, spl_query: str, earliest: str = "-24h",
               latest: str = "now") -> List[Dict[str, Any]]:
        """Run a blocking SPL search and return result rows."""
        import json as _json
        resp = self._http.post(
            f"{self.base_url}/services/search/jobs/export",
            headers=self._headers(),
            data={
                "search": spl_query if spl_query.startswith("search ") else f"search {spl_query}",
                "earliest_time": earliest,
                "latest_time": latest,
                "output_mode": "json",
            },
        )
        resp.raise_for_status()
        results: List[Dict[str, Any]] = []
        for line in resp.text.strip().splitlines():
            try:
                obj = _json.loads(line)
                if "result" in obj:
                    results.append(obj["result"])
            except Exception:
                pass
        return results

    def get_notable_events(self) -> List[Dict[str, Any]]:
        """Return active ES notable events (or general events if no ES)."""
        try:
            return self.search("| inputlookup notable_xref | search status!=5 | head 100")
        except Exception:
            return self.search("index=* | head 50", earliest="-1h")

    def update_notable_status(self, event_id: str, status: str) -> Dict[str, Any]:
        """Update notable event status."""
        status_map = {"open": "0", "assigned": "1", "resolved": "2",
                      "closed": "3", "deferred": "4", "suppressed": "5"}
        status_code = status_map.get(status.lower(), status)
        resp = self._http.post(
            f"{self.base_url}/services/notable_update",
            headers=self._headers(),
            data={"ruleUIDs": event_id, "status": status_code, "output_mode": "json"},
        )
        return {"event_id": event_id, "status": status, "http_status": resp.status_code}

    def create_saved_search(self, name: str, query: str,
                             cron_schedule: Optional[str] = None) -> Dict[str, Any]:
        """Create a saved search in Splunk."""
        data: Dict[str, Any] = {"name": name, "search": query, "output_mode": "json"}
        if cron_schedule:
            data["cron_schedule"] = cron_schedule
            data["is_scheduled"] = "1"
            data["schedule_window"] = "0"
        resp = self._http.post(
            f"{self.base_url}/services/saved/searches",
            headers=self._headers(),
            data=data,
        )
        resp.raise_for_status()
        return {"name": name, "created": True}

    def delete_saved_search(self, name: str) -> Dict[str, Any]:
        """Delete a saved search by name."""
        import urllib.parse as _up
        encoded = _up.quote(name, safe="")
        resp = self._http.delete(
            f"{self.base_url}/services/saved/searches/{encoded}",
            headers=self._headers(),
            params={"output_mode": "json"},
        )
        if resp.status_code == 404:
            return {"name": name, "deleted": False, "reason": "not found"}
        resp.raise_for_status()
        return {"name": name, "deleted": True}

    def close(self) -> None:
        self._http.close()


def get_splunk_client(connector) -> Optional["SplunkClient"]:
    """Build a SplunkClient from connector credentials. Returns None if missing creds."""
    creds = getattr(connector, "credentials", None) or {}
    base_url = creds.get("base_url", "")
    username = creds.get("username", "admin")
    password = creds.get("password", "")
    if not base_url or not password:
        return None
    return SplunkClient(
        base_url=base_url,
        username=username,
        password=password,
        verify_ssl=creds.get("verify_ssl", False),
    )
