# Windows Parallel Migration — Design Spec

## Goal

Migrate a live Windows Server instance to a new instance at a higher OS version with full inventory transfer, EIP/DNS/ENI cutover, and 24-hour rollback window (source stopped, not terminated).

## Architecture

Agent-first pattern mirroring `linux_parallel_upgrade`. Platform deploys the Nexplane agent on both source and dest instances. Source agent drives inventory and robocopy sync. Platform executor drives cutover and rollback. Six phases: preflight → inventory → sync → health check → cutover → decommission.

Decommission is a separate CR action triggered manually after the 24-hour window — not automatic.

## Phases

### 1. Preflight
- Verify agent responds on both source and dest
- Verify cutover resource exists (EIP allocated / Route53 record exists / ENI exists)
- Verify dest is running and reachable
- Verify source OS version < dest OS version

### 2. Inventory
Source agent runs PowerShell to collect:
- **Services** — name, start type, binary path, run-as account
- **Scheduled tasks** — name, trigger, action, run-as account
- **IIS** — sites, app pools, bindings, physical paths, identity
- **Environment variables** — machine scope + user scope
- **Certificates** — store, thumbprint, subject, expiry (exports PFX to staging path)
- **App config files** — web.config, app.config paths + content
- **Registry** — user-specified key paths; defaults to `HKLM\SOFTWARE` excluding `HKLM\SOFTWARE\Microsoft` and `HKLM\SOFTWARE\Windows`
- **DNS records** — forward + reverse entries for source hostname/IP
- **Hostname references** — scan config files, registry values, IIS bindings for strings matching source hostname or source private IP

Manifest stored in `execution_result["inventory"]`.

Hostname hits stored in `execution_result["hostname_refs"]` as a list of:
```json
{"location": "file|registry|iis", "path": "...", "type": "hostname|ip", "value": "..."}
```

Operator reviews hostname hits in CR UI and marks replacements via `desired_outcome["hostname_replacements"]`:
```json
[{"path": "C:\\inetpub\\wwwroot\\web.config", "old": "old-hostname", "new": "new-hostname"}]
```

### 3. Sync
Platform injects an ephemeral Ed25519 keypair: pubkey written to dest via WinRM before sync starts, privkey passed to source agent as a job parameter.

Source agent runs robocopy:
```
robocopy C:\ \\dest-ip\C$ /MIR /COPYALL /R:3 /W:5
  /XD "Windows" "Program Files" "Program Files (x86)" "ProgramData\Microsoft"
  /LOG:C:\nexplane-sync.log
```

After robocopy completes, source agent applies `hostname_replacements` on dest:
- Rewrites matched file content
- Rewrites matched registry values
- Updates IIS bindings

Services are **not started** on dest until cutover.

Sync is re-runnable — operator can trigger a re-sync from the CR if the cutover window is delayed.

### 4. Health Check
Dest agent verifies:
- Dest agent responds and IIS sites are configured
- Replaced hostname strings are no longer present in patched files
- Robocopy log shows zero errors

### 5. Cutover
Three mechanisms selected by `desired_outcome["cutover_method"]`:

**`eip`** — disassociate EIP from source, associate to dest. Same as Linux LPU.

**`dns`** — update Route53 A record to dest private IP (or public IP). TTL lowered to 60s before cutover, restored to original after.

**`eni`** — detach source's secondary ENI, attach to dest. Dest inherits source's private IP.

After cutover:
- Source is **stopped** (not terminated)
- `execution_result` stores: `source_instance_id`, `dest_instance_id`, `cutover_method`, `cutover_resource_id` (eip_id / hosted_zone_id+record_name / eni_id), `source_stopped_at`, `source_asset_id`, `dest_asset_id`

### 6. Decommission (manual trigger)
Terminates source instance after operator confirms 24-hour window has passed. Separate action on the CR, never automatic.

## Rollback

Reverse cutover mechanism (re-associate EIP / restore DNS / re-attach ENI to source), then start source instance, then verify source agent responds within 300s.

Source must be in `stopped` state for rollback to proceed. If source was terminated (operator error), rollback fails with a clear error.

`rollback_result` initialized as `{"actions": []}` — only adds `"error"` key on actual failure (same pattern as Linux LPU to avoid false `rollback_failed` status).

## CR Parameters

### `desired_outcome`
```json
{
  "source_asset_id": "uuid",
  "dest_asset_id": "uuid",
  "cutover_method": "eip|dns|eni",
  "eip_id": "eipalloc-...",            // required if cutover_method=eip
  "hosted_zone_id": "Z...",            // required if cutover_method=dns
  "dns_record_name": "app.internal",  // required if cutover_method=dns
  "eni_id": "eni-...",                 // required if cutover_method=eni
  "registry_keys": ["HKLM\\SOFTWARE\\MyApp"],  // optional, adds to default scan
  "hostname_replacements": [...]       // set by operator after reviewing inventory
}
```

## Agent Commands (new)

- `winventory` — runs PowerShell inventory on source, returns manifest JSON
- `robocopy_push` — runs robocopy from source to dest over SSH tunnel, returns log summary
- `apply_replacements` — rewrites files/registry/IIS bindings from replacement list on dest

## Data Model

No new tables. `execution_result` carries the full manifest and rollback params. Hostname refs reviewable via existing CR UI (progressive disclosure — refs collapsed by default, expandable).

## Windows Versions in Scope

- Windows Server 2016 → 2019
- Windows Server 2019 → 2022

2012/R2 deferred. Non-Server SKUs deferred.

## Deferred

- OT/ICS immovable machines (license-locked, no agent install path)
- License-locked software requiring vendor reactivation on new hostname
- Domain-joined instances (AD re-join after migration is out of scope for v1)
- Auto-decommission after 24h (always manual trigger)
- Service recreation on dest (services are inventoried but must be registered via `sc create` or NSSM on dest — robocopy does not transfer service registration)
- Scheduled task re-registration (tasks are inventoried but must be recreated via `Register-ScheduledTask` on dest)
- Certificate PFX export and import (certs are inventoried metadata-only; exporting PFX with private keys and importing to dest machine store is deferred)
- Machine environment variable application (env vars are inventoried but not re-applied on dest)

## Smoke Test

Two parametrized runs: **2016→2019** and **2019→2022**.

### Infrastructure per run
- Source EC2 instance (Windows Server 2016 or 2019) with:
  - A test IIS site bound to source hostname
  - A scheduled task
  - A Windows service (NSSM-wrapped test script)
  - `C:\testapp\config.ini` containing the source hostname string
- Dest EC2 instance (Windows Server 2019 or 2022), clean
- EIP allocated and associated to source

### Flow
1. Register both instances as platform assets with `instance_id` in metadata
2. Create CR with `cutover_method: eip`
3. Execute through inventory phase
4. Assert manifest contains services, scheduled task, IIS site, hostname ref in `C:\testapp\config.ini`
5. Set `hostname_replacements` for the config file
6. Execute through cutover
7. Assert EIP on dest, source stopped
8. Assert `C:\testapp\config.ini` on dest has new hostname (not old)
9. Assert IIS site present on dest
10. Rollback
11. Assert EIP back on source, source running, source agent responds
12. Teardown — release EIP, terminate both instances

### Test file
`tests/smoke/test_windows_parallel_migration_smoke.py`
