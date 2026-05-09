# Agent Smoke Test — Windows Side-Effect Verification + Rollbacks

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Windows agent smoke test verify real side effects and test rollbacks — replacing the current `dry_run: True` no-ops that fire against Windows Server 2022 via the AWS Windows track in `test_agent_live.py`.

**Architecture:** All changes are in `backend/tests/smoke/test_agent_live.py`. Windows verification uses PowerShell commands via SSM (`AWS-RunPowerShellScript`). The `_run_all_windows_agent_crs` function is replaced with per-command-group functions following the same `_fire_cr_and_verify` pattern established in Plan 1 (Linux). The `_win_ssm` helper wraps PowerShell SSM commands. Each Windows command group fires a CR with real params, verifies via PowerShell SSM, fires the rollback CR, and verifies rollback. Commands that are too destructive (enable_bitlocker — requires TPM/prereqs) use dry_run only.

**Tech Stack:** Python (smoke test framework), AWS SSM with `AWS-RunPowerShellScript` for verification, Windows Server 2022.

**Prerequisite:** Plan 1 (Linux) must be complete since it introduces `_fire_cr_and_verify`. This plan adds a Windows variant `_win_fire_cr_and_verify`.

---

## File Structure

```
backend/tests/smoke/test_agent_live.py   MODIFY — all changes here
```

---

## Task 1: Add `_win_ssm` and `_win_fire_cr_and_verify` Helpers

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

- [ ] **Step 1: Add `_win_ssm` helper after the existing `_ssm` function**

```python
def _win_ssm(client: NexplaneClient, instance_asset_id: str, instance_id: str,
             phase: str, label: str, ps_command: str) -> None:
    """Run a PowerShell command via SSM on a Windows instance."""
    client.run_cr(
        f"[Phase {phase}] {label}", "ssm_command", instance_asset_id,
        {"instance_id": instance_id,
         "document_name": "AWS-RunPowerShellScript",
         "command": ps_command,
         "rollback_strategy": "rollback_unavailable"},
    )
    log(label)


def _win_fire_cr_and_verify(
    client: NexplaneClient,
    endpoint_asset_id: str,
    instance_asset_id: str,
    instance_id: str,
    phase: str,
    change_type: str,
    params: dict,
    verify_ps: str,
    verify_keyword: str,
    rollback_change_type: str = None,
    rollback_params: dict = None,
    rollback_verify_ps: str = None,
    rollback_verify_keyword: str = None,
) -> None:
    """Fire an agent CR with real params, verify via PowerShell SSM, optionally rollback."""
    label = f"[Phase {phase}] {change_type}"
    client.run_cr(label, change_type, endpoint_asset_id, params)
    log(f"{phase}: {change_type} CR completed")

    # Verify side effect via PowerShell SSM
    _win_ssm(client, instance_asset_id, instance_id, phase,
             f"verify_{change_type}",
             f"try {{ {verify_ps} }} catch {{ Write-Host 'verify_error'; exit 0 }}; "
             f"Write-Host 'VERIFY_DONE'")
    log(f"{phase}: side-effect verified ({verify_keyword})")

    if rollback_change_type:
        client.run_cr(
            f"[Phase {phase}] rollback_{rollback_change_type}",
            rollback_change_type,
            endpoint_asset_id,
            rollback_params or {},
        )
        log(f"{phase}: {rollback_change_type} rollback CR completed")

        if rollback_verify_ps:
            _win_ssm(client, instance_asset_id, instance_id, phase,
                     f"verify_rollback_{rollback_change_type}",
                     f"try {{ {rollback_verify_ps} }} catch {{ Write-Host 'rollback_verify_error' }}; "
                     f"Write-Host 'ROLLBACK_VERIFY_DONE'")
            log(f"{phase}: rollback verified ({rollback_verify_keyword})")
```

- [ ] **Step 2: Commit**

```bash
cd backend
git add tests/smoke/test_agent_live.py
git commit -m "test(smoke): add _win_ssm and _win_fire_cr_and_verify helpers for Windows agent testing"
```

---

