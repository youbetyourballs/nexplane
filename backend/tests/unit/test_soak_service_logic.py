# backend/tests/unit/test_soak_service_logic.py
"""Unit tests for SoakService business logic."""
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_session(raw_observations=None, synthesized_profile=None, baseline_delta=None,
                  status="running", cr_id=None, partial=False):
    s = MagicMock()
    s.id = uuid.uuid4()
    s.organization_id = uuid.uuid4()
    s.project_id = uuid.uuid4()
    s.policy_type = "seccomp"
    s.status = status
    s.window_seconds = 30
    s.asset_ids = ["asset-1", "asset-2"]
    s.raw_observations = raw_observations or {}
    s.synthesized_profile = synthesized_profile
    s.baseline_delta = baseline_delta
    s.partial = partial
    s.cr_id = cr_id
    return s


@pytest.mark.asyncio
async def test_collect_observations_aggregates_syscalls():
    from app.services.security_policy.soak_service import _collect_observations

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        if asset_ids == ["asset-1"]:
            return {"syscalls_seen": ["read", "write"], "_asset_ids": ["asset-1"]}
        return {"syscalls_seen": ["open", "close"], "_asset_ids": ["asset-2"]}

    with patch(
        "app.services.security_policy.soak_service._dispatch.dispatch_agent_job",
        side_effect=fake_dispatch,
    ):
        obs, partial = await _collect_observations(
            asset_ids=["asset-1", "asset-2"],
            window_seconds=0,
        )

    assert obs["asset-1"] == ["read", "write"]
    assert obs["asset-2"] == ["open", "close"]
    assert partial is False


@pytest.mark.asyncio
async def test_collect_observations_marks_partial_on_failure():
    from app.services.security_policy.soak_service import _collect_observations

    async def fake_dispatch(command, parameters, asset_ids, timeout_seconds):
        if asset_ids == ["asset-1"]:
            raise RuntimeError("agent unreachable")
        return {"syscalls_seen": ["read"], "_asset_ids": ["asset-2"]}

    with patch(
        "app.services.security_policy.soak_service._dispatch.dispatch_agent_job",
        side_effect=fake_dispatch,
    ):
        obs, partial = await _collect_observations(
            asset_ids=["asset-1", "asset-2"],
            window_seconds=0,
        )

    assert "asset-1" not in obs
    assert obs["asset-2"] == ["read"]
    assert partial is True


def test_build_cr_params_no_baseline():
    from app.services.security_policy.soak_service import _build_cr_params

    profile = {"defaultAction": "SCMP_ACT_ERRNO", "syscalls": [{"names": ["read"], "action": "SCMP_ACT_ALLOW"}]}
    session = _make_session()
    params = _build_cr_params(session=session, profile=profile, service_name="nginx")

    assert params["service_name"] == "nginx"
    import json
    loaded = json.loads(params["profile"])
    assert loaded["syscalls"][0]["names"] == ["read"]
    assert params["session_id"] == str(session.id)


def test_should_auto_propose_when_no_baseline():
    from app.services.security_policy.soak_service import should_auto_propose
    assert should_auto_propose(baseline=None) is True


def test_should_not_auto_propose_when_baseline_exists():
    from app.services.security_policy.soak_service import should_auto_propose
    baseline = MagicMock()
    assert should_auto_propose(baseline=baseline) is False
