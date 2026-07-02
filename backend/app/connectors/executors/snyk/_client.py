# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

from __future__ import annotations
import httpx

_BASE = "https://api.snyk.io"
_API_VERSION = "2023-05-29"


class SnykClient:
    def __init__(self, api_token: str, org_id: str) -> None:
        self._org_id = org_id
        self._http = httpx.AsyncClient(
            base_url=_BASE,
            headers={
                "Authorization": f"token {api_token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def __aenter__(self) -> "SnykClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._http.aclose()

    async def list_projects(self, org_id: str) -> list:
        resp = await self._http.get(
            f"/rest/orgs/{org_id}/projects",
            params={"version": _API_VERSION},
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def get_issues(self, org_id: str, project_id: str) -> list:
        resp = await self._http.get(
            f"/rest/orgs/{org_id}/issues",
            params={"version": _API_VERSION, "project_id": project_id, "limit": 100},
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    async def test_package(self, package_manager: str, package_name: str, version: str) -> dict:
        resp = await self._http.post(
            f"/v1/test/{package_manager}",
            json={"encoding": "plain", "files": {"target": {"contents": f"{package_name}=={version}"}}},
        )
        resp.raise_for_status()
        return resp.json()

    async def test_container_image(self, image: str) -> dict:
        resp = await self._http.post(
            "/v1/test/docker",
            json={"image": image},
        )
        resp.raise_for_status()
        return resp.json()

    async def create_project(self, org_id: str, target: dict) -> dict:
        resp = await self._http.post(
            f"/rest/orgs/{org_id}/projects",
            params={"version": _API_VERSION},
            json={"data": {"type": "project", "attributes": target}},
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_project(self, org_id: str, project_id: str) -> None:
        resp = await self._http.delete(
            f"/rest/orgs/{org_id}/projects/{project_id}",
            params={"version": _API_VERSION},
        )
        resp.raise_for_status()


async def get_client(connector) -> httpx.AsyncClient:
    from app.connectors.executors.common.tunnel_http import tunnel_http_client
    creds = getattr(connector, "credentials", {}) or {}
    return await tunnel_http_client(
        connector,
        base_url="https://api.snyk.io/rest",
        headers={"Authorization": f"token {creds['api_token']}", "Content-Type": "application/json"},
        timeout=30.0,
    )
