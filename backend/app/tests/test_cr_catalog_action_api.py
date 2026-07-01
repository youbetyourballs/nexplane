# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Run with --noconftest."""
import uuid
from pydantic import ValidationError

from app.models.change_request import ChangeType
from app.schemas.change_request import ChangeRequestCreate


def test_catalog_action_cr_omitting_target_asset_ids_is_valid():
    """Omitting target_asset_ids entirely must succeed and default to []."""
    cr = ChangeRequestCreate(
        title="Provision acme",
        change_type=ChangeType.catalog_action,
        desired_outcome={
            "connector_type": "commercial",
            "action_id": "provision_instance",
            "params": {"client_id": "acme"},
        },
    )
    assert cr.target_asset_ids == []


def test_catalog_action_cr_empty_list_is_valid():
    """Explicitly passing [] must also succeed."""
    cr = ChangeRequestCreate(
        title="Provision acme",
        change_type=ChangeType.catalog_action,
        target_asset_ids=[],
        desired_outcome={
            "connector_type": "commercial",
            "action_id": "provision_instance",
            "params": {"client_id": "acme"},
        },
    )
    assert cr.target_asset_ids == []


def test_other_change_type_still_accepts_assets():
    """Non-catalog_action change types still work with asset ids."""
    asset_id = uuid.uuid4()
    cr = ChangeRequestCreate(
        title="Some other CR",
        change_type=ChangeType.dns_update,
        target_asset_ids=[asset_id],
        desired_outcome={"action": "grant"},
    )
    assert cr.target_asset_ids == [asset_id]


def test_create_catalog_action_cr_endpoint():
    import uuid, types
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from app.routers.change_requests import router
    from app.routers import current_user
    from app.database import get_db

    added = []
    class _Result:
        def __init__(self, obj): self._obj = obj
        def scalar_one(self): return self._obj
        def scalar_one_or_none(self): return self._obj
    class _FakeSession:
        async def flush(self):
            for o in added:
                if getattr(o, "id", None) is None: o.id = uuid.uuid4()
        def add(self, o): added.append(o)
        async def commit(self): pass
        async def execute(self, *a, **k):
            crs = [o for o in added if type(o).__name__ == "ChangeRequest"]
            return _Result(crs[0] if crs else None)
    app = FastAPI(); app.include_router(router)
    user = types.SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: _FakeSession()
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/change-requests", json={"title": "Provision acme", "change_type": "catalog_action",
        "target_asset_ids": [],
        "desired_outcome": {"connector_type": "commercial", "action_id": "provision_instance", "params": {"client_id": "acme"}}})
    crs = [o for o in added if type(o).__name__ == "ChangeRequest"]
    assert crs, f"no ChangeRequest created (status {r.status_code}: {r.text})"
    assert crs[0].change_type.value == "catalog_action"
    assert crs[0].target_asset_ids == []
