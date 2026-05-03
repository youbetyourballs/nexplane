import uuid
import pytest


@pytest.mark.asyncio
async def test_create_access_review(auth_client):
    resp = await auth_client.post(
        "/api/access-reviews",
        json={"title": "Q2 Review", "scope": {"connector_ids": None, "groups": None, "user_emails": None}},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "Q2 Review"
    assert data["status"] == "collecting"
    assert "id" in data


@pytest.mark.asyncio
async def test_list_access_reviews(auth_client):
    await auth_client.post(
        "/api/access-reviews",
        json={"title": "List Test", "scope": {}},
    )
    resp = await auth_client.get("/api/access-reviews")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_access_review_not_found(auth_client):
    resp = await auth_client.get(f"/api/access-reviews/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_submit_decisions(auth_client):
    create_resp = await auth_client.post(
        "/api/access-reviews",
        json={"title": "Decision Test", "scope": {}},
    )
    review_id = create_resp.json()["id"]
    entry_id = str(uuid.uuid4())

    resp = await auth_client.post(
        f"/api/access-reviews/{review_id}/decisions",
        json={"decisions": {entry_id: {"decision": "keep", "note": ""}}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["decisions"][entry_id]["decision"] == "keep"


@pytest.mark.asyncio
async def test_approve_requires_all_entries_decided(auth_client):
    """Approve should succeed if all snapshot entries have decisions (or snapshot is empty)."""
    create_resp = await auth_client.post(
        "/api/access-reviews",
        json={"title": "Approve Test", "scope": {}},
    )
    review_id = create_resp.json()["id"]

    # Approve with empty snapshot (no undecided entries)
    resp = await auth_client.post(f"/api/access-reviews/{review_id}/approve")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"


@pytest.mark.asyncio
async def test_get_review_changes(auth_client):
    create_resp = await auth_client.post(
        "/api/access-reviews",
        json={"title": "Changes Test", "scope": {}},
    )
    review_id = create_resp.json()["id"]
    resp = await auth_client.get(f"/api/access-reviews/{review_id}/changes")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