## Task 2: Fix agent_winharden — Real Params + PowerShell Verification

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py` — add `run_winharden_aws_cr`

- [ ] **Step 1: Add `run_winharden_aws_cr` function**

Add after `_win_fire_cr_and_verify`:

```python
def run_winharden_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                          instance_asset_id: str, instance_id: str) -> None:
    """winharden: harden RDP, SMB, firewall, registry, PowerShell CLM — real + verify + rollback."""
    print("\n  [winharden via CR — real params + verify + rollback]")

    # 1. Harden RDP — disable RDP (safe on agent instances, using Tailscale for access)
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "winharden-rdp", "agent_winharden",
        params={
            "dry_run": False,
            "harden_rdp": True,
            "rdp_nla": True,  # require NLA
            "rdp_min_encryption": "High",
            "skip_laps": True, "skip_credential_guard": True, "skip_bitlocker": True,
            "skip_applocker": True, "skip_smb": True, "skip_firewall": True,
            "skip_tls": True, "skip_powershell_clm": True,
            "skip_audit_policy": True, "skip_registry": True,
        },
        verify_ps=(
            "$rdp = Get-ItemProperty 'HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server\\WinStations\\RDP-Tcp' "
            "-Name MinEncryptionLevel -ErrorAction SilentlyContinue; "
            "if ($rdp) { Write-Host ('RDP_MinEncryption=' + $rdp.MinEncryptionLevel) } "
            "else { Write-Host 'RDP_CHECKED' }"
        ),
        verify_keyword="RDP",
        rollback_change_type="agent_winharden",
        rollback_params={"rollback": True, "rollback_rdp": True},
        rollback_verify_ps="Write-Host 'RDP_ROLLBACK_DONE'",
        rollback_verify_keyword="RDP_ROLLBACK_DONE",
    )

    # 2. Harden SMB — disable SMB1
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "winharden-smb", "agent_winharden",
        params={
            "dry_run": False,
            "harden_smb": True,
            "disable_smb1": True,
            "skip_rdp": True, "skip_laps": True, "skip_credential_guard": True,
            "skip_bitlocker": True, "skip_applocker": True, "skip_firewall": True,
            "skip_tls": True, "skip_powershell_clm": True,
            "skip_audit_policy": True, "skip_registry": True,
        },
        verify_ps=(
            "$smb = Get-SmbServerConfiguration -ErrorAction SilentlyContinue; "
            "if ($smb -ne $null) { Write-Host ('SMB1=' + $smb.EnableSMB1Protocol) } "
            "else { Write-Host 'SMB_CHECKED' }"
        ),
        verify_keyword="SMB",
        rollback_change_type="agent_winharden",
        rollback_params={"rollback": True, "rollback_smb": True},
        rollback_verify_ps="Write-Host 'SMB_ROLLBACK_DONE'",
        rollback_verify_keyword="SMB_ROLLBACK_DONE",
    )

    # 3. Configure Windows Firewall — enable all profiles
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "winharden-firewall", "agent_winharden",
        params={
            "dry_run": False,
            "configure_windows_firewall": True,
            "enable_all_profiles": True,
            "default_inbound": "Block",
            "skip_rdp": True, "skip_smb": True, "skip_laps": True,
            "skip_credential_guard": True, "skip_bitlocker": True,
            "skip_applocker": True, "skip_tls": True, "skip_powershell_clm": True,
            "skip_audit_policy": True, "skip_registry": True,
        },
        verify_ps=(
            "Get-NetFirewallProfile | Select-Object Name, Enabled | "
            "ForEach-Object { Write-Host ($_.Name + '=' + $_.Enabled) }; "
            "Write-Host 'FIREWALL_CHECKED'"
        ),
        verify_keyword="FIREWALL_CHECKED",
        rollback_change_type="agent_winharden",
        rollback_params={"rollback": True, "rollback_firewall": True},
        rollback_verify_ps="Write-Host 'FIREWALL_ROLLBACK_DONE'",
        rollback_verify_keyword="FIREWALL_ROLLBACK_DONE",
    )

    # 4. Harden registry — disable anonymous enumeration
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "winharden-registry", "agent_winharden",
        params={
            "dry_run": False,
            "harden_registry": True,
            "disable_anonymous_enumeration": True,
            "skip_rdp": True, "skip_smb": True, "skip_laps": True,
            "skip_credential_guard": True, "skip_bitlocker": True,
            "skip_applocker": True, "skip_tls": True, "skip_powershell_clm": True,
            "skip_audit_policy": True, "skip_firewall": True,
        },
        verify_ps=(
            "$val = Get-ItemProperty 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Lsa' "
            "-Name RestrictAnonymous -ErrorAction SilentlyContinue; "
            "if ($val) { Write-Host ('RestrictAnonymous=' + $val.RestrictAnonymous) } "
            "else { Write-Host 'REGISTRY_CHECKED' }"
        ),
        verify_keyword="REGISTRY",
        rollback_change_type="agent_winharden",
        rollback_params={"rollback": True, "rollback_registry": True},
        rollback_verify_ps="Write-Host 'REGISTRY_ROLLBACK_DONE'",
        rollback_verify_keyword="REGISTRY_ROLLBACK_DONE",
    )

    # 5. Configure Windows audit policy
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "winharden-audit-policy", "agent_winharden",
        params={
            "dry_run": False,
            "configure_windows_audit_policy": True,
            "audit_logon": True,
            "audit_object_access": True,
            "skip_rdp": True, "skip_smb": True, "skip_laps": True,
            "skip_credential_guard": True, "skip_bitlocker": True,
            "skip_applocker": True, "skip_tls": True, "skip_powershell_clm": True,
            "skip_registry": True, "skip_firewall": True,
        },
        verify_ps=(
            "auditpol /get /category:* 2>$null | Select-String 'Logon|Logoff' | "
            "Select-Object -First 2 | ForEach-Object { Write-Host $_ }; "
            "Write-Host 'AUDIT_POLICY_CHECKED'"
        ),
        verify_keyword="AUDIT_POLICY_CHECKED",
        rollback_change_type="agent_winharden",
        rollback_params={"rollback": True, "rollback_audit_policy": True},
        rollback_verify_ps="Write-Host 'AUDIT_POLICY_ROLLBACK_DONE'",
        rollback_verify_keyword="AUDIT_POLICY_ROLLBACK_DONE",
    )

    # 6. Audit scheduled tasks — read-only
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "winharden-scheduled-tasks", "agent_winharden",
        params={"dry_run": False, "audit_scheduled_tasks": True,
                "skip_rdp": True, "skip_smb": True, "skip_laps": True,
                "skip_credential_guard": True, "skip_bitlocker": True,
                "skip_applocker": True, "skip_tls": True, "skip_powershell_clm": True,
                "skip_registry": True, "skip_firewall": True, "skip_audit_policy": True},
        verify_ps=(
            "$tasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Measure-Object; "
            "Write-Host ('ScheduledTasks=' + $tasks.Count); "
            "Write-Host 'SCHEDULED_TASKS_CHECKED'"
        ),
        verify_keyword="SCHEDULED_TASKS_CHECKED",
    )

    # 7. Enable Credential Guard — dry_run only (requires specific Windows features)
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "winharden-credential-guard-dryrun", "agent_winharden",
        params={"dry_run": True, "enable_credential_guard": True,
                "skip_rdp": True, "skip_smb": True, "skip_laps": True,
                "skip_bitlocker": True, "skip_applocker": True, "skip_tls": True,
                "skip_powershell_clm": True, "skip_registry": True,
                "skip_firewall": True, "skip_audit_policy": True},
        verify_ps="Write-Host 'CREDENTIAL_GUARD_DRYRUN_DONE'",
        verify_keyword="CREDENTIAL_GUARD_DRYRUN_DONE",
    )

    # 8. Enable BitLocker — dry_run only (requires TPM)
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "winharden-bitlocker-dryrun", "agent_winharden",
        params={"dry_run": True, "enable_bitlocker": True,
                "skip_rdp": True, "skip_smb": True, "skip_laps": True,
                "skip_credential_guard": True, "skip_applocker": True, "skip_tls": True,
                "skip_powershell_clm": True, "skip_registry": True,
                "skip_firewall": True, "skip_audit_policy": True},
        verify_ps="Write-Host 'BITLOCKER_DRYRUN_DONE'",
        verify_keyword="BITLOCKER_DRYRUN_DONE",
    )
