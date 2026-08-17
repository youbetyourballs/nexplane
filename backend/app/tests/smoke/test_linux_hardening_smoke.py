# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Linux hardening executor live smoke tests.

Dispatches each hardening CR type to a live nexplane agent running on a
real Linux host and verifies execute→rollback lifecycle.

Required env vars:
  SMOKE_HARDENING_ASSET_ID  — UUID of the asset with an active agent
  SMOKE_HARDENING_HOST_IP   — private IP of the agent host (informational)

Set automatically by the CI smoke setup script.
"""

import os
import pytest

_ASSET_ID = os.getenv("SMOKE_HARDENING_ASSET_ID", "")
_HOST_IP = os.getenv("SMOKE_HARDENING_HOST_IP", "")

pytestmark = [
    # Share one event loop across all tests so AsyncSessionLocal's asyncpg pool
    # doesn't get stuck on a closed loop between function-scoped event loops.
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(
        not _ASSET_ID or not _HOST_IP,
        reason="SMOKE_HARDENING_ASSET_ID and SMOKE_HARDENING_HOST_IP not set",
    ),
]

_ASSET_IDS = [_ASSET_ID]


def _assert_job_ok(result: dict, label: str) -> None:
    assert result, f"{label}: empty result"
    assert not result.get("error"), f"{label}: job error: {result.get('error')}"


def _assert_rollback_ok(rb: dict, label: str) -> None:
    assert rb, f"{label} rollback: empty result"
    assert not rb.get("error"), f"{label} rollback: error: {rb.get('error')}"


# ── apply_sysctl_hardening ─────────────────────────────────────────────────────

async def test_sysctl_hardening_and_rollback():
    from app.connectors.executors.nexplane_agent.apply_sysctl_hardening import execute, rollback

    result = await execute({}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "sysctl_hardening")
    assert result.get("settings_applied"), f"No settings applied: {result}"
    assert result.get("drop_in_path"), "No drop_in_path in result"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "sysctl_hardening")


# ── blacklist_kernel_modules ───────────────────────────────────────────────────

async def test_blacklist_kernel_modules_and_rollback():
    from app.connectors.executors.nexplane_agent.blacklist_kernel_modules import execute, rollback

    result = await execute(
        {"modules": ["dccp", "sctp", "rds", "tipc", "cramfs", "freevxfs", "jffs2", "hfs", "hfsplus", "squashfs", "udf"]},
        _ASSET_IDS,
        connector=None,
    )
    _assert_job_ok(result, "blacklist_kernel_modules")
    assert "_asset_ids" in result, f"No _asset_ids in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "blacklist_kernel_modules")


# ── deploy_auditd_rules ────────────────────────────────────────────────────────

async def test_auditd_rules_and_rollback():
    from app.connectors.executors.nexplane_agent.deploy_auditd_rules import execute, rollback

    result = await execute({"profile": "cis_level1"}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "deploy_auditd_rules")
    assert result.get("rules_path"), f"No rules_path in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "deploy_auditd_rules")


# ── harden_ssh ────────────────────────────────────────────────────────────────

async def test_harden_ssh_and_rollback():
    from app.connectors.executors.nexplane_agent.harden_ssh import execute, rollback

    result = await execute({}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "harden_ssh")
    assert "_asset_ids" in result, f"No _asset_ids in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "harden_ssh")


# ── harden_mount_options ───────────────────────────────────────────────────────

async def test_harden_mount_options_and_rollback():
    from app.connectors.executors.nexplane_agent.harden_mount_options import execute, rollback

    result = await execute({}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "harden_mount_options")
    assert result.get("targets_hardened"), f"No targets_hardened in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "harden_mount_options")


# ── setup_file_integrity_monitoring ───────────────────────────────────────────

async def test_file_integrity_monitoring_and_rollback():
    from app.connectors.executors.nexplane_agent.setup_file_integrity_monitoring import execute, rollback

    result = await execute({}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "setup_file_integrity_monitoring")
    assert "_asset_ids" in result, f"No _asset_ids in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "setup_file_integrity_monitoring")


# ── configure_pam ─────────────────────────────────────────────────────────────

async def test_configure_pam_and_rollback():
    from app.connectors.executors.nexplane_agent.configure_pam import execute, rollback

    result = await execute({}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "configure_pam")
    assert result.get("params_applied"), f"No params_applied in result: {result}"
    assert result.get("files_snapshot"), f"No files_snapshot (rollback data) in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "configure_pam")
