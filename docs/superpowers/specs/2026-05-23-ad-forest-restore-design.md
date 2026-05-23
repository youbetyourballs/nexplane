# AD Forest Restore — Implementation Design

> **For agentic workers:** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task.

**Date:** 2026-05-23
**Status:** Approved for implementation

---

## Strategic Context

Existing backup tools (Veeam, Windows Server Backup) restore a compromised state — if ransomware has been in the domain for days, a backup from last night restores last night's infection. The AD forest restore capability separates **clean infrastructure** (a known-good Windows Server AMI promoted from a pre-incident snapshot) from **trusted data** (ntds.dit + SYSVOL captured before compromise). This fills the WannaCry recovery gap: no current product models this separation.

---

## Approach: Staged CR Sequence (Option B)

Three independent CRs executed in operator-approved sequence:

1. **`ad_forest_snapshot`** (existing, enhanced) — capture IFM media to S3
2. **`ad_forest_restore`** (existing, fixed) — promote a clean DC from IFM, verify, cut over DNS
3. **`ad_dc_decommission`** (new) — terminate compromised DCs via EC2 / WinRM / iLO stub

Each CR has its own approval gate, rollback, and audit trail. Fits the existing CR model exactly.

---

## Section 1: Snapshot Changes (IFM-Primary)

### What changes

The existing snapshot stops NTDS, VSS-copies ntds.dit, then separately zips SYSVOL and backs up GPOs. The new approach uses `ntdsutil ifm create full`, which handles VSS internally — zero NTDS downtime.

### New snapshot sequence

1. **Run `ntdsutil ifm create full C:\Temp\NexplaneIFM`** while NTDS is running. ntdsutil takes its own VSS snapshot. Output structure:
   ```
   C:\Temp\NexplaneIFM\
     Active Directory\
       ntds.dit
       ntdsinteg.chk
     Registry\
       SYSTEM          ← Boot Key; required to decrypt ntds.dit
     SYSVOL\
       domain\         ← full SYSVOL tree
   ```
2. **GPO backup separately** via `Backup-GPO -All` — kept as standalone artifact; IFM's SYSVOL content is not in the GPO backup XML format needed for individual GPO restore.
3. **Zip IFM directory** in-memory on the DC, upload to S3 as `{prefix}/IFM.zip`.
4. **Upload `GPO-backup.zip`** to S3 as before.
5. **Write manifest** at `{prefix}/manifest.json`:
   ```json
   {
     "format": "ifm",
     "snapshot_id": "ad-snapshot-20260523T120000Z",
     "snapshot_timestamp": "20260523T120000Z",
     "dc_hostname": "dc1.corp.local",
     "domain_name": "corp.local",
     "artifacts": ["IFM.zip", "GPO-backup.zip"],
     "artifact_sizes_bytes": { "IFM.zip": 524288000, "GPO-backup.zip": 4096000 },
     "ad_ds_downtime_seconds": 0
   }
   ```

### Legacy snapshot compatibility

Snapshots in the old format (`format: "legacy"`, or no manifest) cannot be used with `ad_forest_restore`. The restore executor rejects them immediately:

> *"Snapshot format is 'legacy' — re-run ad_forest_snapshot to create an IFM snapshot before restoring. No offline conversion is possible: the Registry/SYSTEM hive (containing the Boot Key required to decrypt ntds.dit) was not captured in the legacy format."*

The Boot Key is stored in `Registry/SYSTEM`. Without it, `Install-ADDSDomainController -InstallationMediaPath` cannot decrypt the database. Conversion requires a live DC connection — which is just running a new snapshot.

### Result changes

- `ad_ds_downtime_seconds` is now always `0` (no NTDS stop needed)
- `artifacts` now includes `IFM.zip` instead of `ntds.dit` and `SYSVOL.zip`
- `manifest_s3_key` added to result for restore executor to reference

---

## Section 2: Restore Executor

### Key fix

The current executor uses `Install-ADDSDomainController` without `-InstallationMediaPath`. That cmdlet replicates from an existing online DC. The pre-flight check requires all existing DCs to be isolated — making promotion impossible. IFM-based promotion works whether other DCs are online or not.

### Parameters

| Parameter | Required | Default | Description |
|-----------|----------|---------|-------------|
| `target_hostname` | yes | — | Clean Windows Server to promote |
| `winrm_username` | yes | — | WinRM credentials for target |
| `winrm_password` | yes | — | |
| `winrm_port` | no | `5985` | |
| `winrm_use_ssl` | no | `false` | |
| `snapshot_s3_prefix` | yes | — | S3 prefix containing manifest.json |
| `s3_bucket` | yes | — | S3 bucket |
| `domain_name` | no | from manifest | Override domain name |
| `safe_mode_password` | yes | — | DSRM password for new DC |
| `require_dc_isolation` | no | `false` | Abort if any existing DC is reachable |
| `existing_dc_hostnames` | no | `[]` | Hosts to check for isolation |
| `dns_update_mode` | no | `manual` | `route53` \| `azure` \| `manual` |
| `route53_zone_id` | no | — | Required if `dns_update_mode=route53` |
| `azure_zone_name` | no | — | Required if `dns_update_mode=azure` |
| `azure_resource_group` | no | — | Required if `dns_update_mode=azure` |
| `aws_region` | no | `us-east-1` | |

