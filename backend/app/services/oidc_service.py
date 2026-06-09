import urllib.parse
import httpx
from dataclasses import dataclass, field
from typing import Any


@dataclass
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    scopes: list[str] = field(default_factory=lambda: ["openid", "email", "profile"])
    auto_provision: bool = False
    icon: str | None = None


async def _get_oidc_metadata(issuer: str) -> dict[str, Any]:
    """Fetch .well-known/openid-configuration."""
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


async def _fetch_token(
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _fetch_userinfo(userinfo_endpoint: str, access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


def build_authorization_url(
    config: OidcConfig,
    redirect_uri: str,
    state: str,
    authorization_endpoint: str | None = None,
) -> str:
    if authorization_endpoint is None:
        authorization_endpoint = config.issuer.rstrip("/") + "/authorize"

    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": redirect_uri,
        "scope": " ".join(config.scopes),
        "state": state,
    }
    return f"{authorization_endpoint}?{urllib.parse.urlencode(params)}"


async def exchange_code_for_userinfo(
    config: OidcConfig,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    metadata = await _get_oidc_metadata(config.issuer)
    token_response = await _fetch_token(
        token_endpoint=metadata["token_endpoint"],
        client_id=config.client_id,
        client_secret=config.client_secret,
        code=code,
        redirect_uri=redirect_uri,
    )
    userinfo = await _fetch_userinfo(
        userinfo_endpoint=metadata["userinfo_endpoint"],
        access_token=token_response["access_token"],
    )
    return userinfo
