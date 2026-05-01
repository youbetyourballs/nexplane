# Agent: Windows Security Hardening — Design Spec (Spec 5c)

**Date:** 2026-05-01
**Status:** Approved
**Scope:** New agent commands for Windows security hardening — authentication & credentials (LAPS, Credential Guard, PowerShell CLM), application control (AppLocker/WDAC, SMB hardening), disk & network (BitLocker, Windows Firewall, TLS protocol hardening, RDP hardening), and audit & monitoring (Windows audit policy, scheduled task audit, registry hardening). All commands are Windows-only. All become catalog actions in `nexplane_agent_mock.json`.

---

## Design Decisions

- **Privilege model:** Agent must run as SYSTEM or local Administrator (requirement A). Each command runs an upfront capability preflight before touching system state.
- **Rollback contract:** Every `Execute` that modifies system state captures a snapshot before changes, stores it in the result under `"snapshot"`, and implements `Rollback` that reads `snapshot` from merged params and restores prior state.
- **Registry snapshots:** Registry changes use `reg export` to capture subtree snapshots and `reg import` to restore. Snapshot content stored as base64-encoded .reg file content in the result.
- **No-restart preference:** Commands use reload/apply mechanisms where possible (Group Policy refresh, service restart vs. host reboot). Where a reboot is required (Credential Guard, BitLocker), the command documents this clearly in safety_notes and returns `reboot_required: true` in the result.
- **Audit-mode-first:** Commands that support audit mode (AppLocker/WDAC) default to audit mode unless `enforce: true` is explicitly set.
- **Read-only ingest commands:** `audit_scheduled_tasks` is read-only. No rollback needed.
- **Catalog integration:** All commands get `nexplane_agent_mock.json` catalog entries and mock executor stubs.
- **Build tags:** All Windows executor files use `//go:build windows` build tags.

---

## Section 1: Authentication & Credentials

### 1.1 `configure_laps`

**Parameters:**
- `action` (string, required) — `enable` or `disable`
- `password_age_days` (int, default 30) — maximum password age in days
- `password_length` (int, default 14) — generated password length
- `admin_account_name` (string, default `Administrator`) — local admin account LAPS manages

**Preflight:**
- Checks caller is SYSTEM or Administrator
- Checks LAPS is installed: `Get-Module -Name LAPS -ListAvailable` or checks for `AdmPwd.dll` / `Microsoft LAPS` feature
- For `enable`: checks the machine is domain-joined

**Execute:**
1. Snapshot: export current LAPS registry settings from `HKLM:\SOFTWARE\Policies\Microsoft Services\AdmPwd` (or Microsoft LAPS equivalent) via `reg export`
2. For `enable`:
   - Set `PasswordAgeDays`, `PasswordLength`, `AdminAccountName` via `Set-LapsADPasswordExpirationTime` or registry keys
   - Enable LAPS: `Set-ItemProperty HKLM:\...\AdmPwd -Name AdmPwdEnabled -Value 1`
   - `gpupdate /force`
3. For `disable`:
   - `Set-ItemProperty HKLM:\...\AdmPwd -Name AdmPwdEnabled -Value 0`
   - `gpupdate /force`

**Result:** `{ action, password_age_days, password_length, admin_account_name, snapshot }`

**Rollback:** Restore registry from `snapshot` (`reg import`); `gpupdate /force`.

**Catalog entry:**
```json
{
  "action_id": "configure_laps",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "workstation"],
  "rollback_action": "configure_laps",
  "safety_notes": [
    "Requires machine to be domain-joined and LAPS schema extensions applied to AD",
    "After enabling, existing local admin password is not immediately rotated — wait for next scheduled rotation or trigger manually"
  ]
}
```

---

### 1.2 `enable_credential_guard`

**Parameters:**
- `action` (string, required) — `enable` or `disable`
- `require_uefi_lock` (bool, default `false`) — if `true`, locks Credential Guard via UEFI variables (survives registry rollback; requires firmware intervention to undo)

**Preflight:**
- Checks caller is SYSTEM or Administrator
- Checks hardware requirements: Secure Boot enabled, UEFI firmware, TPM 2.0 present (`Get-WmiObject -Class Win32_TPM`)
- Checks Windows version ≥ Windows 10 Enterprise / Server 2016