### Execution sequence

**Step 1 — Verify manifest.**
Download `{snapshot_s3_prefix}/manifest.json`. Confirm `format: "ifm"`. Fail with the legacy error message if not. Extract `domain_name` from manifest as default.

**Step 2 — Pre-flight isolation check.**
If `require_dc_isolation: true`: verify every host in `existing_dc_hostnames` is WinRM-unreachable. Abort if any responds. If `require_dc_isolation: false` (partial scenario — some DCs still online): log warning and proceed. IFM promotion works in both cases.

**Step 3 — Connect to target; confirm clean.**
WinRM connect to `target_hostname`. Confirm AD DS not installed (`(Get-WindowsFeature AD-Domain-Services).InstallState != "Installed"`). Confirm Windows Server edition (not Desktop). Fail fast if either check fails.

**Step 4 — Install AD DS role.**
`Install-WindowsFeature -Name AD-Domain-Services -IncludeManagementTools -ErrorAction Stop`

**Step 5 — Download and extract IFM.**
Generate 15-minute presigned URL for `{prefix}/IFM.zip`. Download to `C:\Temp\NexplaneIFM.zip` via `Invoke-WebRequest`. Unzip to `C:\Temp\NexplaneIFM`. Verify expected subdirectories exist (`Active Directory`, `Registry`, `SYSVOL`).

**Step 6 — Promote via IFM.**
```powershell
Import-Module ADDSDeployment
$secPwd = ConvertTo-SecureString $SafeModePassword -AsPlainText -Force
Install-ADDSDomainController `
    -DomainName $DomainName `
    -InstallationMediaPath "C:\Temp\NexplaneIFM" `
    -SafeModeAdministratorPassword $secPwd `
    -InstallDns:$true `
    -NoRebootOnCompletion:$false `
    -Force:$true
```
WinRM drops when the reboot fires — expected. Catch transport errors, wait for host to go offline (up to 5 min), then wait for WinRM to return (up to 15 min).

**Step 7 — Post-reboot verification.**
Reconnect. Check:
- NTDS service status = `Running`
- `(Get-ADDomain).DNSRoot` matches `domain_name`
- `nltest /dsgetdc:$DomainName` succeeds
- `Get-SmbShare -Name SYSVOL` exists (SYSVOL share up)

If SYSVOL share is absent within 2 minutes of reconnect: set `sysvol_status: "pending"` (non-fatal warning — DFSR can take a few minutes to finish initialising from IFM content). All other checks are fatal.

**Step 8 — DNS cutover.**
Route53: upsert A record `domain_name → new_dc_ip`, TTL 60.
Azure: PUT to Azure DNS REST API.
Manual: return `dns_instructions` string in result.

**Step 9 — Inline integrity check.**
Import and call `dc_integrity_check.execute()` directly. Include full output under `post_restore_integrity` in the CR result. This gives the operator a single CR result with complete verification.

### Rollback

`Uninstall-ADDSDomainController -LocalAdministratorPassword $secPwd -Force -NoRebootOnCompletion:$false`. Clean up `C:\Temp\NexplaneIFM` and `C:\Temp\NexplaneIFM.zip`. If promotion never completed (WinRM still responsive before reboot), the server is already clean — rollback succeeds trivially.

---

## Section 3: Decommission Executor (New)

### Change type: `ad_dc_decommission`

Runs after the restore is verified. Takes a list of compromised DCs, handles each by type, processes all concurrently.

### Input

```json
{
  "compromised_dcs": [
    {"type": "ec2",     "instance_id": "i-0abc123",         "name": "dc1"},
    {"type": "winrm",   "winrm_hostname": "dc2.corp.local",  "name": "dc2"},
    {"type": "ilodrac", "hostname": "dc3.corp.local",         "name": "dc3",
     "pam_path": "secret/dc-hw/dc3"}
  ],
  "winrm_username": "Administrator",
  "winrm_password": "...",
  "winrm_port": 5985
}
```

`winrm_username` / `winrm_password` apply to all `winrm`-type entries. Falls back to connector record credentials if not provided in parameters. Not used for `ec2` or `ilodrac` entries.

### Per-type handling

**`ec2`** — `ec2.terminate_instances(InstanceIds=[instance_id])` using the platform's AWS connector credentials. Non-fatal if already terminated.
Result: `{"status": "terminated", "instance_id": "i-0abc123"}`

