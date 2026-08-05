# Proxmox VE Rolling Upgrade Design

**Goal:** Upgrade a Proxmox VE cluster from one major/minor version to the next by running `pveupgrade` on each node sequentially, migrating workloads off before each node's reboot and maintaining corosync quorum throughout.

**Architecture:** Proxmox VE uses corosync for cluster membership — a 3-node cluster requires 2 nodes for quorum, so exactly one node may be offline at a time. The CR enforces this invariant: it never begins upgrading a second node until the first has rejoined the cluster and quorum is re-established. Each node upgrade is driven over SSH using `DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade` preceded by `pveupgrade` preflight checks; the Proxmox REST API handles VM/CT migration and cluster status polling. Storage backend detection (ZFS, Ceph, LVM) adjusts preflight checks and informs rollback options.

## Phases

1. **Preflight** — `GET /api2/json/cluster/status` to confirm quorum (`quorate: 1`) and all nodes online; `GET /api2/json/nodes` to enumerate cluster members; verify each node's current Proxmox version via `pveversion -v` over SSH; dry-run the upgrade: `apt-get -s dist-upgrade | grep -i proxmox` to list packages that will change; confirm corosync ring health: `corosync-cfgtool -s` shows all rings active; if Ceph is configured, `ceph -s` must show `HEALTH_OK`; verify `migrate_vms_before_upgrade: true` nodes have at least one migration-capable destination node; check subscription status if using Enterprise repo.

2. **Snapshot** — Per cluster: `pvecm status` to file; `pveversion -v` to file; `cat /etc/pve/corosync.conf` to file; `tar czf /tmp/pve-configs-<timestamp>.tar.gz /etc/pve/` (includes VM/CT configs, storage config, network config). Per node: `qm list` and `pct list` to record running VMs and CTs with their current node; if Ceph: `ceph -s` and `ceph osd tree` to file; record current Proxmox package versions: `dpkg -l | grep proxmox` to file (enables targeted package-level rollback). All snapshot files written to a persistent path on the Proxmox node and also pulled to the executor for safekeeping.

3. **Upgrade** — For each node in sequence: **(1) Migrate workloads off:** for each running VM, `POST /api2/json/nodes/<node>/qemu/<vmid>/migrate` with `{target: <target_node>, online: 1}`; poll migration task until complete within `vm_migration_timeout_seconds`; for VMs that cannot live-migrate: `POST /api2/json/nodes/<node>/qemu/<vmid>/status/shutdown`; for CTs: `pct migrate <ctid> <target_node> --online 1`; track all migrated VMs/CTs for optional restoration later. **(2) Upgrade node:** SSH to node; `apt-get update`; `DEBIAN_FRONTEND=noninteractive pveupgrade` (or `apt-get dist-upgrade -y` if `pveupgrade` is non-interactive); handle any held-back packages explicitly. **(3) Reboot:** `reboot`; poll `GET /api2/json/nodes/<node>/status` until online within `node_boot_timeout_seconds`. **(4) Quorum check:** `GET /api2/json/cluster/status` must show all remaining nodes in quorum before proceeding to next node. **(5) Optional VM restore:** if `restore_vms_after_upgrade: true`, migrate tracked VMs/CTs back to the upgraded node.

4. **Verify** — `pveversion -v` on all nodes shows target version string; `pvecm status` shows quorum established with all expected nodes; `GET /api2/json/cluster/status` all nodes online; corosync ring check passes; `pvecm updatecerts` run if the upgrade spans a version boundary with certificate changes; all VMs that were running pre-upgrade are running on some node in the cluster; if Ceph: `ceph -s` returns `HEALTH_OK`.