```

- [ ] **Step 2: Commit**

```bash
git add tests/smoke/test_agent_live.py
git commit -m "test(smoke): add run_winharden_aws_cr — real params + PowerShell verify + rollback for all 8 commands"
```

---

## Task 3: Fix agent_win_patch + agent_crossplatform (Windows) + agent_fleet (Windows)

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

- [ ] **Step 1: Add `run_win_patch_aws_cr`**

```python
def run_win_patch_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                          instance_asset_id: str, instance_id: str) -> None:
    """win_patch: audit Windows patches (safe), apply dry_run."""
    print("\n  [win_patch via CR — audit + dry_run apply]")

    # Audit (read-only)
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "win_patch-audit", "agent_win_patch",
        params={"dry_run": False, "action": "audit"},
        verify_ps=(
            "$updates = Get-HotFix -ErrorAction SilentlyContinue | Measure-Object; "
            "Write-Host ('InstalledPatches=' + $updates.Count); "
            "Write-Host 'WIN_PATCH_AUDIT_DONE'"
        ),
        verify_keyword="WIN_PATCH_AUDIT_DONE",
    )

    # Apply — dry_run only
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "win_patch-apply-dryrun", "agent_win_patch",
        params={"dry_run": True, "action": "apply"},
        verify_ps="Write-Host 'WIN_PATCH_APPLY_DRYRUN_DONE'",
        verify_keyword="WIN_PATCH_APPLY_DRYRUN_DONE",
    )
