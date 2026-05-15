from __future__ import annotations
"""Nessus Essentials REST API client (port 8834)."""
from typing import Optional
import httpx


class NessusClient:
    """
    Nessus REST API client.
    Auth: POST /session returns X-Cookie token; pass as X-Cookie header on all requests.
    """

    def __init__(self, base_url: str, username: str, password: str,
                 verify_ssl: bool = False, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._token: Optional[str] = None

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def login(self) -> str:
        """POST /session, store token, return token string."""
        resp = httpx.post(
            f"{self.base_url}/session",
            json={"username": self.username, "password": self.password},
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        self._token = resp.json().get("token", "")
        return self._token

    def _headers(self) -> dict:
        if not self._token:
            self.login()
        return {
            "X-Cookie": f"token={self._token}",
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        resp = httpx.get(
            f"{self.base_url}{path}",
            headers=self._headers(),
            params=params,
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, json: Optional[dict] = None) -> dict:
        resp = httpx.post(
            f"{self.base_url}{path}",
            headers=self._headers(),
            json=json or {},
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _delete(self, path: str) -> None:
        resp = httpx.delete(
            f"{self.base_url}{path}",
            headers=self._headers(),
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        resp.raise_for_status()

    # ------------------------------------------------------------------
    # Scans
    # ------------------------------------------------------------------

    def create_scan(self, name: str, targets: str, policy_id: Optional[int] = None) -> dict:
        """Create a new scan. targets is a comma-separated IP/CIDR string."""
        settings: dict = {
            "name": name,
            "text_targets": targets,
            "enabled": False,
        }
        if policy_id is not None:
            settings["policy_id"] = policy_id

        payload: dict = {"uuid": "ab4bacd2-05d6-44c3-9084-1626a6b2102e", "settings": settings}
        return self._post("/scans", payload)

    def launch_scan(self, scan_id: int) -> dict:
        """Launch a scan, returns scan_uuid."""
        return self._post(f"/scans/{scan_id}/launch")

    def get_scan_status(self, scan_id: int) -> dict:
        """Return scan detail including status field."""
        return self._get(f"/scans/{scan_id}")

    def get_scan_results(self, scan_id: int) -> dict:
        """Return full scan detail including hosts/vulnerabilities."""
        return self._get(f"/scans/{scan_id}")

    def export_scan(self, scan_id: int, fmt: str = "nessus") -> dict:
        """Request an export, return token for download."""
        return self._post(f"/scans/{scan_id}/export", {"format": fmt})

    def delete_scan(self, scan_id: int) -> None:
        """Delete a scan permanently."""
        self._delete(f"/scans/{scan_id}")

    # ------------------------------------------------------------------
    # Policies / templates
    # ------------------------------------------------------------------

    def list_policies(self) -> list:
        data = self._get("/policies")
        return data.get("policies") or []

    def list_scan_templates(self) -> list:
        data = self._get("/editor/scan/templates")
        return data.get("templates") or []
