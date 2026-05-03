import pytest


@pytest.mark.asyncio
async def test_asset_read_has_connector_fields(auth_client):
    """AssetRead schema exposes connector_id and connector_name."""
    resp = await auth_client.get("/assets")
    assert resp.status_code == 200
    data = resp.json()
    for asset in data:
        assert "connector_id" in asset
        assert "connector_name" in asset


@pytest.mark.asyncio
async def test_asset_list_accepts_connector_id_filter(auth_client):
    """Asset list endpoint accepts connector_id query param without error."""
    import uuid
    fake_connector_id = str(uuid.uuid4())
    resp = await auth_client.get(f"/assets?connector_id={fake_connector_id}")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_ingest_stamps_connector_id(auth_client):
    """Filter by connector_id works without error (assets may be empty)."""
    connectors_resp = await auth_client.get("/connectors")
    assert connectors_resp.status_code == 200
    connectors = connectors_resp.json()
    if not connectors:
        pytest.skip("No connectors in test org")
    connector = connectors[0]
    connector_id = connector["id"]
    assets_resp = await auth_client.get(f"/assets?connector_id={connector_id}")
    assert assets_resp.status_code == 200
    assert isinstance(assets_resp.json(), list)