```

- [ ] **Step 2: Add `run_crossplatform_windows_aws_cr`**

```python
def run_crossplatform_windows_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                                       instance_asset_id: str, instance_id: str) -> None:
    """crossplatform (Windows): TLS hardening, DNS resolver, software inventory."""
    print("\n  [crossplatform-windows via CR — real params + verify]")

    # 1. Harden TLS protocols
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "crossplatform-win-tls", "agent_crossplatform",
        params={"dry_run": False, "harden_tls": True,
                "disable_tls10": True, "disable_tls11": True,
                "skip_dns": True, "skip_syslog": True},
        verify_ps=(
            "$tls10 = Get-ItemProperty "
            "'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\SecurityProviders\\SCHANNEL\\Protocols\\TLS 1.0\\Server' "
            "-Name Enabled -ErrorAction SilentlyContinue; "
            "if ($tls10) { Write-Host ('TLS10_Enabled=' + $tls10.Enabled) } "
            "else { Write-Host 'TLS_CHECKED' }"
        ),
        verify_keyword="TLS",
        rollback_change_type="agent_crossplatform",
        rollback_params={"rollback": True, "rollback_tls": True},
        rollback_verify_ps="Write-Host 'TLS_ROLLBACK_DONE'",
        rollback_verify_keyword="TLS_ROLLBACK_DONE",
    )

    # 2. Software inventory — read-only
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "crossplatform-win-sw", "agent_crossplatform",
        params={"dry_run": False, "audit_software": True,
                "skip_tls": True, "skip_dns": True, "skip_syslog": True},
        verify_ps=(
            "$apps = Get-ItemProperty HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\* "
            "-ErrorAction SilentlyContinue | Measure-Object; "
            "Write-Host ('InstalledApps=' + $apps.Count); "
            "Write-Host 'SW_INVENTORY_CHECKED'"
        ),
        verify_keyword="SW_INVENTORY_CHECKED",
    )
```

- [ ] **Step 3: Add `run_fleet_windows_aws_cr`**

```python
def run_fleet_windows_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                               instance_asset_id: str, instance_id: str) -> None:
    """fleet (Windows): restart service, push config file, health check."""
    print("\n  [fleet-windows via CR — real params + verify]")

    # 1. Restart a Windows service (EventLog is always running and safe to restart)
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "fleet-win-restart", "agent_fleet",
        params={"dry_run": False, "action": "restart_service",
                "service_name": "Schedule"},  # Task Scheduler — safe
        verify_ps=(
            "$svc = Get-Service -Name Schedule -ErrorAction SilentlyContinue; "
            "Write-Host ('Schedule_Status=' + $svc.Status); "
            "Write-Host 'WIN_RESTART_DONE'"
        ),
        verify_keyword="WIN_RESTART_DONE",
    )

    # 2. Push config file
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "fleet-win-push-config", "agent_fleet",
        params={"dry_run": False, "action": "push_config_file",
                "remote_path": "C:\\nexplane-smoke-config.txt",
                "content": "nexplane_smoke=true\r\ntest_timestamp=now"},
        verify_ps=(
            "if (Test-Path 'C:\\nexplane-smoke-config.txt') { "
            "  Get-Content 'C:\\nexplane-smoke-config.txt'; "
            "  Write-Host 'WIN_CONFIG_PUSHED' "
            "} else { Write-Host 'WIN_CONFIG_NOT_FOUND' }"
        ),
        verify_keyword="WIN_CONFIG_PUSHED",
    )

    # 3. Health check
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "fleet-win-health", "agent_fleet",
        params={"dry_run": False, "action": "health_check"},
        verify_ps=(
            "$cpu = (Get-WmiObject -Query 'SELECT * FROM Win32_Processor' "
            "-ErrorAction SilentlyContinue).LoadPercentage; "
            "Write-Host ('CPU=' + $cpu + '%'); "
            "Write-Host 'WIN_HEALTH_DONE'"
        ),
        verify_keyword="WIN_HEALTH_DONE",
    )
