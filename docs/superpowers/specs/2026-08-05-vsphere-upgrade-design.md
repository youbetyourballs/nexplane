# vSphere / vCenter Rolling Upgrade Design

**Goal:** Upgrade a vSphere environment by first upgrading the vCenter Server Appliance (VCSA) and then rolling through ESXi hosts one at a time, vMotioning workloads off each host before upgrade and restoring quorum before proceeding.

**Architecture:** vSphere upgrades are strictly ordered: VCSA must reach the target version before any ESXi host upgrade begins, because vCenter manages the host remediation workflow. The CR uses the vCenter REST API and VAMI API for VCSA operations and PyVmomi for ESXi host orchestration (maintenance mode, vMotion, VUM remediation). VCSA upgrade is driven by the `vcsa-deploy upgrade` CLI on a jump host and can take 30–90 minutes; ESXi host upgrades proceed node-by-node after VCSA is confirmed healthy. DRS must be enabled on all clusters to allow automated vMotion evacuation.

## Phases

1. **Preflight** — Connect via PyVmomi (`vim.ServiceInstance.RetrieveContent()`); verify vCenter version (`content.about.version`); `GET /api/appliance/system/version` (VAMI) for appliance version; confirm all ESXi hosts have `connectionState == connected`; verify DRS is enabled on all clusters (required for automated vMotion during host maintenance); check vSAN cluster health if vSAN is configured; confirm `backup_destination` is reachable and writable; verify the VCSA ISO path on the jump host exists and matches the target version; check Distributed Switch version compatibility with the target ESXi version.

2. **Snapshot** — Trigger VCSA file-based backup: `POST /api/appliance/recovery/backup/jobs` with the configured `backup_destination` (SFTP/NFS/SMB); poll `GET /api/appliance/recovery/backup/jobs/<id>` until `status == SUCCEEDED`; record each ESXi host's current version and boot bank via SSH (`esxcli system version get`, `esxcli system boot device get`); export cluster DRS and HA settings via PowerCLI `Get-Cluster | Export-Clixml`; record all running VMs and their current host placement.

3. **Upgrade** — **Phase 1 — VCSA:** Mount the VCSA ISO on the jump host; render `upgrade-spec.json` from CR parameters; run `vcsa-deploy upgrade --accept-eula --no-ssl-certificate-verification upgrade-spec.json`; poll upgrade appliance progress log via SSH; the old VCSA is shut down automatically at completion; wait for new VCSA to pass VAMI health check `GET /api/appliance/health/system`. **Phase 2 — ESXi hosts (rolling):** For each host in sequence: (1) `vim.HostSystem.EnterMaintenanceMode(timeout=maintenance_mode_timeout_seconds, evacuatePoweredOffVms=True)` — triggers DRS to vMotion running VMs to other hosts; (2) apply ESXi upgrade baseline via VUM remediation (`esxi_upgrade_method: vum`) or push ESXi ISO via iDRAC/iLO (`esxi_upgrade_method: iso`); (3) reboot host; (4) poll `connectionState` until `connected`; (5) `vim.HostSystem.ExitMaintenanceMode()`; (6) confirm all VMs that vMotioned off have a healthy power state on their new host; (7) proceed to next host.

4. **Verify** — `content.about.version` matches target vCenter version; `GET /api/appliance/system/version` matches; all ESXi hosts are connected and not in maintenance mode; run `GET /api/appliance/health/system` — all subsystems green; confirm all VMs that were running pre-upgrade are running on some host; check vSAN health if applicable; run `vpxd` log scan for ERROR-level entries since upgrade completion.

5. **Rollback** — VCSA rollback: deploy a new VCSA appliance from the file-based backup taken in Snapshot phase (`vcsa-deploy install` from backup manifest); this is time-consuming (30–60 min) and replaces the upgraded appliance. ESXi host rollback: SSH to host; `esxcli system boot change --setactive=<previous_bootbank>`; reboot — host returns to pre-upgrade ESXi version using the inactive boot bank. ESXi rollback is executed in FILO order (last upgraded host first).

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `vcsa_host` | string | yes | — | vCenter FQDN or IP |
| `vcsa_user` | string | no | from connector | vCenter admin username |
| `vcsa_password` | string | no | from connector | vCenter admin password |
| `target_vcsa_version` | string | yes | — | Target vCenter version, e.g. `8.0.2` |
| `vcsa_iso_path` | string | no | — | Path to VCSA ISO on the jump host |
| `esxi_hosts` | list[dict] | no | all hosts | `[{name, moref, ssh_host}]`; if omitted, upgrades all cluster hosts |
| `esxi_target_version` | string | yes | — | Target ESXi version, e.g. `8.0.2` |
| `esxi_upgrade_method` | string | no | `vum` | `vum` (VUM baseline remediation) or `iso` (iDRAC/iLO push) |
| `cluster_names` | list[string] | no | — | Limit upgrade to named clusters |
| `maintenance_mode_timeout_seconds` | int | no | `600` | Per-host timeout for DRS evacuation |
| `backup_destination` | string | yes | — | SFTP/NFS URL for VCSA file-based backup |
| `dry_run` | bool | no | `false` | Log all actions without executing |

## Rollback Capability

**PARTIAL.** ESXi host rollback is **FULL** — the inactive boot bank preserves the previous ESXi version and the host can be restored with a single `esxcli` command and reboot. VCSA rollback is **time-consuming but possible** — the file-based backup taken in Snapshot phase can be used to deploy a replacement VCSA appliance, but this takes 30–60 minutes and requires the backup destination to be accessible. VM data (VMDK files on datastores) is never modified by this CR; only the hypervisor and management plane binaries change.

## Smoke Test Requirements

Full vSphere infrastructure is expensive and slow to provision for automated smoke testing. The smoke strategy is two-tier: (1) VCSA API operations (preflight, backup trigger, health checks, version queries) are validated against VMware's `vcsim` vCenter simulator, which exposes the full vCenter REST API surface; (2) ESXi host upgrade lifecycle (maintenance mode, boot bank swap, reconnect) is validated against a real ESXi host in the Nexplane lab environment. The combined smoke run exercises the full CR state machine — plan, approval gate, VCSA phase, host phase, verify, and rollback — with vcsim substituted only where a real VCSA would be destructively modified.

## Key Risks

- **VCSA upgrade failure mid-flight.** The `vcsa-deploy upgrade` process is not atomic — if it fails partway through, both old and new VCSA may be in inconsistent states. The CR monitors upgrade appliance logs and triggers the backup-restore rollback path immediately on any non-zero exit code, before the old VCSA is shut down if possible.
- **DRS vMotion fails for a VM.** Some VMs (e.g., with local storage, USB passthrough, or anti-affinity rules) cannot be live-migrated. The CR pre-identifies non-migratable VMs in preflight and either skips the host (if `skip_non_migratable: true`) or fails preflight hard, preventing a host from entering maintenance mode with stranded powered-on VMs.
- **Distributed Switch version incompatibility.** Upgrading ESXi hosts to a version that requires a newer VDS version can cause network disruption for VMs using the distributed switch. The CR checks VDS version compatibility in preflight and halts if the current VDS version is below the minimum required by the target ESXi version.