**Execute:**
1. Snapshot: `reg export HKLM\SYSTEM\CurrentControlSet\Control\DeviceGuard` and `reg export HKLM\SOFTWARE\Policies\Microsoft\Windows\DeviceGuard`
2. Set registry keys:
   - `EnableVirtualizationBasedSecurity = 1`
   - `RequirePlatformSecurityFeatures = 1` (Secure Boot)
   - `LsaCfgFlags = 1` (enabled without UEFI lock) or `2` (with UEFI lock)
   - `HypervisorEnforcedCodeIntegrity = 1`
3. Return `reboot_required: true`

**Result:** `{ action, uefi_lock, reboot_required: true, snapshot }`

**Rollback:**
- Restore registry from snapshot
- If `require_uefi_lock` was `false`: rollback takes effect on next reboot
- If `require_uefi_lock` was `true`: warn that UEFI lock prevents registry rollback — firmware intervention required

**Catalog entry:**
```json
{
  "action_id": "enable_credential_guard",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "workstation"],
  "rollback_action": "enable_credential_guard",
  "safety_notes": [
    "Requires reboot to take effect",
    "require_uefi_lock=true makes rollback require firmware intervention — use with caution",
    "Incompatible with some older applications that use NTLM or Kerberos in unsupported ways"
  ]
}
```

---

### 1.3 `enforce_powershell_clm`

**Parameters:**
- `action` (string, required) — `enable` or `disable`
- `mechanism` (string, default `registry`) — `registry` (sets `__PSLockdownPolicy` env var via registry) or `wdac` (applies a WDAC policy that enforces CLM)

**Preflight:**
- Checks caller is SYSTEM or Administrator
- For `mechanism=wdac`: checks WDAC is available (Windows 10/Server 2016+)

**Execute:**
1. Snapshot: `reg export HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment` (for registry mechanism) or export current WDAC policy
2. For `enable` + `registry`: set `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment\__PSLockdownPolicy = 4`
3. For `enable` + `wdac`: generate and apply a WDAC policy XML that enforces CLM via `ConvertFrom-CIPolicy` + `Set-CIPolicyVersion` + `CiTool --update-policy`
4. For `disable`: remove `__PSLockdownPolicy` key or remove WDAC policy

**Result:** `{ action, mechanism, snapshot }`

**Rollback:** Restore from snapshot (registry or WDAC policy).

**Catalog entry:**
```json
{
  "action_id": "enforce_powershell_clm",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "workstation"],
  "rollback_action": "enforce_powershell_clm",
  "safety_notes": [
    "CLM blocks many administration scripts — verify remote management scripts work in CLM before applying to all hosts",
    "WDAC mechanism persists across reboots and survives registry cleanup; registry mechanism is session-bound per user"
  ]
}
```

---

## Section 2: Application Control

### 2.1 `deploy_applocker_policy`

**Parameters:**
- `policy` (string, required) — XML AppLocker policy content (complete `AppLockerPolicy` XML)
- `enforce` (bool, default `false`) — if `false`, all rules set to `AuditOnly`; if `true`, rules set to `Enabled`
- `rule_collection` (string, optional) — apply only to a specific collection: `Exe`, `Msi`, `Script`, `Dll`, `Appx`; omit to apply all

**Preflight:**
- Checks caller is SYSTEM or Administrator
- Checks Application Identity service (`AppIDSvc`) is running or can be started
- Validates policy XML: `[xml]$policy = ...` parse check

**Execute:**
1. Snapshot: `Get-AppLockerPolicy -Effective -Xml` — capture current effective policy as XML
2. If `enforce=false`: replace all `EnforcementMode` attributes in policy XML with `AuditOnly`
3. Apply: `Set-AppLockerPolicy -XmlPolicy {policy_xml}`
4. Ensure `AppIDSvc` is running: `Set-Service AppIDSvc -StartupType Automatic; Start-Service AppIDSvc`

**Result:** `{ enforce, rule_collection, rules_applied_count, snapshot }`

