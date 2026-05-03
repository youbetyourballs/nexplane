"""GitHub Fine-Grained Personal Access Token rotation connector action."""

import httpx
from typing import Any


async def create_github_pat(
    connector_config: Any,
    pat_name: str,
    permissions: list[str],
) -> dict:
    """
    Creates a new fine-grained PAT via the GitHub REST API.
    Returns {"new_api_key": "<token>", "new_pat_id": "<id>"}.

    Note: GitHub fine-grained PATs require a GitHub App installation or a
    user PAT with 'admin:org' scope to manage other tokens. The connector_config
    must supply a token with sufficient scope.
    """
    headers = {
        "Authorization": f"Bearer {connector_config.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.github.com/user/personal-access-tokens",
            headers=headers,
            json={
                "name": pat_name,
                "description": f"Rotated by Nexplane on behalf of {pat_name}",
                "permissions": {p: "write" for p in permissions},
            },
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "new_api_key": data["token"],
        "new_pat_id": str(data["id"]),
    }


async def delete_github_pat(connector_config: Any, pat_id: str) -> None:
    """Deletes a fine-grained PAT by its numeric ID."""
    headers = {
        "Authorization": f"Bearer {connector_config.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            f"https://api.github.com/user/personal-access-tokens/{pat_id}",
            headers=headers,
        )
        resp.raise_for_status()