**`winrm`** — Two-step: block inbound domain traffic first, then shut down.
```powershell
# Step 1: Isolate
New-NetFirewallRule -DisplayName "NexplaneIsolate" -Direction Inbound -Action Block -Protocol TCP

# Step 2: Shut down
Stop-Computer -Force
```
WinRM drop during shutdown is expected — treated as success.
Result: `{"status": "shutdown", "winrm_hostname": "dc2.corp.local"}`

**`ilodrac`** — Stub. Returns:
```json
{
  "status": "not_implemented",
  "hostname": "dc3.corp.local",
  "pam_path": "secret/dc-hw/dc3",
  "reason": "iLO/iDRAC hardware power control requires the oob_management connector (backlog). Manually power off this host via your OOB management interface. Credential path when implemented: secret/dc-hw/dc3"
}
```
The CR does not fail on iLO/iDRAC entries — they are logged as warnings.

### CR completion logic

- `completed` if at least one DC entry was successfully handled (terminated or shutdown); iLO/iDRAC stub entries appear in `result.warnings[]` but do not affect CR status
- `failed` only if every entry errored (no EC2 or WinRM entry succeeded)

### Rollback

Not applicable. The rollback response explicitly states: *"DC decommission is not reversible. If an EC2 instance was incorrectly terminated, restore from its most recent AMI or EBS snapshot. For WinRM-shutdown hosts, power them on manually."* The CR result includes instance IDs and hostnames to assist recovery.

---

## Section 4: Smoke Test (`AD_DC_RESTORE` phase)

### Setup

Two EC2 instances:
- **Source DC** — launched from the existing AD_DC cached AMI (Windows Server 2022 with AD DS). This simulates the "compromised" DC.
- **Target** — clean Windows Server 2022 base AMI, no AD DS. WinRM enabled via SSM `AWS-RunPowerShellScript` (`Enable-PSRemoting -Force` + firewall rule for port 5985). No AMI caching needed.

### Sequence

1. Launch source DC from cached AMI, wait for SSM ready
2. Launch target from base AMI, enable WinRM via SSM
3. **Snapshot CR** — `ad_forest_snapshot` against source DC; verify `manifest.json` in S3 with `format: "ifm"`
4. **Restore CR** — `ad_forest_restore` against target; `require_dc_isolation: false`, `dns_update_mode: "manual"`
5. Verify: dc_integrity_check against target confirms NTDS running, SYSVOL shared, domain DNS root correct
6. **Decommission CR** — `ad_dc_decommission` with one `ec2` entry pointing at source DC instance ID
7. Verify: `describe_instances` confirms source DC in `terminated` state
8. Terminate target in `finally` block

### Characteristics

- Estimated run time: ~18 minutes
- Estimated cost: ~$0.12/run (two `t3.medium` Windows, ~18 min)
- Phase name: `AD_DC_RESTORE` — not in default suite (slow + expensive); included in the named connector phases batch
- Source DC AMI already cached — only target provisioning adds time (~5 min for Windows boot + WinRM setup)

---

## Section 5: Hardware Power Control Backlog Entry

> **`oob_management` connector — hardware power control for bare metal**
>
> Required for `ad_dc_decommission` to handle `type: "ilodrac"` entries. Critical for bare metal DC termination in on-prem environments.
>
> **Transports:** iLO REST API (HPE iLO 4/5/6), iDRAC REST API (Dell iDRAC 8/9), IPMI (generic fallback).
> **Actions:** `power_off`, `power_on`, `power_cycle`, `get_power_state`.
>
> **Credentials:** fetched at execution time via the **platform's common secrets manager abstraction** (`secrets_service.get(pam_path)`). The `pam_path` is specified per-DC in the `compromised_dcs` list. No credentials stored in the connector record itself. This establishes the pattern for any connector requiring runtime credential fetch — all go through the same abstraction, not bespoke per-provider code.
>
> **Prerequisite:** external secret store integration (also on backlog) must be built first — it provides the `secrets_service.get(pam_path)` interface backed by Vault, CyberArk, AWS Secrets Manager, etc. The `oob_management` connector has no PAM-provider-specific code.

---

## Files Touched

| File | Action |
|------|--------|
| `backend/app/connectors/executors/active_directory/ad_forest_snapshot.py` | Rewrite: IFM via ntdsutil, manifest.json, zero NTDS downtime |
| `backend/app/connectors/executors/active_directory/ad_forest_restore.py` | Fix: IFM-based promotion, `require_dc_isolation` param, inline integrity check |
| `backend/app/connectors/executors/active_directory/ad_dc_decommission.py` | New: EC2 terminate + WinRM shutdown + iLO stub |
| `backend/app/connectors/catalog/active_directory.json` | Add `ad_dc_decommission` action |
| `backend/app/connectors/change_type_definitions/ad_dc_decommission.json` | New CTD |
| `backend/app/connectors/change_type_definitions/ad_forest_snapshot.json` | Update description |
| `backend/app/models/change_request.py` | Add `ad_dc_decommission` to `ChangeType` enum |
| `backend/tests/smoke/test_aws_live.py` | Add `AD_DC_RESTORE` phase |
