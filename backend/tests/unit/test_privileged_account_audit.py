# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from app.connectors.executors.active_directory.privileged_account_audit import (
    execute,
    rollback,
    _filetime_to_dt,
    _is_service_account,
    _is_stale,
    _assess_account,
    _build_remediations,
    _FILETIME_EPOCH_DIFF,
)


def _mock_connector(has_creds: bool = False):
    conn = MagicMock()
    conn.credentials = {"server": "dc.corp.local", "base_dn": "DC=corp,DC=local"} if has_creds else {}
    return conn


@pytest.mark.asyncio
async def test_execute_returns_findings():
    connector = _mock_connector(has_creds=False)
    result = await execute({}, [], connector)
    assert "group_summary" in result
    assert "accounts" in result
    assert "risky_accounts" in result
    assert "proposed_remediations" in result
    assert "audited_at" in result


@pytest.mark.asyncio
async def test_risky_flagging_stale():
    connector = _mock_connector(has_creds=False)
    result = await execute({"stale_threshold_days": 90}, [], connector)
    risky = result["risky_accounts"]
    stale_risky = [a for a in risky if "stale_no_recent_logon" in a.get("risk_flags", [])]
    assert len(stale_risky) >= 1, "Expected at least one stale risky account in mock data"


@pytest.mark.asyncio
async def test_risky_flagging_service_account():
    connector = _mock_connector(has_creds=False)
    result = await execute({}, [], connector)
    risky = result["risky_accounts"]
    svc_risky = [a for a in risky if "service_account_with_admin_rights" in a.get("risk_flags", [])]
    assert len(svc_risky) >= 1, "Expected at least one service account flagged in mock data"


@pytest.mark.asyncio
async def test_rollback_is_noop():
    connector = _mock_connector(has_creds=False)
    result = await rollback({}, {}, connector)
    assert result["rolled_back"] is True
    assert "read-only" in result.get("reason", "").lower()


def test_filetime_conversion():
    # 2020-01-01 00:00:00 UTC in Windows FILETIME
    # unix ts: 1577836800 -> filetime = 1577836800 * 10_000_000 + _FILETIME_EPOCH_DIFF
    unix_ts = 1577836800
    filetime = unix_ts * 10_000_000 + _FILETIME_EPOCH_DIFF
    result = _filetime_to_dt(filetime)
    assert result is not None
    assert result.year == 2020
    assert result.month == 1
    assert result.day == 1


def test_filetime_zero_returns_none():
    assert _filetime_to_dt(0) is None
    assert _filetime_to_dt(None) is None


def test_proposed_remediations_format():
    risky_accounts = [
        {
            "sAMAccountName": "old_admin",
            "distinguishedName": "CN=old_admin,CN=Users,DC=corp,DC=local",
            "risk_flags": ["stale_no_recent_logon", "disabled_but_in_privileged_group"],
            "disabled": True,
        },
        {
            "sAMAccountName": "svc_backup",
            "distinguishedName": "CN=svc_backup,CN=Users,DC=corp,DC=local",
            "risk_flags": ["service_account_with_admin_rights"],
            "disabled": False,
        },
    ]
    remediations = _build_remediations(risky_accounts)
    assert len(remediations) >= 1
    for r in remediations:
        assert "change_type" in r
        assert "parameters" in r
        assert "reason" in r


def test_is_service_account_svc_prefix():
    assert _is_service_account("svc_backup", "") is True
    assert _is_service_account("SVC_DEPLOY", "") is True


def test_is_service_account_dollar_suffix():
    assert _is_service_account("MACHINE$", "") is True


def test_is_service_account_description():
    assert _is_service_account("normaluser", "Backup service account") is True
    assert _is_service_account("jsmith", "IT Administrator") is False


def test_is_stale_never_logged_in():
    assert _is_stale(None, 90) is True


def test_is_stale_recent_logon():
    recent = datetime.now(tz=timezone.utc) - timedelta(days=5)
    assert _is_stale(recent, 90) is False


def test_is_stale_old_logon():
    old = datetime.now(tz=timezone.utc) - timedelta(days=120)
    assert _is_stale(old, 90) is True


def test_assess_account_severity_high_two_flags():
    entry = {
        "sAMAccountName": "svc_old",
        "distinguishedName": "CN=svc_old,CN=Users,DC=corp,DC=local",
        "userAccountControl": 512,
        "description": "service account",
        "_last_logon_dt": datetime.now(tz=timezone.utc) - timedelta(days=200),
        "groups": ["Domain Admins"],
    }
    result = _assess_account(entry, 90)
    assert result["severity"] == "high"
    assert result["is_risky"] is True
    assert "stale_no_recent_logon" in result["risk_flags"]
    assert "service_account_with_admin_rights" in result["risk_flags"]


def test_assess_account_clean_account():
    entry = {
        "sAMAccountName": "jsmith",
        "distinguishedName": "CN=jsmith,CN=Users,DC=corp,DC=local",
        "userAccountControl": 512,
        "description": "IT Admin",
        "_last_logon_dt": datetime.now(tz=timezone.utc) - timedelta(days=5),
        "groups": ["Domain Admins"],
    }
    result = _assess_account(entry, 90)
    assert result["is_risky"] is False
    assert result["severity"] == "low"
    assert result["risk_flags"] == []
