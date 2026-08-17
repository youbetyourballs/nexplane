# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2024-2026 Nexplane, Inc.
"""Windows hardening executor live smoke tests.

Dispatches each hardening CR type to a live nexplane agent running on a
real Windows host and verifies execute→rollback lifecycle.

Required env vars:
  SMOKE_HARDENING_ASSET_ID  — UUID of the asset with an active Windows agent
  SMOKE_HARDENING_HOST_IP   — private IP of the agent host (informational)

Set automatically by the CI smoke setup script.
"""

import os
import pytest

_ASSET_ID = os.getenv("SMOKE_HARDENING_ASSET_ID", "")
_HOST_IP = os.getenv("SMOKE_HARDENING_HOST_IP", "")

pytestmark = [
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


# ── configure_laps ────────────────────────────────────────────────────────────

async def test_configure_laps_and_rollback():
    from app.connectors.executors.nexplane_agent.configure_laps import execute, rollback

    result = await execute({"action": "enable", "password_age_days": 30}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "configure_laps")
    assert result.get("snapshot") is not None, f"No snapshot in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "configure_laps")


# ── enable_credential_guard ───────────────────────────────────────────────────

async def test_credential_guard_and_rollback():
    from app.connectors.executors.nexplane_agent.enable_credential_guard import execute, rollback

    result = await execute({"action": "enable", "require_uefi_lock": False}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "enable_credential_guard")
    assert result.get("snapshot") is not None, f"No snapshot in result: {result}"
    assert result.get("reboot_required") is True, f"Expected reboot_required: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "enable_credential_guard")


# ── enforce_powershell_clm ────────────────────────────────────────────────────

async def test_enforce_powershell_clm_and_rollback():
    from app.connectors.executors.nexplane_agent.enforce_powershell_clm import execute, rollback

    result = await execute({"action": "enable"}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "enforce_powershell_clm")
    assert result.get("snapshot") is not None, f"No snapshot in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "enforce_powershell_clm")


# ── deploy_applocker_policy ───────────────────────────────────────────────────

_APPLOCKER_POLICY = """<AppLockerPolicy Version="1">
  <RuleCollection Type="Exe" EnforcementMode="AuditOnly">
    <FilePathRule Id="fd686d83-a829-4351-8ff4-27c7de5755d2" Name="All files" Description="" UserOrGroupSid="S-1-1-0" Action="Allow">
      <Conditions><FilePathCondition Path="*"/></Conditions>
    </FilePathRule>
  </RuleCollection>
</AppLockerPolicy>"""

async def test_deploy_applocker_policy_and_rollback():
    from app.connectors.executors.nexplane_agent.deploy_applocker_policy import execute, rollback

    result = await execute({"policy": _APPLOCKER_POLICY, "enforce": False}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "deploy_applocker_policy")
    assert result.get("snapshot") is not None, f"No snapshot in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "deploy_applocker_policy")


# ── harden_smb ────────────────────────────────────────────────────────────────

async def test_harden_smb_and_rollback():
    from app.connectors.executors.nexplane_agent.harden_smb import execute, rollback

    result = await execute({}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "harden_smb")
    assert result.get("snapshot") is not None, f"No snapshot in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "harden_smb")


# ── configure_windows_firewall ────────────────────────────────────────────────

async def test_configure_windows_firewall_and_rollback():
    from app.connectors.executors.nexplane_agent.configure_windows_firewall import execute, rollback

    result = await execute({}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "configure_windows_firewall")
    assert result.get("snapshot") is not None, f"No snapshot in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "configure_windows_firewall")


# ── harden_tls_protocols ──────────────────────────────────────────────────────

async def test_harden_tls_protocols_and_rollback():
    from app.connectors.executors.nexplane_agent.harden_tls_protocols import execute, rollback

    result = await execute({}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "harden_tls_protocols")
    assert result.get("snapshot") is not None, f"No snapshot in result: {result}"
    assert result.get("reboot_required") is True, f"Expected reboot_required: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "harden_tls_protocols")


# ── harden_rdp ────────────────────────────────────────────────────────────────

async def test_harden_rdp_and_rollback():
    from app.connectors.executors.nexplane_agent.harden_rdp import execute, rollback

    result = await execute({}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "harden_rdp")
    assert result.get("snapshot") is not None, f"No snapshot in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "harden_rdp")


# ── configure_windows_audit_policy ────────────────────────────────────────────

async def test_configure_windows_audit_policy_and_rollback():
    from app.connectors.executors.nexplane_agent.configure_windows_audit_policy import execute, rollback

    result = await execute({}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "configure_windows_audit_policy")
    assert result.get("snapshot") is not None, f"No snapshot in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "configure_windows_audit_policy")


# ── harden_registry ───────────────────────────────────────────────────────────

async def test_harden_registry_and_rollback():
    from app.connectors.executors.nexplane_agent.harden_registry import execute, rollback

    result = await execute({}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "harden_registry")
    assert result.get("snapshot") is not None, f"No snapshot in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "harden_registry")


# ── wdac_audit ────────────────────────────────────────────────────────────────

async def test_wdac_audit_and_rollback():
    from app.connectors.executors.nexplane_agent.wdac_audit import execute, rollback

    result = await execute({}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "wdac_audit")

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "wdac_audit")