5. **Rollback** — Package-level rollback (if old version in apt cache): SSH to node; `apt-get install proxmox-ve=<previous_version> pve-manager=<previous_version>` — restores Proxmox packages without touching VM data. Full OS rollback (if ZFS is configured on the system volume): `zfs rollback rpool/ROOT/<previous_snapshot>` and reboot. If neither is available: boot from Proxmox rescue ISO, restore `/etc/pve/` configs from snapshot tarball, reinstall the previous Proxmox version. VM data on ZFS/LVM/Ceph volumes is never modified by package upgrade and is unaffected by any rollback path. Rollback is executed in FILO order (last upgraded node first).

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `proxmox_host` | string | yes | — | Any cluster node FQDN/IP for API access |
| `proxmox_user` | string | no | from connector | API user, e.g. `root@pam` |
| `proxmox_token` | string | no | from connector | API token secret |
| `target_version` | string | no | — | Target PVE version; if omitted, upgrades to latest available in configured repo |
| `nodes` | list[string] | no | all nodes | Node names to upgrade; if omitted, upgrades all cluster nodes |
| `upgrade_order` | list[string] | no | — | Explicit upgrade sequence; overrides default ordering |
| `migrate_vms_before_upgrade` | bool | no | `true` | Migrate running VMs/CTs off node before upgrade |
| `restore_vms_after_upgrade` | bool | no | `false` | Migrate VMs/CTs back to node after it rejoins |
| `vm_migration_timeout_seconds` | int | no | `600` | Per-VM/CT migration timeout |
| `node_boot_timeout_seconds` | int | no | `300` | Max wait for node to come back online after reboot |
| `apt_proxy` | string | no | — | HTTP proxy for apt if nodes lack direct internet access |
| `dry_run` | bool | no | `false` | Log all actions without executing |

## Rollback Capability

**PARTIAL.** Package-level rollback is possible if the previous version remains in the apt cache or a local mirror is configured — this is reliable immediately after upgrade but may not be available days later. If ZFS is used on the Proxmox system volume, a pre-upgrade ZFS snapshot enables full OS-level rollback. VM and CT data volumes (ZFS datasets, LVM volumes, Ceph RBD images) are never touched by a Proxmox package upgrade and are safe under all rollback scenarios. The CR records the previous package versions in Snapshot phase to enable targeted reinstall regardless of cache state.

## Smoke Test Requirements

A 3-node Proxmox VE cluster using nested virtualization: EC2 bare metal instance (`metal` family or KVM-enabled instance type) running three Proxmox VE VMs provisioned via Terraform. The smoke run starts at PVE 7.4, upgrades one node at a time to 8.1, verifies quorum is maintained throughout (quorum must show 3/3 before each node upgrade begins), runs a test VM migration between nodes, and confirms the test VM survives the full rolling upgrade. Rollback phase downgrades the last-upgraded node via package reinstall and confirms it rejoins the cluster. AMI caching is recommended for the base 3-node cluster image (`/nexplane/smoke-amis/proxmox/{hash}`) as Proxmox cluster formation takes longer than the 60-second provisioning threshold.

## Key Risks

- **Quorum loss during upgrade.** If a second node goes offline while one is already being upgraded, the cluster loses quorum and VMs freeze. The CR enforces a hard check — `pvecm status` must show all non-upgrading nodes online before any node enters the upgrade sequence; if quorum drops unexpectedly mid-run, the executor halts and raises an alert rather than proceeding.
- **VM cannot be migrated off node.** VMs with local disk (not shared storage), passthrough devices, or broken cluster network will block the migration step and leave the node populated. The CR pre-checks each VM's migration eligibility in preflight and either fails hard or shuts down non-migratable VMs (with explicit operator confirmation via CR parameter) before entering the upgrade loop.
- **Kernel module incompatibility after reboot.** Proxmox upgrades may introduce new kernel versions; if out-of-tree modules (e.g., custom ZFS builds, proprietary drivers) are not compatible, the node may fail to boot or come up degraded. The CR checks `proxmox-default-kernel` and `pve-kernel-*` package versions in preflight and warns if the target upgrade includes a kernel version change, giving the operator a chance to pre-stage compatible modules.
