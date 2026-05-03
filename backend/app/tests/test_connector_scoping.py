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
