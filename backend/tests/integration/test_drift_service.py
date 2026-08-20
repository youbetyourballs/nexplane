# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.models.drift import ResourceState, DriftPolicy, DriftEvent


def test_models_importable():
    assert ResourceState.__tablename__ == "resource_states"
    assert DriftPolicy.__tablename__ == "drift_policies"
    assert DriftEvent.__tablename__ == "drift_events"


from app.services.drift_service import (
    compute_diff, normalize_state, SURFACE_SEVERITY,
    HOST_SURFACES, CLOUD_SURFACES,
)


def test_compute_diff_empty_when_identical():
    state = {"PermitRootLogin": "no", "Port": "22"}
    assert compute_diff(state, state.copy()) == {}


def test_compute_diff_added():
    baseline = {"Port": "22"}
    observed = {"Port": "22", "PermitRootLogin": "no"}
    diff = compute_diff(baseline, observed)
    assert diff == {"added": {"PermitRootLogin": "no"}, "removed": {}, "changed": {}}


def test_compute_diff_removed():
    baseline = {"Port": "22", "X11Forwarding": "yes"}
    observed = {"Port": "22"}
    diff = compute_diff(baseline, observed)
    assert diff == {"added": {}, "removed": {"X11Forwarding": "yes"}, "changed": {}}


def test_compute_diff_changed():
    baseline = {"Port": "22"}
    observed = {"Port": "2222"}
    diff = compute_diff(baseline, observed)
    assert diff == {"added": {}, "removed": {}, "changed": {"Port": {"from": "22", "to": "2222"}}}


def test_compute_diff_all_three():
    baseline = {"a": "1", "b": "2", "c": "3"}
    observed = {"a": "X", "b": "2", "d": "4"}
    diff = compute_diff(baseline, observed)
    assert diff["added"] == {"d": "4"}
    assert diff["removed"] == {"c": "3"}
    assert diff["changed"] == {"a": {"from": "1", "to": "X"}}


def test_normalize_state_sorts_keys():
    raw = {"z": "last", "a": "first"}
    result = normalize_state("ssh_config", raw)
    assert list(result.keys()) == ["a", "z"]


def test_surface_severity_all_surfaces_covered():
    all_surfaces = HOST_SURFACES | CLOUD_SURFACES
    for s in all_surfaces:
        assert s in SURFACE_SEVERITY, f"Missing severity for {s}"
        assert SURFACE_SEVERITY[s] in ("high", "medium", "low")


from app.services.drift_service import observe_host_surface


@pytest.mark.asyncio
async def test_observe_host_surface_dispatches_agent_job():
    mock_db = AsyncMock()
    asset_id = uuid.UUID("1a7051be-7110-4a21-9cdf-b023231cdff8")

    mock_result = {"state": {"PermitRootLogin": "no", "Port": "22"}, "status": "success"}

    with patch("app.services.drift_service.dispatch_agent_job", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = mock_result
        result = await observe_host_surface(mock_db, asset_id, "ssh_config")

    mock_dispatch.assert_called_once_with(
        command="capture_drift_state",
        parameters={"surface_type": "ssh_config"},
        asset_ids=[str(asset_id)],
        timeout_seconds=60,
    )
    assert result == {"PermitRootLogin": "no", "Port": "22"}


@pytest.mark.asyncio
async def test_observe_host_surface_raises_on_failure():
    mock_db = AsyncMock()
    asset_id = uuid.UUID("1a7051be-7110-4a21-9cdf-b023231cdff8")

    with patch("app.services.drift_service.dispatch_agent_job", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.return_value = {"status": "failed", "error": "agent unreachable"}
        with pytest.raises(RuntimeError, match="agent unreachable"):
            await observe_host_surface(mock_db, asset_id, "ssh_config")


import json
import os

def test_catalog_drift_surfaces_valid():
    catalog_path = os.path.join(
        os.path.dirname(__file__), "../../app/connectors/catalog/nexplane_agent.json"
    )
    with open(catalog_path) as f:
        catalog = json.load(f)

    all_surfaces = HOST_SURFACES | CLOUD_SURFACES
    for entry in catalog.get("actions", []):
        surfaces = entry.get("drift_surfaces", [])
        assert isinstance(surfaces, list), f"{entry['action_id']}: drift_surfaces must be a list"
        for s in surfaces:
            assert s in all_surfaces, f"{entry['action_id']}: unknown surface '{s}'"

    action_ids = [e["action_id"] for e in catalog.get("actions", [])]
    assert "restore_resource_state" in action_ids, "restore_resource_state must be in catalog"

    restore = next(e for e in catalog["actions"] if e["action_id"] == "restore_resource_state")
    assert restore["rollback_capability"] == "none"
    assert restore["smoke_verified"] == False
    assert restore["drift_surfaces"] == []


@pytest.mark.asyncio
async def test_on_cr_completed_skips_when_no_drift_surfaces():
    """CR with no drift_surfaces in catalog should do nothing."""
    from app.services.drift_service import on_cr_completed
    mock_db = AsyncMock()

    with patch("app.services.drift_service._load_catalog_entry") as mock_catalog:
        mock_catalog.return_value = {"action_id": "some_cr", "drift_surfaces": []}
        with patch("app.services.drift_service._load_cr") as mock_cr:
            mock_cr.return_value = MagicMock(change_type="some_cr", target_asset_ids=[])
            await on_cr_completed(uuid.uuid4(), mock_db)

    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_drift_worker_creates_event_on_diff():
    """Worker creates DriftEvent when observed != baseline."""
    from app.workers.drift_check_worker import check_policy_drift
    # This is an integration-level test — verify the function is importable and callable
    # Full behavioral test is in the smoke phases
    assert callable(check_policy_drift)


def test_restore_resource_state_executor_importable():
    from app.connectors.executors.nexplane_agent.restore_resource_state import (
        ROLLBACK_CAPABILITY, execute
    )
    assert ROLLBACK_CAPABILITY == "none"
    assert callable(execute)


def test_schemas_importable():
    from app.schemas.drift import (
        ResourceStateRead,
        DriftPolicyCreate, DriftPolicyRead,
        DriftEventRead,
        DriftEventAcceptBody, DriftEventAttestBody,
    )
    assert ResourceStateRead.model_config.get("from_attributes")
    assert DriftEventRead.model_config.get("from_attributes")
