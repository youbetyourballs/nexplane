import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity_provider import IdentityProvider, IdpType, IdpStatus


@pytest_asyncio.fixture
async def idp_factory(db: AsyncSession, test_org):
    """Factory: create an IdentityProvider. Returns the created record."""
    async def _make(name: str = "Test OIDC", enabled: bool = False) -> IdentityProvider:
        idp = IdentityProvider(
            id=uuid.uuid4(),
            org_id=test_org.id,
            type=IdpType.oidc,
            name=name,
            status=IdpStatus.pending,
            enabled=enabled,
            config={
                "issuer": "https://accounts.example.com",
                "client_id": "abc",
                "client_secret": "secret",
                "scopes": ["openid", "email", "profile"],
            },
        )
        db.add(idp)
        await db.flush()
        return idp
    return _make


@pytest.mark.asyncio
async def test_identity_providers_table_exists(db: AsyncSession):
    result = await db.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name='identity_providers'")
    )
    cols = {r[0] for r in result.fetchall()}
    assert {"id", "org_id", "type", "name", "status", "enabled", "config", "connector_id"} <= cols


@pytest.mark.asyncio
async def test_org_has_auth_mode_column(db: AsyncSession):
    result = await db.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name='organizations' AND column_name='auth_mode'")
    )
    assert result.fetchone() is not None


@pytest.mark.asyncio
async def test_identity_provider_model_roundtrip(db: AsyncSession, test_org):
    idp = IdentityProvider(
        id=uuid.uuid4(),
        org_id=test_org.id,
        type=IdpType.oidc,
        name="Test OIDC",
        status=IdpStatus.pending,
        enabled=False,
        config={"issuer": "https://accounts.example.com", "client_id": "abc", "client_secret": "secret"},
    )
    db.add(idp)
    await db.flush()
    assert idp.connector_id is None
    assert idp.config["issuer"] == "https://accounts.example.com"


@pytest.mark.asyncio
async def test_create_identity_provider(auth_client: AsyncClient):
    resp = await auth_client.post("/identity-providers", json={
        "type": "oidc",
        "name": "Google OIDC",
        "config": {
            "issuer": "https://accounts.google.com",
            "client_id": "abc.apps.googleusercontent.com",
            "client_secret": "secret",
            "scopes": ["openid", "email", "profile"],
        },
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["type"] == "oidc"
    assert data["status"] == "pending"
    assert data["enabled"] is False


@pytest.mark.asyncio
async def test_list_identity_providers(auth_client: AsyncClient, idp_factory):
    await idp_factory(name="IDP1")
    await idp_factory(name="IDP2")
    resp = await auth_client.get("/identity-providers")
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


@pytest.mark.asyncio
async def test_get_identity_provider_by_id(auth_client: AsyncClient, idp_factory):
    idp = await idp_factory(name="MyIDP")
    resp = await auth_client.get(f"/identity-providers/{idp.id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "MyIDP"


@pytest.mark.asyncio
async def test_update_identity_provider(auth_client: AsyncClient, idp_factory):
    idp = await idp_factory(name="OldName")
    resp = await auth_client.put(f"/identity-providers/{idp.id}", json={"name": "NewName"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "NewName"


@pytest.mark.asyncio
async def test_delete_identity_provider(auth_client: AsyncClient, idp_factory):
    idp = await idp_factory(name="ToDelete")
    resp = await auth_client.delete(f"/identity-providers/{idp.id}")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_active_idps_no_auth_required(client: AsyncClient):
    resp = await client.get("/identity-providers/active")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_switch_to_idp_mode_atomic(auth_client: AsyncClient, idp_factory, test_org):
    idp = await idp_factory(name="GoogleOIDC", enabled=True)
    resp = await auth_client.post(
        f"/orgs/{test_org.id}/auth-mode",
        json={"auth_mode": "idp", "idp_id": str(idp.id)},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["auth_mode"] == "idp"
    idp_resp = await auth_client.get(f"/identity-providers/{idp.id}")
    assert idp_resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_switch_back_to_local(auth_client: AsyncClient, idp_factory, test_org):
    idp = await idp_factory(name="G", enabled=True)
    await auth_client.post(f"/orgs/{test_org.id}/auth-mode", json={"auth_mode": "idp", "idp_id": str(idp.id)})
    resp = await auth_client.post(f"/orgs/{test_org.id}/auth-mode", json={"auth_mode": "local"})
    assert resp.status_code == 200
    assert resp.json()["auth_mode"] == "local"