**Rollback:** `Set-AppLockerPolicy -XmlPolicy {snapshot}`; if original was empty, `Set-AppLockerPolicy -XmlPolicy "<AppLockerPolicy Version='1'/>"`

**Catalog entry:**
```json
{
  "action_id": "deploy_applocker_policy",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "workstation"],
  "rollback_action": "deploy_applocker_policy",
  "safety_notes": [
    "Always test in AuditOnly mode first — enforcement can block legitimate applications",
    "AppLocker requires Windows Enterprise or Education edition on workstations"
  ]
}
```

---

### 2.2 `harden_smb`

**Parameters:**
- `disable_smb1` (bool, default `true`) — disable SMBv1 protocol
- `require_signing` (bool, default `true`) — require SMB signing on server and client
- `disable_compression` (bool, default `false`) — disable SMB compression (mitigates CoercedPotato variants)
- `disable_guest_access` (bool, default `true`) — disable guest fallback on SMB client

**Preflight:**
- Checks caller is SYSTEM or Administrator
- Checks `SmbServer` module is available (Windows Server or workstation with feature installed)

**Execute:**
1. Snapshot: `Get-SmbServerConfiguration | Select-Object EnableSMB1Protocol,RequireSecuritySignature,EnableSMBQUIC,EnableGuestAccess` + equivalent client config via registry export of `HKLM\SYSTEM\CurrentControlSet\Services\LanManWorkstation\Parameters`
2. If `disable_smb1`: `Set-SmbServerConfiguration -EnableSMB1Protocol $false -Force`; `Set-SmbClientConfiguration -EnableBandwidthThrottling $false -Force` (client SMB1 already off by default on modern Windows, but explicitly confirm); `Disable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -NoRestart`
3. If `require_signing`: `Set-SmbServerConfiguration -RequireSecuritySignature $true -Force`; `Set-SmbClientConfiguration -RequireSecuritySignature $true -Force`
4. If `disable_compression`: `Set-SmbServerConfiguration -DisableCompression $true -Force`
5. If `disable_guest_access`: `Set-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Services\LanManWorkstation\Parameters -Name AllowInsecureGuestAuth -Value 0`

**Result:** `{ smb1_disabled, signing_required, compression_disabled, guest_disabled, snapshot }`

**Rollback:** Restore each setting from snapshot values; re-enable SMB1 feature if it was enabled before (`Enable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol -NoRestart`).

**Catalog entry:**
```json
{
  "action_id": "harden_smb",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "workstation"],
  "rollback_action": "harden_smb",
  "safety_notes": [
    "Disabling SMB1 breaks connections to legacy devices (old NAS, printers, XP clients) — audit SMB1 connections before disabling",
    "Requiring signing may cause performance degradation on high-throughput file servers"
  ]
}
```

---

## Section 3: Disk & Network

### 3.1 `enable_bitlocker`

**Parameters:**
- `drive_letter` (string, default `C:`) — volume to encrypt
- `protector` (string, default `tpm`) — `tpm`, `tpm_pin`, or `recovery_key_only`
- `pin` (string, optional) — required when `protector=tpm_pin`
- `encryption_method` (string, default `XtsAes256`) — `XtsAes256`, `XtsAes128`, `Aes256`, `Aes128`

**Preflight:**
- Checks caller is SYSTEM or Administrator
- Checks BitLocker feature is available: `Get-WindowsFeature BitLocker` (server) or `Get-BitLockerVolume` cmdlet availability
- Checks TPM is present and ready if `protector=tpm` or `tpm_pin`: `Get-WmiObject -Class Win32_TPM`
- Checks volume is NTFS and not already encrypted: `Get-BitLockerVolume -MountPoint {drive}`

**Execute:**
1. Snapshot: `Get-BitLockerVolume -MountPoint {drive} | Select-Object VolumeStatus,EncryptionMethod,ProtectionStatus` — record current state
2. Add key protector based on `protector`:
   - `tpm`: `Add-BitLockerKeyProtector -MountPoint {drive} -TpmProtector`
   - `tpm_pin`: `Add-BitLockerKeyProtector -MountPoint {drive} -TpmAndPinProtector -Pin (ConvertTo-SecureString {pin} -AsPlainText -Force)`
   - `recovery_key_only`: `Add-BitLockerKeyProtector -MountPoint {drive} -RecoveryPasswordProtector`