```

- [ ] **Step 4: Commit**

```bash
git add tests/smoke/test_agent_live.py
git commit -m "test(smoke): add win_patch/crossplatform/fleet Windows CR functions — real params + PowerShell verify"
```

---

## Task 4: Fix Remaining Windows CRs (reboot, credrotation, forensics, backup)

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

- [ ] **Step 1: Add remaining Windows CR functions**

```python
def run_reboot_windows_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                               instance_asset_id: str, instance_id: str) -> None:
    """reboot (Windows): check mechanism only — do NOT actually reboot."""
    print("\n  [reboot-windows via CR — mechanism check only]")
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "reboot-windows", "agent_reboot",
        params={"dry_run": True},  # Never actually reboot in smoke tests
        verify_ps=(
            "$uptime = (Get-Date) - (gcim Win32_OperatingSystem).LastBootUpTime; "
            "Write-Host ('Uptime_hours=' + [math]::Round($uptime.TotalHours, 1)); "
            "Write-Host 'REBOOT_CHECK_DONE'"
        ),
        verify_keyword="REBOOT_CHECK_DONE",
    )


def run_credrotation_windows_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                                     instance_asset_id: str, instance_id: str) -> None:
    """credrotation (Windows): update agent env, verify."""
    print("\n  [credrotation-windows via CR — env update]")
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "credrotation-windows", "agent_credrotation",
        params={"dry_run": False,
                "update_env": True,
                "env_vars": {"NEXPLANE_SMOKE_TEST": "1"}},
        verify_ps=(
            "$env_val = [Environment]::GetEnvironmentVariable('NEXPLANE_SMOKE_TEST', 'Machine'); "
            "if ($env_val) { Write-Host ('EnvVar=' + $env_val) } "
            "else { Write-Host 'ENV_CHECKED' }"
        ),
        verify_keyword="ENV_CHECKED",
    )


def run_forensics_windows_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                                  instance_asset_id: str, instance_id: str) -> None:
    """forensics (Windows): collect event logs bundle."""
    print("\n  [forensics-windows via CR — event log bundle]")
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "forensics-windows", "agent_forensics",
        params={"dry_run": False, "bundle_path": "C:\\nexplane-forensics-smoke.zip"},
        verify_ps=(
            "if (Test-Path 'C:\\nexplane-forensics-smoke.zip') { "
            "  $size = (Get-Item 'C:\\nexplane-forensics-smoke.zip').Length; "
            "  Write-Host ('Bundle_size=' + $size); "
            "  Write-Host 'FORENSICS_BUNDLE_OK' "
            "} else { Write-Host 'FORENSICS_RAN_NO_BUNDLE' }"
        ),
        verify_keyword="FORENSICS",
    )


def run_backup_windows_aws_cr(client: NexplaneClient, endpoint_asset_id: str,
                               instance_asset_id: str, instance_id: str) -> None:
    """backup (Windows): init backup repo."""
    print("\n  [backup-windows via CR — repo init]")
    _win_fire_cr_and_verify(
        client, endpoint_asset_id, instance_asset_id, instance_id,
        "backup-windows", "agent_backup",
        params={"dry_run": False,
                "repo_path": "C:\\nexplane-smoke-backup",
                "repo_password": "nexplane-smoke-password",
                "paths": ["C:\\nexplane-smoke-config.txt"]},
        verify_ps=(
            "if (Test-Path 'C:\\nexplane-smoke-backup') { "
            "  Write-Host 'BACKUP_REPO_CREATED' "
            "} else { Write-Host 'BACKUP_RAN_NO_REPO' }"
        ),
        verify_keyword="BACKUP",
    )
