# Identity/Auth Infrastructure Upgrades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement upgrade CR types for four identity and authentication infrastructure systems — Keycloak/RHSSO major upgrade, FreeIPA/RHIDM upgrade, OpenLDAP schema migration, and Vault cluster upgrade — all via nexplane_agent with AMI-cached smoke tests.

**Architecture:** All four follow the preflight → snapshot → upgrade → verify → rollback pattern via `dispatch_agent_job`. FreeIPA and Vault already have cached AMIs from prior smoke sessions — reuse those cache keys. Keycloak v16→v17 (WildFly→Quarkus migration) requires export/import; v17+ is in-place `kc.sh build`. Each executor reads current version in preflight and chooses the correct path.

**Tech Stack:** Python asyncio executors, `nexplane_agent._dispatch.dispatch_agent_job`, pytest smoke using `NexplaneClient` + `get_connector_creds_from_db`, boto3 for AMI cache.

## Global Constraints

- All executors in `backend/app/connectors/executors/nexplane_agent/`
- `ROLLBACK_CAPABILITY` declared at module level on every executor
- `desired_outcome` is the only parameter channel
- ChangeType enum + DB migration + change_type_definition JSON + catalog entry required for each CR type
- FreeIPA AMI cached at `/nexplane/smoke-amis/freeipa/7d2480d9` (ami-064887df65fad525f) — reuse it
- Vault AMI cached at `/nexplane/smoke-amis/vault/{hash}` (ami-09df8f5733ff981b3) — reuse it
- Keycloak and OpenLDAP need new AMIs built on first smoke run, then cached
- Follow db_major_version_upgrade.py and k8s_cluster_upgrade.py patterns exactly

---

## CR Type 1: `keycloak_upgrade`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/keycloak_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/keycloak_upgrade.json`
- Modify: `backend/app/models/change_request.py` — add `keycloak_upgrade` after `keycloak_disable_user`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file for DB enum
- Create: `backend/tests/smoke/test_smoke_keycloak_upgrade.py`

**Parameters (all in `desired_outcome`):**
- `source_version`: e.g. `"21.1"` (required)
- `target_version`: e.g. `"24.0"` (required)
- `keycloak_home`: default `/opt/keycloak`
- `db_vendor`: `"postgres"` | `"mysql"` | `"h2"` (default `"postgres"`)
- `db_host`, `db_port`, `db_name`, `db_user`, `db_password`: DB connection for realm export/import
- `admin_user`, `admin_password`: Keycloak admin
- `realms_to_export`: list of realm names to export (default: all realms via `kcadm.sh get realms`)
- `dry_run`: bool

**Version path detection:**
- v16 and below: WildFly-based → migration path required
- v17 and above: Quarkus-based → in-place upgrade path
- Detect by checking presence of `standalone/` directory in keycloak_home

**Executor flow — WildFly→Quarkus (source ≤ 16, target ≥ 17):**
1. Preflight: detect version, validate target reachable via download URL, check DB reachable
2. Snapshot: `dispatch_agent_job("keycloak_export_realms", ...)` — `kc.sh export --realm <realm> --file /tmp/realm-<name>.json` for each realm; DB dump via pg_dump/mysqldump; store artifact paths
3. Install new Keycloak: download target release zip, extract to `/opt/keycloak-<version>`
4. Build: `dispatch_agent_job("keycloak_build_quarkus", ...)` — `kc.sh build --db=<vendor>`
5. Stop old Keycloak: `systemctl stop keycloak`
6. Configure new: update `/opt/keycloak/conf/keycloak.conf` (DB, hostname, https)
7. Start new: `kc.sh start --optimized`, wait for `/health/ready`
8. Import realms: `kc.sh import --file /tmp/realm-<name>.json` for each exported realm
9. Verify: `GET /admin/realms` via admin API — all realms present, user count matches snapshot

**Executor flow — Quarkus in-place (source ≥ 17, target ≥ 17):**
1. Preflight: detect version, validate +1 major or minor
2. Snapshot: realm export (same as above) + DB dump
3. Stop old Keycloak
4. Download and extract new release
5. `kc.sh build --db=<vendor>`
6. Run DB migration: `kc.sh show-config` triggers schema auto-migration on first start
7. Start: `kc.sh start --optimized`, wait for `/health/ready`
8. Verify: `/health/ready` returns UP, realm count matches

**Rollback:** ROLLBACK_CAPABILITY = `"full"` — stop new Keycloak, start old binary, restore DB from dump, reimport realms from export. For WildFly→Quarkus path: restore old WildFly installation from snapshot.

**change_type_definition:**
```json
{
  "change_type": "keycloak_upgrade",
  "display_name": "Keycloak Major Upgrade",
  "steps": [{"generic_action": "keycloak_upgrade", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable", "agent_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "keycloak_upgrade",
  "rollback_connector_type": "nexplane_agent"
}
```

