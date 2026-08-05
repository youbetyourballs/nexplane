# Vault Cluster Upgrade Design

**Goal:** Perform a safe rolling upgrade of a HashiCorp Vault cluster by upgrading standby nodes first, triggering a controlled leader step-down, then upgrading the former active node — minimising downtime while preserving all secrets and lease state.

**Architecture:** The CR exploits Vault's active/standby model: standby nodes are upgraded first so that at least one node on the new binary is available to assume leadership before the active node is ever restarted. For Raft-backed clusters, a pre-upgrade Raft snapshot is taken as the rollback anchor; for Shamir-sealed clusters, the CR enters a `pending_action` pause after each node restart to allow operator-provided unseal keys before proceeding. Auto-unseal clusters (AWS KMS, Azure Key Vault, GCP CKMS) proceed without operator intervention. The executor is Python async and drives each node sequentially, emitting structured progress events per node to the CR log.

## Phases

1. **Preflight** — Run `vault status` on all nodes; record sealed/unsealed state, HA mode, and leader address. Run `vault version` on all nodes and record current version. For Raft storage, run `vault operator raft list-peers` and assert all peers are in quorum. Check seal type (`vault status | grep "Seal Type"`) to determine whether the upgrade can proceed autonomously or requires operator unseal coordination. For Vault Enterprise: check license expiry and performance replication status. Verify the snapshot storage path (`snapshot_path`) is writable. Identify the active node dynamically from `vault status -format=json | jq .leader_address` regardless of what `nodes[].is_active` declares.

2. **Snapshot** — On the active node: `vault operator raft snapshot save <snapshot_path>/vault-snapshot-<timestamp>.snap`. Verify the snapshot file exists and is non-zero. Record `vault status` JSON output for all nodes. Back up `/etc/vault.d/vault.hcl` on each node to `<snapshot_path>/vault.hcl.<node>.<timestamp>`. Record the current binary version and its install path on each node. Snapshot metadata (path, timestamp, size, sha256) is stored in the CR execution record for rollback reference.

3. **Upgrade** — Upgrade standby nodes first, then the active node:

   **For each standby node:** Stop the Vault service (`systemctl stop vault`); download and install the target binary via the configured `package_manager` or `binary_url_template`; start the service (`systemctl start vault`). If `seal_type` is `"shamir"`, set CR to `pending_action` and wait for operator to provide unseal keys; if `"auto"`, poll `vault status` until `Sealed: false` within `unseal_wait_timeout_seconds`. Verify the node rejoins as standby: `vault status | grep "HA Mode: standby"`. Proceed to the next standby only after the current node is confirmed healthy.

   **For the active node:** Issue `vault operator step-down` to trigger leader election; poll `vault status` on this node until `HA Mode: standby` (a just-upgraded standby will have assumed leadership). Then stop, upgrade binary, start, and unseal as above. Verify the node rejoins as standby.

4. **Verify** — Run `vault status` on all nodes and confirm all are unsealed. Run `vault version` on all nodes and assert target version. Run `vault operator raft list-peers` and confirm all nodes are healthy peers. Write a canary secret (`vault kv put secret/upgrade-probe value=ok`) and read it back (`vault kv get secret/upgrade-probe`). Confirm audit backends are still active via `vault audit list`. Delete the canary secret.

5. **Rollback** — For binary rollback: stop Vault on each upgraded node, replace the binary with the version recorded during preflight, start, and unseal. Rollback proceeds in reverse node order (FILO): active-node-equivalent first, then standbys in reverse upgrade order. If data corruption is suspected or a Raft peer fails to rejoin: `vault operator raft snapshot restore <snapshot_path>/vault-snapshot-<timestamp>.snap` on the active node — this restores all Vault data to the pre-upgrade snapshot. Any secrets written after the snapshot was taken will be lost; the CR warns the operator explicitly before executing a snapshot restore.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_version` | string | yes | — | Target Vault version, e.g. `"1.16.2"` |
| `nodes` | list[dict] | yes | — | List of `{name, vault_addr, ssh_host, is_active}`; `is_active` is auto-detected at runtime if omitted |
| `seal_type` | string | no | `"auto"` | `"auto"` for KMS/cloud auto-unseal; `"shamir"` for operator-provided unseal keys |
| `vault_token` | string | no | from connector creds | Root or admin token used for health checks and snapshot operations |
| `package_manager` | string | no | `"apt"` | `"apt"`, `"yum"`, or `"binary"` (direct download and replace) |
| `binary_url_template` | string | no | HashiCorp releases URL | Template string with `{version}` placeholder for binary download |
| `snapshot_path` | string | no | `"/var/backup/vault"` | Directory on each node where Raft snapshots and config backups are written |
| `unseal_wait_timeout_seconds` | int | no | `120` | Max time to wait for a node to auto-unseal after start |
| `raft_peer_timeout_seconds` | int | no | `60` | Max time to wait for a node to rejoin the Raft peer list after start |
| `enterprise` | bool | no | `false` | Enable Enterprise-specific preflight checks (license expiry, performance replication status) |
| `dry_run` | bool | no | `false` | Run all preflight checks, take snapshot, and log upgrade commands without executing them |

## Rollback Capability

**FULL** — binary rollback per node is always possible by replacing the binary and restarting; the CR stores the previous binary path and version during preflight. Raft snapshot restore is available for data recovery but is destructive: any secrets written after the snapshot point are lost, and the CR requires explicit operator confirmation before executing a restore. Shamir-sealed clusters require operator coordination to provide unseal keys after any restart, both during the upgrade and during rollback.

## Smoke Test Requirements

A 3-node Vault cluster on EC2 with Raft integrated storage and AWS KMS auto-unseal. The smoke test upgrades from 1.15.x to 1.16.x, verifies stepdown and leader election produce a healthy new active node on the upgraded binary, confirms secret persistence across the upgrade (canary written pre-upgrade must be readable post-upgrade), and exercises the binary rollback path on one standby node. AMI cache recommended after initial Vault cluster setup due to KMS configuration and Raft join sequence.

## Key Risks

- **Shamir unseal coordination bottleneck:** If `seal_type` is `"shamir"`, every node restart requires operator-provided unseal keys before the upgrade can continue. The CR enters `pending_action` state and surfaces a clear prompt for each node; if the operator does not respond within `unseal_wait_timeout_seconds`, the CR aborts and leaves the node sealed but otherwise unmodified, preventing a silent stall from cascading.
- **Leader election failure after step-down:** If all standby nodes fail to assume leadership after `vault operator step-down` (e.g., due to Raft quorum loss), the cluster may become unavailable. The CR verifies at least one standby is healthy and on the new binary before issuing the step-down, and monitors leadership transfer with a timeout before declaring failure.
- **Snapshot restore data loss:** Restoring the Raft snapshot is the last-resort rollback and destroys all secrets written after the snapshot was taken. The CR guards this action behind an explicit `pending_action` confirmation, displays the snapshot timestamp and estimated data loss window to the operator, and only proceeds on affirmative approval — consistent with the platform's AI-proposes/human-approves contract.