3. Always add a recovery password protector: `Add-BitLockerKeyProtector -MountPoint {drive} -RecoveryPasswordProtector` → capture the 48-digit recovery key
4. Enable BitLocker: `Enable-BitLocker -MountPoint {drive} -EncryptionMethod {method} -UsedSpaceOnly`
5. Return recovery key in result (operator must persist this securely)

**Result:** `{ drive, encryption_method, protector, recovery_key, volume_status, snapshot }`

**Rollback:**
- If encryption has not started: `Remove-BitLockerKeyProtector` for all protectors added, then `Disable-BitLocker -MountPoint {drive}`
- If encryption is in progress or complete: BitLocker decryption can be triggered with `Disable-BitLocker -MountPoint {drive}` but this is a long-running operation; return `{ warning: "Decryption triggered but will take time to complete" }`
- Note: Recovery key is not recoverable from snapshot — operator must have saved it from the Execute result

**Catalog entry:**
```json
{
  "action_id": "enable_bitlocker",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "workstation"],
  "rollback_action": "enable_bitlocker",
  "safety_notes": [
    "Save the recovery key from the result immediately — it cannot be recovered later if lost",
    "Rollback after encryption has started requires full decryption which can take hours",
    "Encryption runs in the background; status available via Get-BitLockerVolume"
  ]
}
```

---

### 3.2 `configure_windows_firewall`

**Parameters:**
- `action` (string, required) — `add_rule`, `remove_rule`, or `set_default_action`
- `rule` (map, required for `add_rule`/`remove_rule`):
  - `name` (string) — display name for the rule
  - `direction` (string) — `inbound` or `outbound`
  - `protocol` (string, default `TCP`) — `TCP`, `UDP`, `Any`
  - `local_port` (string, optional) — port or range, e.g. `80` or `8080-8090`
  - `remote_address` (string, optional) — IP, CIDR, or `Any`
  - `action_type` (string) — `Allow` or `Block`
  - `profile` (string, default `Any`) — `Domain`, `Private`, `Public`, `Any`
- `default_action` (map, optional for `set_default_action`):
  - `inbound` (string) — `Block` or `Allow`
  - `outbound` (string) — `Block` or `Allow`

**Preflight:**
- Checks caller is SYSTEM or Administrator
- Checks Windows Firewall service (`MpsSvc`) is running

**Execute:**
1. Snapshot: `netsh advfirewall export "{temp_path}"` — exports full firewall policy to file; store file content (base64) in snapshot
2. For `add_rule`: `New-NetFirewallRule -DisplayName {name} -Direction {dir} -Protocol {proto} -LocalPort {port} -RemoteAddress {addr} -Action {action} -Profile {profile} -Enabled True`
3. For `remove_rule`: `Remove-NetFirewallRule -DisplayName {name}`
4. For `set_default_action`: `Set-NetFirewallProfile -Profile {profiles} -DefaultInboundAction {inbound} -DefaultOutboundAction {outbound}`

**Result:** `{ action, rule_name, snapshot }`

**Rollback:** Write base64-decoded snapshot to temp `.wfw` file; `netsh advfirewall import "{temp_path}"`; delete temp file.

**Catalog entry:**
```json
{
  "action_id": "configure_windows_firewall",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "workstation"],
  "rollback_action": "configure_windows_firewall",
  "safety_notes": [
    "Setting default inbound action to Block without an allow rule for RDP/WinRM will lock out remote management",
    "Firewall changes take effect immediately — test in a break-glass scenario first"
  ]
}
```

---

### 3.3 `harden_tls_protocols`

**Parameters:**
- `disable_protocols` (list of strings, default `["SSL2.0","SSL3.0","TLS1.0","TLS1.1"]`) — protocols to disable
- `enabled_protocols` (list of strings, default `["TLS1.2","TLS1.3"]`) — protocols to ensure enabled
- `cipher_suite_order` (list of strings, optional) — ordered list of cipher suites; omit to use Windows-recommended order
- `apply_to` (list of strings, default `["server","client"]`) — `server`, `client`, or both