**Smoke test:**
- Phase 1: launch EC2 from cached AMI (Keycloak 21.1, Postgres backend). Cache key `/nexplane/smoke-amis/keycloak/21.1`. Register connector + asset.
- Phase 2: CR lifecycle — upgrade 21.1→24.0 (Quarkus in-place path). Assert `/health/ready` UP, master realm present.
- Phase 3: Rollback. Assert old version responds.
- Phase 4: Teardown.

---

## CR Type 2: `freeipa_upgrade`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/freeipa_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/freeipa_upgrade.json`
- Modify: `backend/app/models/change_request.py` — add `freeipa_upgrade`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file for DB enum
- Create: `backend/tests/smoke/test_smoke_freeipa_upgrade.py`

**Parameters:**
- `source_version`: e.g. `"4.10"` (required)
- `target_version`: e.g. `"4.12"` (required)
- `replica_hosts`: list of `{host}` — upgrade replicas before master (required if any replicas)
- `master_host`: `{host}` — upgraded last (required)
- `ipa_admin_password`: IPA admin password
- `dry_run`: bool

**FreeIPA upgrade topology rule:** replicas must be upgraded before the master (IPA replication is backward-compatible only in one direction). Within a replica set, any order is safe.

**Executor flow:**
1. Preflight: `dispatch_agent_job("ipa_status_check", ...)` — `ipactl status` on master + all replicas, `ipa --version` to get current versions
2. Snapshot: `dispatch_agent_job("ipa_backup", ...)` — `ipa-backup --gpg` (creates backup in `/var/lib/ipa/backup/`); record backup directory path
3. Upgrade replicas (in order): `dispatch_agent_job("ipa_server_upgrade", ...)` per replica
   - `dnf upgrade ipa-server ipa-server-dns ipa-client` (RHEL/CentOS) or equivalent
   - `ipa-server-upgrade` — idempotent, safe to re-run
   - Verify: `ipactl status` all green, `ipa --version` matches target
4. Upgrade master: same `dnf upgrade` + `ipa-server-upgrade` sequence
5. Verify: `ipa user-find --all` returns users, Kerberos ticket works (`kinit admin`)
6. Return: `{status, master_upgraded, replicas_upgraded, source_version, target_version, backup_path, upgraded_at}`

**Rollback:** ROLLBACK_CAPABILITY = `"partial"` — FreeIPA does not support downgrade of the server package. Rollback restores from `ipa-backup` on the master (which wipes and re-initializes the LDAP+Kerberos DBs). Replicas self-heal from master after restore. Surface data loss window (all changes after backup time are lost).

**Smoke test:** Reuse cached FreeIPA AMI `ami-064887df65fad525f` (at `/nexplane/smoke-amis/freeipa/7d2480d9`). Single-node (master, no replicas). Upgrade 4.10→4.12 (or whatever version is on the cached AMI → latest available). Assert `ipactl status` all green. Rollback asserts `ipa-backup restore` completes without error.

---

## CR Type 3: `openldap_schema_migration`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/openldap_schema_migration.py`
- Create: `backend/app/connectors/change_type_definitions/openldap_schema_migration.json`
- Modify: `backend/app/models/change_request.py` — add `openldap_schema_migration`
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file for DB enum
- Create: `backend/tests/smoke/test_smoke_openldap_schema_migration.py`

**Parameters:**
- `schema_ldif_path`: path on target host to the new schema LDIF file (required) — operator pre-stages this
- `schema_dn`: DN of the schema to add/modify (e.g. `cn=myapp,cn=schema,cn=config`) (required)
- `backup_path`: where to save slapcat export (default `/tmp/nexplane-ldap-backup.ldif`)
- `slapd_config_dir`: default `/etc/ldap/slapd.d`
- `dry_run`: bool

**Executor flow:**
1. Preflight: `dispatch_agent_job("openldap_preflight", ...)` — `slaptest -F /etc/ldap/slapd.d` (config valid), `ldapsearch -x -b '' -s base namingContexts` (server reachable), validate schema LDIF exists at `schema_ldif_path`
2. Snapshot: `dispatch_agent_job("openldap_slapcat", ...)` — `slapcat -n0 -l /tmp/nexplane-config.ldif` (config DB), `slapcat -n1 -l /tmp/nexplane-data.ldif` (data DB); store paths
3. Test: `dispatch_agent_job("openldap_schema_test", ...)` — `slapadd -n0 -F /tmp/slapd-test.d -l /tmp/nexplane-config.ldif && slapadd -n0 -F /tmp/slapd-test.d -l <schema_ldif_path>` in temp config dir — validates schema LDIF is syntactically valid without touching production
4. Apply: `dispatch_agent_job("openldap_schema_apply", ...)` — `ldapadd -Y EXTERNAL -H ldapi:/// -f <schema_ldif_path>` (for new schema) or `ldapmodify` (for modifications)
5. Verify: `ldapsearch -Y EXTERNAL -H ldapi:/// -b cn=schema,cn=config` — new schema DN present
6. Return: `{status, schema_dn, backup_config_path, backup_data_path, applied_at}`

