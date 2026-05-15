from __future__ import annotations
import httpx


class JFrogClient:
    """Thin async client for JFrog Artifactory + Xray REST APIs."""

    def __init__(self, base_url: str, username: str, password_or_token: str) -> None:
        self._base = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            auth=(username, password_or_token),
            headers={"Content-Type": "application/json"},
            timeout=60.0,
            verify=False,  # free-tier certs may be self-signed
        )

    async def __aenter__(self) -> "JFrogClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self._http.aclose()

    # -------------------------------------------------------------------
    # Xray — scanning
    # -------------------------------------------------------------------

    async def scan_artifact(self, repo: str, path: str) -> dict:
        resp = await self._http.post(
            f"{self._base}/xray/api/v1/scanArtifact",
            json={"componentID": f"gav:///{repo}/{path}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_violations(self, filters: dict) -> list:
        resp = await self._http.post(
            f"{self._base}/xray/api/v1/violations",
            json=filters,
        )
        resp.raise_for_status()
        return resp.json().get("violations", [])

    async def get_artifact_summary(self, checksums: list) -> dict:
        resp = await self._http.post(
            f"{self._base}/xray/api/v1/summary/artifact",
            json={"checksums": checksums},
        )
        resp.raise_for_status()
        return resp.json()

    # -------------------------------------------------------------------
    # Xray — policies
    # -------------------------------------------------------------------

    async def create_policy(self, name: str, rules: list) -> dict:
        resp = await self._http.post(
            f"{self._base}/xray/api/v2/policies",
            json={"name": name, "type": "security", "rules": rules},
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_policy(self, name: str) -> None:
        resp = await self._http.delete(f"{self._base}/xray/api/v2/policies/{name}")
        resp.raise_for_status()

    # -------------------------------------------------------------------
    # Xray — watches
    # -------------------------------------------------------------------

    async def create_watch(self, name: str, repos: list) -> dict:
        resources = {
            "repositories": {
                r: {"name": r, "type": "repository", "filters": []}
                for r in repos
            }
        }
        resp = await self._http.post(
            f"{self._base}/xray/api/v2/watches",
            json={"general_data": {"name": name, "active": True}, "project_resources": resources},
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_watch(self, name: str) -> None:
        resp = await self._http.delete(f"{self._base}/xray/api/v2/watches/{name}")
        resp.raise_for_status()

    # -------------------------------------------------------------------
    # Artifactory — artifact upload/delete helpers
    # -------------------------------------------------------------------

    async def upload_artifact(self, repo: str, path: str, content: bytes) -> dict:
        resp = await self._http.put(
            f"{self._base}/artifactory/{repo}/{path}",
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_artifact(self, repo: str, path: str) -> None:
        resp = await self._http.delete(f"{self._base}/artifactory/{repo}/{path}")
        resp.raise_for_status()
