# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

"""Unit tests for aws_iam_role_baseline executor."""

import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_connector(has_creds=False):
    conn = MagicMock()
    conn.credentials = {"aws_access_key_id": "FAKE"} if has_creds else {}
    return conn


def _make_iam_client(existing_roles=None):
    existing_roles = existing_roles or set()
    iam = MagicMock()

    class _NoSuchEntity(Exception):
        pass

    iam.exceptions.NoSuchEntityException = _NoSuchEntity

    def _get_role(RoleName):
        if RoleName in existing_roles:
            return {"Role": {"RoleName": RoleName}}
        raise _NoSuchEntity(f"Role {RoleName} not found")

    iam.get_role.side_effect = _get_role
    iam.create_role.return_value = {"Role": {}}
    iam.attach_role_policy.return_value = {}
    iam.delete_role.return_value = {}
    iam.detach_role_policy.return_value = {}

    page_iter = MagicMock()
    page_iter.__iter__ = MagicMock(return_value=iter([
        {"AttachedPolicies": [{"PolicyArn": "arn:aws:iam::aws:policy/AdministratorAccess"}]}
    ]))
    iam.get_paginator.return_value.paginate.return_value = page_iter

    return iam


def _make_sts_client(account_id="111122223333"):
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": account_id}
    return sts


def _patch_c(monkeypatch, iam, sts):
    """Patch the module-level _c() in the executor."""
    from app.connectors.executors.aws import iam_role_baseline as mod

    def _fake_c(connector, service, region="us-east-1"):
        if service == "iam":
            return iam
        if service == "sts":
            return sts
        return MagicMock()

    monkeypatch.setattr(mod, "_c", _fake_c)


# ---------------------------------------------------------------------------
# Mock-path tests (no credentials)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_returns_created_roles():
    """Mock path returns created_roles, skipped_roles, account_id."""
    from app.connectors.executors.aws.iam_role_baseline import execute
    conn = _mock_connector(has_creds=False)
    result = await execute({}, [], conn)
    assert "created_roles" in result
    assert "skipped_roles" in result
    assert "account_id" in result
    assert result["mock"] is True
    assert len(result["created_roles"]) == 3


@pytest.mark.asyncio
async def test_rollback_mock_path():
    """Mock rollback returns rolled_back=True without touching AWS."""
    from app.connectors.executors.aws.iam_role_baseline import rollback
    conn = _mock_connector(has_creds=False)
    result = await rollback({}, {}, conn)
    assert result["rolled_back"] is True
    assert result.get("mock") is True


# ---------------------------------------------------------------------------
# Trust-policy unit tests (pure logic, no AWS)
# ---------------------------------------------------------------------------

def test_breakglass_requires_mfa_in_trust():
    """NexplaneBreakGlass trust policy must include MFA condition."""
    from app.connectors.executors.aws.iam_role_baseline import _trust_policy
    trust = json.loads(_trust_policy("111122223333", require_mfa=True))
    stmt = trust["Statement"][0]
    assert "Condition" in stmt
    cond = stmt["Condition"]
    assert cond.get("Bool", {}).get("aws:MultiFactorAuthPresent") == "true"


def test_non_breakglass_no_mfa_condition():
    """ReadOnly and SecurityAudit trust policies must NOT require MFA."""
    from app.connectors.executors.aws.iam_role_baseline import _trust_policy
    trust = json.loads(_trust_policy("111122223333", require_mfa=False))
    stmt = trust["Statement"][0]
    assert "Condition" not in stmt


def test_roles_tagged_managed_by_nexplane():
    """COMMON_TAGS must include ManagedBy=nexplane."""
    from app.connectors.executors.aws.iam_role_baseline import COMMON_TAGS
    tag_map = {t["Key"]: t["Value"] for t in COMMON_TAGS}
    assert tag_map.get("ManagedBy") == "nexplane"
    assert tag_map.get("Purpose") == "security-baseline"