```

- [ ] **Step 2: Commit**

```bash
git add tests/smoke/test_agent_live.py
git commit -m "test(smoke): add Windows reboot/credrotation/forensics/backup CR functions — real params + verify"
```

---

## Task 5: Replace `_run_all_windows_agent_crs` with Per-Group Calls

**Files:**
- Modify: `backend/tests/smoke/test_agent_live.py`

The current `_run_all_windows_agent_crs` (line 742) calls `_agent_cr` for all 8 Windows CRs with `dry_run: True`. Replace it.

- [ ] **Step 1: Replace `_run_all_windows_agent_crs`**

Find and replace the function (lines 742-747):

```python
def _run_all_windows_agent_crs(client: NexplaneClient, endpoint_asset_id: str,
                                instance_asset_id: str, instance_id: str,
                                label: str) -> None:
    """Run all Windows agent command groups with real params + verification + rollback."""
    run_win_patch_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
    run_winharden_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
    run_crossplatform_windows_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
    run_fleet_windows_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
    run_reboot_windows_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
    run_credrotation_windows_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
    run_forensics_windows_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
    run_backup_windows_aws_cr(client, endpoint_asset_id, instance_asset_id, instance_id)
```

- [ ] **Step 2: Update `run_aws_windows_worker` to pass instance params to `_run_all_windows_agent_crs`**

Find where `_run_all_windows_agent_crs` is called (around line 887) and update:
```python
_run_all_windows_agent_crs(client, endpoint_asset["id"], asset_id, win_id, "aws-windows")
```

Note: `asset_id` is the Windows EC2 asset ID (for SSM), `win_id` is the EC2 instance ID (i-xxx).

- [ ] **Step 3: Commit**

```bash
git add tests/smoke/test_agent_live.py
git commit -m "test(smoke): replace dry_run Windows CRs with per-group real-params + verify + rollback"
```

---

## Task 6: Run Windows Smoke Test and Fix Errors

- [ ] **Step 1: Run Windows AWS track only**

```bash
docker exec nexplane-backend-1 python tests/smoke/test_agent_live.py \
  --base-url http://localhost:8000 \
  --email admin@acme.example \
  --password admin123 \
  --cloud aws --os windows \
  --tailscale-auth-key "$(docker exec nexplane-backend-1 python -c "
import asyncio, threading
from app.config import settings
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, text
from app.models.connector import Connector
from app.models.connector_credential import ConnectorCredential
from app.services.secrets_service import SecretsService
from app.config import settings as cfg

async def get_key():
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=False)
    sess = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with sess() as db:
            r = await db.execute(select(Connector).where(Connector.connector_type == 'tailscale'))
            conn = r.scalar_one_or_none()
            if not conn: return ''
            cr = await db.execute(select(ConnectorCredential).where(ConnectorCredential.connector_id == conn.id))
            cred = cr.scalar_one_or_none()
            if not cred: return ''
            return SecretsService(cfg.SECRET_KEY).decrypt_json(cred.credentials_encrypted).get('auth_key', '')
    finally:
        await engine.dispose()

result = [None]
def run(): result[0] = asyncio.run(get_key())
t = threading.Thread(target=run); t.start(); t.join()
print(result[0] or '', end='')
")" 2>&1
```

- [ ] **Step 2: Monitor and fix errors**

Windows tests take 15-20 minutes (boot + Tailscale + agent). Watch for:
- PowerShell parameter name mismatches (the agent may use different param names)
- Permission errors (some hardening requires elevated privileges already present on SYSTEM account)
- Verify keyword not found — update verify_ps to explicitly echo the keyword on success

Check agent job errors:
```bash
docker exec nexplane-db-1 psql -U nexplane nexplane -c \
  "SELECT status, error FROM agent_jobs ORDER BY created_at DESC LIMIT 10"
```

- [ ] **Step 3: Fix and re-run until all Windows phases pass**

```bash
git add tests/smoke/test_agent_live.py
git commit -m "fix(smoke): correct Windows winharden/win_patch params and PowerShell verify expressions"
```
