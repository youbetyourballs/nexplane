from __future__ import annotations
"""OPNsense REST API client — authenticates with API key + secret (HTTP Basic)."""
import json
from typing import Optional

try:
    import httpx as _httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False


class OPNsenseClient:
    def __init__(self, base_url: str, api_key: str, api_secret: str, verify_ssl: bool = False):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.verify_ssl = verify_ssl

    def _request(self, method: str, path: str, data: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{path}"
        auth = (self.api_key, self.api_secret)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        if _HAS_HTTPX:
            with _httpx.Client(verify=self.verify_ssl, timeout=30) as c:
                resp = c.request(method, url, auth=auth, headers=headers,
                                 content=json.dumps(data) if data is not None else None)
            resp.raise_for_status()
            return resp.json()
        elif _HAS_REQUESTS:
            resp = _requests.request(method, url, auth=auth, headers=headers,
                                     json=data, verify=self.verify_ssl, timeout=30)
            resp.raise_for_status()
            return resp.json()
        else:
            raise RuntimeError("httpx or requests package required — pip install httpx")

    def get(self, path: str) -> dict:
        return self._request("GET", path)

    def post(self, path: str, data: Optional[dict] = None) -> dict:
        return self._request("POST", path, data or {})

    def add_alias(self, name: str, description: str, addresses: list) -> dict:
        """Create a host alias (type=host) containing one or more IPs."""
        payload = {
            "alias": {
                "name": name,
                "type": "host",
                "description": description,
                "content": "\n".join(addresses),
                "enabled": "1",
            }
        }
        result = self.post("/api/firewall/alias/addItem", payload)
        self.post("/api/firewall/alias/reconfigure")
        return result

    def delete_alias(self, uuid: str) -> dict:
        result = self.post(f"/api/firewall/alias/delItem/{uuid}")
        self.post("/api/firewall/alias/reconfigure")
        return result

    def add_filter_rule(self, interface: str, action: str, protocol: str,
                        source: str, destination: str, description: str,
                        destination_port: str = "any") -> dict:
        """Add a firewall filter rule. action: 'block' or 'pass'."""
        payload = {
            "rule": {
                "enabled": "1",
                "action": action,
                "interface": interface,
                "ipprotocol": "inet",
                "protocol": protocol,
                "source_net": source,
                "destination_net": destination,
                "destination_port": destination_port,
                "description": description,
            }
        }
        result = self.post("/api/firewall/filter/addRule", payload)
        self.post("/api/firewall/filter/apply")
        return result

    def delete_filter_rule(self, uuid: str) -> dict:
        result = self.post(f"/api/firewall/filter/delRule/{uuid}")
        self.post("/api/firewall/filter/apply")
        return result

    def apply_filter(self) -> dict:
        return self.post("/api/firewall/filter/apply")


def get_opnsense_client(connector) -> Optional[OPNsenseClient]:
    creds = getattr(connector, "credentials", None) or {}
    base_url = creds.get("base_url") or creds.get("url")
    api_key = creds.get("api_key") or creds.get("key")
    api_secret = creds.get("api_secret") or creds.get("secret")
    if not (base_url and api_key and api_secret):
        return None
    verify = creds.get("verify_ssl", False)
    if isinstance(verify, str):
        verify = verify.lower() == "true"
    return OPNsenseClient(base_url, api_key, api_secret, verify_ssl=verify)