**Preflight:**
- Checks caller is SYSTEM or Administrator
- Checks Windows version (TLS 1.3 requires Windows Server 2022 / Windows 11)

**Execute:**
1. Snapshot: `reg export "HKLM\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL"` (captures all protocol and cipher suite settings)
2. For each protocol in `disable_protocols`: set `Enabled = 0` and `DisabledByDefault = 1` under `HKLM\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols\{Protocol}\{Server|Client}`
3. For each protocol in `enabled_protocols`: set `Enabled = 1` and `DisabledByDefault = 0`
4. If `cipher_suite_order` supplied: `Set-ItemProperty HKLM:\SOFTWARE\Policies\Microsoft\Cryptography\Configuration\SSL\00010002 -Name Functions -Value (Join cipher_suite_order ",")`
5. Return `reboot_required: true` (SCHANNEL changes require restart of the service or full reboot to take effect for all processes; `IIS` and `RDP` can be restarted individually)

**Result:** `{ disabled_protocols, enabled_protocols, reboot_required: true, snapshot }`

**Rollback:** `reg import {snapshot_file}`; `reboot_required: true`.

**Catalog entry:**
```json
{
  "action_id": "harden_tls_protocols",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "workstation"],
  "rollback_action": "harden_tls_protocols",
  "safety_notes": [
    "Disabling TLS 1.0/1.1 breaks connections to legacy clients and older .NET applications — audit inbound TLS versions before applying",
    "Requires reboot or restart of affected services (IIS, RDP) to fully take effect"
  ]
}
```

---

### 3.4 `harden_rdp`

**Parameters:**
- `require_nla` (bool, default `true`) — enforce Network Level Authentication
- `encryption_level` (string, default `High`) — `Low`, `Client`, `High`, `FIPS`
- `idle_timeout_minutes` (int, optional) — disconnect idle sessions after N minutes
- `max_session_timeout_minutes` (int, optional) — forcibly disconnect sessions after N minutes
- `port` (int, optional) — change RDP listening port (default 3389); omit to keep existing

**Preflight:**
- Checks caller is SYSTEM or Administrator
- Checks RDP service (`TermService`) is running
- If `port` supplied: checks new port is not already in use

**Execute:**
1. Snapshot: `reg export "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server"` and `reg export "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp"`
2. If `require_nla`: set `HKLM\...\WinStations\RDP-Tcp\UserAuthentication = 1`
3. Set `SecurityLayer = 2` (FIPS-compliant TLS layer) and `MinEncryptionLevel` based on `encryption_level` enum
4. If `idle_timeout_minutes`: set `MaxIdleTime = {n * 60000}` (milliseconds) in `HKLM\...\WinStations\RDP-Tcp`
5. If `max_session_timeout_minutes`: set `MaxConnectionTime` similarly
6. If `port` supplied: set `HKLM\...\WinStations\RDP-Tcp\PortNumber = {port}`; add firewall rule for new port; optionally remove default 3389 rule
7. `Restart-Service TermService`

**Result:** `{ nla, encryption_level, idle_timeout_minutes, port, snapshot }`

**Rollback:** Restore registry from snapshot; `Restart-Service TermService`; if port was changed, restore firewall rules.

**Catalog entry:**
```json
{
  "action_id": "harden_rdp",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "workstation"],
  "rollback_action": "harden_rdp",
  "safety_notes": [
    "Changing RDP port requires updating firewall rules before restarting — this command handles both, but verify VPN/network firewall rules externally",
    "Enabling NLA requires the connecting client to support it — old RDP clients may be locked out"
  ]
}
```

---

## Section 4: Audit & Monitoring

### 4.1 `configure_windows_audit_policy`

**Parameters:**
- `profile` (string, default `cis_level1`) — `cis_level1`, `cis_level2`, `stig`, or `custom`
- `categories` (map, optional) — specific category overrides when `profile=custom` or to override profile defaults:
  - `logon_events` (string) — `success`, `failure`, `both`, `none`
  - `account_logon` (string) — same options
  - `object_access` (string) — same options
  - `privilege_use` (string) — same options
  - `process_creation` (string) — same options
  - `policy_change` (string) — same options
  - `account_management` (string) — same options
  - `directory_service_access` (string) — same options
  - `system_events` (string) — same options