# ── wdac_enforce ──────────────────────────────────────────────────────────────

async def test_wdac_enforce_and_rollback():
    from app.connectors.executors.nexplane_agent.wdac_enforce import execute, rollback

    result = await execute({}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "wdac_enforce")

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "wdac_enforce")


# ── asr_audit ─────────────────────────────────────────────────────────────────

async def test_asr_audit_and_rollback():
    from app.connectors.executors.nexplane_agent.asr_audit import execute, rollback

    result = await execute(
        {"duration_seconds": 5, "rule_names": ["block-credential-stealing", "block-untrusted-executables-email"]},
        _ASSET_IDS,
        connector=None,
    )
    _assert_job_ok(result, "asr_audit")
    assert "rules_audited" in result, f"No rules_audited in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "asr_audit")


# ── asr_enforce ───────────────────────────────────────────────────────────────

async def test_asr_enforce_and_rollback():
    from app.connectors.executors.nexplane_agent.asr_enforce import execute, rollback

    result = await execute(
        {"rule_names": ["block-credential-stealing"]},
        _ASSET_IDS,
        connector=None,
    )
    _assert_job_ok(result, "asr_enforce")
    assert "rules_enforced" in result, f"No rules_enforced in result: {result}"

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "asr_enforce")


# ── sysmon_deploy ─────────────────────────────────────────────────────────────
# Skipped: requires Sysmon64.exe pre-installed at C:\Windows\Sysmon64.exe
# and must be downloaded separately (not bundled with the agent).

async def test_sysmon_deploy_and_rollback():
    from app.connectors.executors.nexplane_agent.sysmon_deploy import execute, rollback

    result = await execute(
        {"sysmon_path": "C:\\Windows\\Sysmon64.exe"},
        _ASSET_IDS,
        connector=None,
    )
    _assert_job_ok(result, "sysmon_deploy")

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "sysmon_deploy")


# ── enable_bitlocker ──────────────────────────────────────────────────────────
# EC2 instances have no TPM — BitLocker requires TPM or USB key protector.
# xfail: expected to fail on standard EC2 without TPM.

@pytest.mark.xfail(reason="EC2 has no TPM; BitLocker requires hardware TPM or USB protector")
async def test_enable_bitlocker_and_rollback():
    from app.connectors.executors.nexplane_agent.enable_bitlocker import execute, rollback

    result = await execute({"drive_letter": "C:", "protector": "tpm"}, _ASSET_IDS, connector=None)
    _assert_job_ok(result, "enable_bitlocker")

    rb = await rollback({}, result, connector=None)
    _assert_rollback_ok(rb, "enable_bitlocker")
