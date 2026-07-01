# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
"""OpenVAS / Greenbone Community Edition REST API client (GSA on port 9392)."""
from typing import Optional
import httpx


class OpenVASClient:
    """
    Client for the Greenbone Security Assistant (GSA) REST API.
    Uses session-cookie auth: POST /login returns a Set-Cookie header.
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

    def authenticate(self) -> str:
        """POST /login, store session token, return token string."""
        resp = httpx.post(
            f"{self.base_url}/api/v1/authentication",
            json={"username": self.username, "password": self.password},
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data.get("token") or data.get("data", {}).get("token", "")
        if not self._token:
            # Try cookie-based flow (older GSA versions)
            cookie = resp.cookies.get("session")
            if cookie:
                self._token = cookie
        return self._token or ""

    def _headers(self) -> dict:
        if not self._token:
            self.authenticate()
        return {
            "Authorization": f"Bearer {self._token}",
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
        return resp.json()

    def _delete(self, path: str) -> dict:
        resp = httpx.delete(
            f"{self.base_url}{path}",
            headers=self._headers(),
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # ------------------------------------------------------------------
    # Targets
    # ------------------------------------------------------------------

    def create_target(self, name: str, hosts: str) -> dict:
        """Create a scan target. hosts is a comma-separated IP/CIDR string."""
        return self._post("/api/v1/targets", {
            "name": name,
            "hosts": hosts,
            "port_range": "default",
        })

    def delete_target(self, target_id: str) -> dict:
        return self._delete(f"/api/v1/targets/{target_id}")

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def create_task(self, name: str, target_id: str, config_id: str) -> dict:
        """Create a scan task. config_id selects the scan policy."""
        return self._post("/api/v1/tasks", {
            "name": name,
            "target": {"id": target_id},
            "config": {"id": config_id},
        })

    def start_task(self, task_id: str) -> dict:
        """Start a task, returns report_id in response."""
        return self._post(f"/api/v1/tasks/{task_id}/start")

    def get_task_status(self, task_id: str) -> dict:
        """Return task detail including status and last_report."""
        return self._get(f"/api/v1/tasks/{task_id}")

    def delete_task(self, task_id: str) -> dict:
        return self._delete(f"/api/v1/tasks/{task_id}")

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    def get_report(self, report_id: str) -> dict:
        """Fetch report detail including results list."""
        return self._get(f"/api/v1/reports/{report_id}")

    # ------------------------------------------------------------------
    # Scan configs (policies)
    # ------------------------------------------------------------------

    def list_scan_configs(self) -> list:
        data = self._get("/api/v1/scanconfigs")
        return data.get("scanconfigs", data.get("data", []))