**Profile defaults:**

| Category | CIS Level 1 | CIS Level 2 | STIG |
|----------|-------------|-------------|------|
| Logon events | success+failure | success+failure | success+failure |
| Account logon | success+failure | success+failure | success+failure |
| Object access | failure | success+failure | success+failure |
| Privilege use | failure | success+failure | failure |
| Process creation | success | success | success |
| Policy change | success | success+failure | success+failure |
| Account management | success+failure | success+failure | success+failure |
| Directory service | failure | success+failure | success+failure |
| System events | success+failure | success+failure | success+failure |

**Preflight:**
- Checks caller is SYSTEM or Administrator
- Checks `auditpol` is available

**Execute:**
1. Snapshot: `auditpol /get /category:* /r` — CSV output of all current settings
2. Apply each category via `auditpol /set /subcategory:"{subcategory}" /success:{enable|disable} /failure:{enable|disable}` for each relevant subcategory within the category
3. For `process_creation`: also enable command-line auditing via `reg add HKLM\Software\Microsoft\Windows\CurrentVersion\Policies\System\Audit /v ProcessCreationIncludeCmdLine_Enabled /t REG_DWORD /d 1 /f`

**Result:** `{ profile_applied, categories_configured, snapshot }`

**Rollback:** Parse snapshot CSV and restore each subcategory via `auditpol /set`.

**Catalog entry:**
```json
{
  "action_id": "configure_windows_audit_policy",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "workstation"],
  "rollback_action": "configure_windows_audit_policy",
  "safety_notes": [
    "High-verbosity audit settings (object_access=both) can generate very large event logs — ensure log size limits and SIEM forwarding are configured before applying"
  ]
}
```

---

### 4.2 `audit_scheduled_tasks` (read-only ingest)

No parameters required.

**Preflight:** Checks caller is SYSTEM or Administrator.

**Checks performed:**

| Check | Method | Finding tag |
|-------|--------|-------------|
| All scheduled tasks | `Get-ScheduledTask` | Inventory |
| Non-Microsoft author/publisher tasks | Filter by `TaskInfo.Author` not matching `Microsoft` | `third-party-task` |
| Tasks running as SYSTEM or Administrator | Check `Principal.RunLevel` or `Principal.UserId` | `privileged-task` |
| Tasks with actions pointing to user-writable paths | `icacls` on action executable paths | `writable-task-binary` |
| Tasks disabled by Group Policy but present | `TaskInfo.State = Disabled` with policy source | `gpo-disabled-task` |
| Tasks with no registered author | Missing `TaskInfo.Author` | `unregistered-task` |
| Hidden tasks (not shown in Task Scheduler UI) | Check `Hidden = $true` in task settings | `hidden-task` |

**Result:** Structured list of findings per task. Enriches asset `asset_metadata` with `scheduled_tasks_audit` key. Adds `scheduled-task-findings` tag to asset if any suspicious findings present.

No rollback — read-only.

**Catalog entry:**
```json
{
  "action_id": "audit_scheduled_tasks",
  "action_type": "ingest",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "workstation"]
}
```

---

### 4.3 `harden_registry`

**Parameters:**
- `profile` (string, default `cis_level1`) — `cis_level1`, `cis_level2`, or `custom`
- `settings` (map, optional) — specific settings to enable/disable when `profile=custom` or to override profile defaults:
  - `disable_autorun` (bool, default `true`) — disable AutoRun/AutoPlay for all drives
  - `restrict_anonymous_registry` (bool, default `true`) — prevent anonymous enumeration of registry
  - `enforce_uac_prompt` (bool, default `true`) — ensure UAC prompts for admin elevation (not silently elevate)
  - `disable_lm_hash` (bool, default `true`) — disable LAN Manager hash storage (`NoLMHash`)
  - `disable_ntlmv1` (bool, default `true`) — set LmCompatibilityLevel to 5 (NTLMv2 only)
  - `disable_wdigest` (bool, default `true`) — prevent WDigest from storing cleartext credentials in LSASS
  - `enable_safe_dll_search` (bool, default `true`) — set `SafeDllSearchMode = 1`
  - `disable_print_spooler_remote` (bool, default `true`) — restrict spooler to local-only (PrintNightmare mitigation)