**Rollback:** ROLLBACK_CAPABILITY = `"full"` — stop slapd, restore config from slapcat export (`slapadd -n0 -F /etc/ldap/slapd.d -l /tmp/nexplane-config.ldif` after clearing slapd.d), start slapd. Data DB is untouched by schema migrations so no data restore needed (schema changes don't modify existing entries).

**Smoke test:** EC2 with OpenLDAP (slapd) installed. Cached AMI `/nexplane/smoke-amis/openldap/2.5`. Pre-stage a test schema LDIF (`cn=testapp,cn=schema,cn=config`) on the AMI. CR lifecycle: apply schema → verify DN present → rollback → verify DN gone.

---

## CR Type 4: `vault_cluster_upgrade`

**Files:**
- Create: `backend/app/connectors/executors/nexplane_agent/vault_cluster_upgrade.py`
- Create: `backend/app/connectors/change_type_definitions/vault_cluster_upgrade.json`
- Modify: `backend/app/models/change_request.py` — add `vault_cluster_upgrade` after `vault_rotate` (or near existing vault types)
- Modify: `backend/app/connectors/catalog/nexplane_agent.json`
- Create: migration file for DB enum
- Create: `backend/tests/smoke/test_smoke_vault_cluster_upgrade.py`

**Parameters:**
- `source_version`: e.g. `"1.15"` (required)
- `target_version`: e.g. `"1.16"` (required)
- `nodes`: list of `{host, api_port}` (required — all Raft cluster members)
- `vault_token`: Vault root token or token with `sys/` capabilities
- `snapshot_s3_bucket`: optional S3 bucket for Raft snapshot upload
- `dry_run`: bool

**Vault upgrade topology rule:** upgrade standby nodes first, then step down the active node (it becomes standby), then upgrade it. This minimizes downtime.

**Executor flow:**
1. Preflight: `GET /v1/sys/health` on all nodes, identify active vs standby, validate version path (+1 minor max for safety)
2. Snapshot: `dispatch_agent_job("vault_raft_snapshot", ...)` — `vault operator raft snapshot save /tmp/nexplane-vault-snapshot.snap`; optionally upload to S3; store path
3. Identify standbys: all nodes where `standby: true` in `/v1/sys/health`
4. Upgrade standbys (in order): per standby —
   a. `dispatch_agent_job("vault_upgrade_node", ...)` — stop vault, replace binary, start vault
   b. Wait for node to unseal and rejoin: poll `/v1/sys/health` until `initialized: true, sealed: false`
   c. Verify standby role: `standby: true` in health response
5. Step down active: `dispatch_agent_job("vault_stepdown", ...)` — `vault operator step-down` — triggers leader election; new leader elected from upgraded standbys
6. Upgrade old active (now standby): same stop/replace/start/unseal-poll sequence
7. Verify: all nodes `/v1/sys/health` healthy, `vault version` matches target on all nodes
8. Return: `{status, nodes_upgraded, snapshot_path, active_node, upgraded_at}`

**Rollback:** ROLLBACK_CAPABILITY = `"full"` — `vault operator raft snapshot restore /tmp/nexplane-vault-snapshot.snap` on the active node, then rolling restart of all nodes with old binary. Data between snapshot and upgrade is lost; surface this clearly.

**change_type_definition:**
```json
{
  "change_type": "vault_cluster_upgrade",
  "display_name": "HashiCorp Vault Cluster Upgrade",
  "steps": [{"generic_action": "vault_cluster_upgrade", "purpose": "execute", "required": true}],
  "preflight_checks": ["connector_reachable", "agent_reachable"],
  "verification_methods": ["api_check"],
  "rollback_action": "vault_cluster_upgrade",
  "rollback_connector_type": "nexplane_agent"
}
```

**Smoke test:** Reuse cached Vault AMI `ami-09df8f5733ff981b3`. Single-node Vault (Raft, no standbys — simulates 1-node cluster). Upgrade 1.15→1.16. Assert `/v1/sys/health` unsealed + initialized, version matches. Rollback: restore snapshot, verify old version. Teardown.

---

## Backlog additions (cross-cloud parity)

- **Azure AD B2C / Entra ID upgrade** — Entra ID is SaaS, no upgrade executor needed; track as N/A
- **GCP Cloud Identity / GCDS upgrade** — similar SaaS; N/A
- **Okta upgrade** — SaaS; N/A
- **Active Directory functional level upgrade** — already implemented (`ad_domain_functional_level_upgrade`)