# ---------------------------------------------------------------------------
# Real-path tests (patched _c)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idempotent_skips_existing(monkeypatch):
    """When all three roles exist, all appear in skipped_roles."""
    from app.connectors.executors.aws.iam_role_baseline import execute
    existing = {"NexplaneReadOnly", "NexplaneSecurityAudit", "NexplaneBreakGlass"}
    iam = _make_iam_client(existing_roles=existing)
    sts = _make_sts_client()
    _patch_c(monkeypatch, iam, sts)

    conn = _mock_connector(has_creds=True)
    result = await execute({"skip_if_exists": True}, [], conn)

    assert set(result["skipped_roles"]) == existing
    assert result["created_roles"] == []
    iam.create_role.assert_not_called()


@pytest.mark.asyncio
async def test_rollback_deletes_new_roles(monkeypatch):
    """Rollback deletes roles where was_new=True."""
    from app.connectors.executors.aws.iam_role_baseline import rollback
    iam = _make_iam_client()
    sts = _make_sts_client()
    _patch_c(monkeypatch, iam, sts)

    conn = _mock_connector(has_creds=True)
    execution_result = {
        "pre_state": {
            "roles": [
                {"name": "NexplaneReadOnly", "was_new": True},
                {"name": "NexplaneSecurityAudit", "was_new": True},
                {"name": "NexplaneBreakGlass", "was_new": True},
            ]
        }
    }
    result = await rollback({}, execution_result, conn)
    assert result["rolled_back"] is True
    assert len(result["deleted_roles"]) == 3
    assert iam.delete_role.call_count == 3


@pytest.mark.asyncio
async def test_rollback_skips_existing_roles(monkeypatch):
    """Rollback must NOT delete roles that pre-existed (was_new=False)."""
    from app.connectors.executors.aws.iam_role_baseline import rollback
    iam = _make_iam_client()
    sts = _make_sts_client()
    _patch_c(monkeypatch, iam, sts)

    conn = _mock_connector(has_creds=True)
    execution_result = {
        "pre_state": {
            "roles": [
                {"name": "NexplaneReadOnly", "was_new": False},
                {"name": "NexplaneSecurityAudit", "was_new": False},
                {"name": "NexplaneBreakGlass", "was_new": True},
            ]
        }
    }
    result = await rollback({}, execution_result, conn)
    assert result["rolled_back"] is True
    assert result["deleted_roles"] == ["NexplaneBreakGlass"]
    assert iam.delete_role.call_count == 1


@pytest.mark.asyncio
async def test_breakglass_tagged_breakglass_true(monkeypatch):
    """NexplaneBreakGlass role must be created with BreakGlass=true tag."""
    from app.connectors.executors.aws.iam_role_baseline import execute
    iam = _make_iam_client(existing_roles=set())
    sts = _make_sts_client()

    created_calls = {}

    def _create_role(**kwargs):
        created_calls[kwargs["RoleName"]] = kwargs.get("Tags", [])
        return {"Role": {}}

    iam.create_role.side_effect = _create_role
    _patch_c(monkeypatch, iam, sts)

    conn = _mock_connector(has_creds=True)
    await execute({}, [], conn)

    bg_tags = {t["Key"]: t["Value"] for t in created_calls.get("NexplaneBreakGlass", [])}
    assert bg_tags.get("BreakGlass") == "true"


@pytest.mark.asyncio
async def test_all_roles_have_managed_by_tag(monkeypatch):
    """All created roles must carry ManagedBy=nexplane tag."""
    from app.connectors.executors.aws.iam_role_baseline import execute
    iam = _make_iam_client(existing_roles=set())
    sts = _make_sts_client()

    created_tags = {}

    def _create_role(**kwargs):
        created_tags[kwargs["RoleName"]] = {t["Key"]: t["Value"] for t in kwargs.get("Tags", [])}
        return {"Role": {}}

    iam.create_role.side_effect = _create_role
    _patch_c(monkeypatch, iam, sts)

    conn = _mock_connector(has_creds=True)
    await execute({}, [], conn)

    assert len(created_tags) == 3
    for role_name, tags in created_tags.items():
        assert tags.get("ManagedBy") == "nexplane", f"ManagedBy missing on {role_name}"