**Profile defaults (CIS Level 1):** all settings above enabled.

**Preflight:**
- Checks caller is SYSTEM or Administrator

**Execute:**
1. Snapshot: `reg export` for each affected registry path listed below
2. Apply each setting:

| Setting | Registry Path | Value |
|---------|--------------|-------|
| `disable_autorun` | `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer\NoDriveTypeAutoRun` | `255` |
| `restrict_anonymous_registry` | `HKLM\SYSTEM\CurrentControlSet\Control\SecurePipeServers\Winreg\AllowedExactPaths\Machine` | empty |
| `enforce_uac_prompt` | `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System\ConsentPromptBehaviorAdmin` | `2` |
| `disable_lm_hash` | `HKLM\SYSTEM\CurrentControlSet\Control\Lsa\NoLMHash` | `1` |
| `disable_ntlmv1` | `HKLM\SYSTEM\CurrentControlSet\Control\Lsa\LmCompatibilityLevel` | `5` |
| `disable_wdigest` | `HKLM\SYSTEM\CurrentControlSet\Control\SecurityProviders\WDigest\UseLogonCredential` | `0` |
| `enable_safe_dll_search` | `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\SafeDllSearchMode` | `1` |
| `disable_print_spooler_remote` | `HKLM\Software\Policies\Microsoft\Windows NT\Printers\RegisterSpoolerRemoteRpcEndPoint` | `2` |

**Result:** `{ profile_applied, settings_applied, snapshot }`

**Rollback:** `reg import {snapshot_file}` for each captured path.

**Catalog entry:**
```json
{
  "action_id": "harden_registry",
  "action_type": "change",
  "execution_tier": 3,
  "applicable_asset_types": ["server", "workstation"],
  "rollback_action": "harden_registry",
  "safety_notes": [
    "disable_ntlmv1 (LmCompatibilityLevel=5) breaks authentication to legacy systems that only support NTLMv1 — audit your environment first",
    "disable_print_spooler_remote breaks remote printing — ensure users print locally or through a print server not this host"
  ]
}
```

---

## Section 5: New Files

| File | Purpose |
|------|---------|
| `agent/commands/winharden/winharden.go` | Package entry: `Execute`/`Rollback` dispatch |
| `agent/commands/winharden/laps_windows.go` | `configure_laps` implementation |
| `agent/commands/winharden/credguard_windows.go` | `enable_credential_guard` implementation |
| `agent/commands/winharden/pscm_windows.go` | `enforce_powershell_clm` implementation |
| `agent/commands/winharden/applocker_windows.go` | `deploy_applocker_policy` implementation |
| `agent/commands/winharden/smb_windows.go` | `harden_smb` implementation |
| `agent/commands/winharden/bitlocker_windows.go` | `enable_bitlocker` implementation |
| `agent/commands/winharden/firewall_windows.go` | `configure_windows_firewall` implementation |
| `agent/commands/winharden/tls_windows.go` | `harden_tls_protocols` implementation |
| `agent/commands/winharden/rdp_windows.go` | `harden_rdp` implementation |
| `agent/commands/winharden/auditpol_windows.go` | `configure_windows_audit_policy` implementation |
| `agent/commands/winharden/tasks_windows.go` | `audit_scheduled_tasks` ingest |
| `agent/commands/winharden/registry_windows.go` | `harden_registry` implementation |
| `agent/commands/winharden/winharden_test.go` | Validation tests (param validation — no build tag needed) |

## Section 6: Modified Files

| File | Change |
|------|--------|
| `agent/executor/executor.go` | Add 13 new commands to `commands` and `rollbacks` maps |
| `backend/app/connectors/catalog/nexplane_agent_mock.json` | Add 13 catalog entries |
| `backend/app/connectors/executors/nexplane_agent_mock/` | Add 13 mock executor stubs |
