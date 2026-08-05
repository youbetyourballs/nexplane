# OpenLDAP Rolling Upgrade Design

**Goal:** Upgrade a provider/consumer OpenLDAP syncrepl topology node-by-node, with schema and overlay compatibility validation before any package is touched, and full LDIF-based rollback regardless of MDB format version changes.

**Architecture:** The CR queries `cn=monitor` to discover topology and contextCSN state, then upgrades consumers before the provider (preserving replication integrity). Each node upgrade follows a stop-upgrade-validate-start sequence. For major version jumps (2.4 to 2.6), the CR runs `slaptest` against the existing `cn=config` before starting services and reports schema conflicts in the plan phase so operators resolve them prior to approval. Overlay compatibility is checked against the target package's available module versions before any node is touched.

## Phases

1. **Preflight** — Confirm slapd is running on all nodes via `slapd -VV 2>&1` and `ldapsearch -H ldapi:// -Y EXTERNAL -b cn=monitor`. Enumerate all loaded overlays via `ldapsearch -H ldapi:// -Y EXTERNAL -b cn=config olcModuleLoad`; if `check_overlays` is true, verify each overlay has a compatible version in the target package set and fail preflight with a named list of incompatible overlays. Check MDB environment health on each node with `mdb_stat -e <mdb_path>`. For major version upgrades, run `slaptest` against `cn=config` and surface schema deprecation warnings as plan-phase findings requiring operator acknowledgment before approval. Verify contextCSN synchronization between provider and all consumers.

2. **Snapshot** — On each node: export `cn=config` via `slapcat -n 0 > <backup_path>/config-<timestamp>.ldif`; export the main DIT via `slapcat -n 1 > <backup_path>/data-<timestamp>.ldif`; copy the MDB environment directory to `<backup_path>/mdb-<timestamp>/`. Record the overlay list, schema file checksums, and current slapd version. Store backup manifest in the CR execution record so rollback targets are unambiguous per node.

3. **Upgrade** — For each consumer node (then the provider): (1) `systemctl stop slapd`; (2) upgrade packages via `apt-get install -y slapd ldap-utils` or `yum update openldap-servers`; (3) run `slaptest -f /etc/ldap/slapd.d` to validate cn=config compatibility with the new binary — abort node upgrade if `slaptest` exits non-zero and surface the error; (4) `systemctl start slapd`; (5) verify slapd is accepting connections via `ldapsearch -H ldapi:// -Y EXTERNAL -b cn=monitor`; (6) confirm contextCSN synchronization with the provider before moving to the next node. Provider is upgraded last only after all consumers pass verification.

4. **Verify** — Confirm `slapd -VV 2>&1` reports the target version on all nodes. Run `ldapsearch -x -b <base_dn>` and assert expected entries are present. Compare contextCSN across all nodes via `ldapsearch -H ldapi:// -Y EXTERNAL -b <base_dn> contextCSN` — all values must match. Confirm all overlays are loaded and functional by querying `cn=config` for `olcModuleLoad` entries.

5. **Rollback** — On each node: `systemctl stop slapd`; downgrade package to pre-upgrade version; if MDB format is incompatible with the downgraded binary (common in 2.4→2.6), restore from LDIF: wipe `<mdb_path>`, then `slapadd -n 0 -l <backup_path>/config-<timestamp>.ldif` followed by `slapadd -n 1 -l <backup_path>/data-<timestamp>.ldif`; `systemctl start slapd`. Rollback consumers first, then provider, to maintain replication consistency during the unwind.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `nodes` | list[dict] | Yes | — | List of `{hostname, ssh_host, role}` where role is `provider` or `consumer` |
| `base_dn` | string | Yes | — | Base DN for the directory, e.g. `dc=example,dc=com` |
| `target_version` | string | No | latest in repo | Package version to upgrade to; omit to use latest available |
| `bind_dn` | string | No | `cn=admin,<base_dn>` | DN used for LDAP queries during verify phase |
| `bind_password` | string | No | from connector | Bind password; pulled from connector credentials if not provided |
| `ldap_port` | int | No | `389` | LDAP port for external connectivity checks |
| `backup_path` | string | No | `/var/backup/ldap` | Directory on each node for LDIF and MDB snapshot output |
| `mdb_path` | string | No | `/var/lib/ldap` | Path to the slapd MDB environment directory |
| `check_overlays` | bool | No | `true` | Verify overlay module compatibility with target package before upgrade begins |
| `dry_run` | bool | No | `false` | Run preflight and snapshot phases only; skip package installation and slapd stop/start |

## Rollback Capability

**FULL.** `slapcat` LDIF exports are format-agnostic — a `slapadd` restore works regardless of MDB format version differences between old and new slapd binaries. Both `cn=config` (database 0) and the main DIT (database 1) are captured separately, so rollback can restore configuration schema and data independently. LDIF-based restore is always possible, making rollback deterministic even across major version boundaries.

## Smoke Test Requirements

A 2-node OpenLDAP deployment on EC2 with syncrepl configured: one provider and one consumer, both running OpenLDAP 2.4 on Ubuntu 20.04 or RHEL 7. The smoke test upgrades both nodes to OpenLDAP 2.6, verifies `ldapsearch -x -b dc=example,dc=com` returns expected entries on both nodes, and confirms contextCSN values match between provider and consumer post-upgrade. Rollback phase restores from LDIF snapshots and verifies replication resumes.

## Key Risks

- **Schema deprecation breaks slapd startup on 2.4→2.6.** OpenLDAP 2.6 removed several legacy attributeTypes. If the existing schema uses them, slapd will refuse to start after upgrade. Mitigated by running `slaptest` in preflight and surfacing all deprecation warnings as plan-phase findings that block approval until the operator acknowledges and resolves them.
- **MDB format incompatibility prevents package downgrade rollback.** Downgrading the slapd binary does not automatically restore the MDB file format, leaving slapd unable to open its database. Mitigated by always capturing a `slapcat` LDIF export in the snapshot phase, so rollback bypasses MDB format issues entirely via `slapadd` restore.
- **Provider upgraded before consumers desynchronize replication.** If the provider is taken offline before consumers have a consistent contextCSN, consumers may diverge permanently. Mitigated by enforcing provider-last upgrade order and verifying contextCSN synchronization after each consumer upgrade before proceeding to the next node.
