# FreeIPA Rolling Upgrade Design

**Goal:** Orchestrate a safe, topology-aware upgrade of a multi-master FreeIPA cluster by upgrading replicas first and the CA renewal master last, with full backup and restore rollback capability.

**Architecture:** The CR discovers the replication topology via `ipa-replica-manage` and enforces upgrade order — replicas in any sequence, CA master always last. Each node is upgraded in isolation: package update, `ipa-server-upgrade` or `ipa-replica-upgrade`, then service restart. Replication synchronization is verified between each node upgrade to prevent propagating a half-upgraded state.

## Phases

1. **Preflight** — Run `ipactl status` on all servers and assert all services are UP (krb5kdc, kadmin, named-pkcs11, httpd, pki-tomcatd, dirsrv). Record current version via `ipa --version`. Check replication status with `ipa-replica-manage status` — all agreements must show "In Synchronization"; block if any replica is behind beyond `replication_lag_tolerance_seconds`. Verify `/etc/ipa/default.conf` is consistent across all servers. Confirm `/var` has at least 2 GB free on each node for package downloads and upgrade logs. Auto-detect or validate the CA renewal master via `ipa config-show | grep 'CA renewal master'`.

2. **Snapshot** — Run `ipa-backup` on the CA master to produce a full backup in `backup_path`, capturing LDAP data, the Kerberos database, and PKI state. Record the replication agreement topology via `ipa topologysegment-find` and persist it in the CR execution record. Store the CA master hostname so rollback targeting is unambiguous.

3. **Upgrade** — For each replica (parallel or serial, user-controlled): (1) update packages via `dnf update ipa-server ipa-server-dns`; (2) run `ipa-replica-upgrade`; (3) restart all IPA services with `ipactl restart`; (4) wait up to `service_start_timeout_seconds` for all services to reach running state; (5) verify replication re-synchronizes before proceeding to the next node. After all replicas pass, repeat steps 1–5 on the CA master using `ipa-server-upgrade`.

4. **Verify** — Run `ipactl status` on every server and assert all services are UP. Confirm `ipa --version` reports the target version on all nodes. Validate user directory is intact: `ipa user-find admin` must return a result. Test Kerberos: `kinit admin` succeeds. Confirm replication: `ipa-replica-manage status` shows all agreements "In Synchronization".

5. **Rollback** — Run `ipa-restore <backup_path>/<backup-dir>` on the CA master. This restores LDAP data, the Kerberos database, and PKI state, and reverts packages to the pre-upgrade version. IPA services are stopped during restore — this is a disruptive but complete cluster restore. All replicas must be rolled back in concert; partial rollback of individual replicas is not supported due to replication consistency requirements.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `servers` | list[dict] | Yes | — | List of `{hostname, ssh_host, role}` where role is `master` or `replica` |
| `target_version` | string | No | latest in repo | Package version to upgrade to; omit to use latest available |
| `ca_master` | string | No | auto-detected | Hostname of the CA renewal master; auto-detected via `ipa config-show` if omitted |
| `admin_password` | string | No | from connector | IPA admin password; pulled from connector credentials if not provided |
| `backup_path` | string | No | `/var/lib/ipa/backup` | Directory on the CA master where `ipa-backup` writes its output |
| `replication_lag_tolerance_seconds` | int | No | `60` | Maximum allowed replication lag before blocking a node's upgrade |
| `service_start_timeout_seconds` | int | No | `180` | Seconds to wait for `ipactl restart` to bring all services to running |
| `dry_run` | bool | No | `false` | Run all preflight and snapshot phases but skip package installation and upgrade commands |

## Rollback Capability

**FULL.** `ipa-restore` is a complete cluster restore that reverts LDAP data, the Kerberos database, PKI certificates, and package versions to the exact pre-upgrade snapshot. Rollback is disruptive — IPA services are unavailable during the restore window — but the restore is deterministic and total. No data written after the snapshot is preserved.

## Smoke Test Requirements

A 2-node FreeIPA topology on EC2: one master node and one replica, both running RHEL 8 with IPA 4.9. The smoke test upgrades both nodes to IPA 4.10, verifies all services are healthy post-upgrade on both nodes, confirms `ipa user-find admin` returns a result, and checks that `ipa-replica-manage status` reports "In Synchronization" after the upgrade completes. Rollback phase restores from the `ipa-backup` snapshot and confirms services return to 4.9.

## Key Risks

- **CA master upgraded too early.** If the CA master is upgraded before all replicas, CA renewal authority may become inconsistent across the cluster. Mitigated by enforcing CA master last via topology detection and blocking the master upgrade until all replica verify phases pass.
- **Replication lag blocks indefinitely.** A replica that is persistently behind will cause the CR to block. Mitigated by surfacing the lag in the plan phase with an actionable error and exposing `replication_lag_tolerance_seconds` so operators can set a reasonable threshold rather than waiting forever.
- **`ipa-backup` consumes significant disk space.** Full backups on clusters with large LDAP databases can fill `/var`. Mitigated by the 2 GB disk space preflight check; operators can override `backup_path` to a volume with sufficient capacity.
