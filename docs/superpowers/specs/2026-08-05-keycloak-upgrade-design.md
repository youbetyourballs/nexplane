# Keycloak / RHSSO Rolling Upgrade Design

**Goal:** Upgrade a standalone or clustered Keycloak (or RHSSO) deployment to a target version, capturing a full DB snapshot before the irreversible schema migration runs, and performing a rolling restart that keeps the cluster available throughout.

**Architecture:** The executor takes a database dump and realm JSON exports before touching any node. The first node to start on the new binary automatically runs Keycloak's DB schema migration — this is the point of no return. Subsequent nodes skip migration since the schema is already updated. For clustered deployments, each node is drained from the load balancer before shutdown and restored after health confirmation, maintaining service availability throughout the upgrade.

## Phases

1. **Preflight** — Query the Keycloak admin REST API (`GET /auth/admin/serverinfo`) on all nodes to confirm current version and reachability. Check DB connectivity and confirm DB engine version compatibility with the target Keycloak version. For clustered deployments, verify all nodes are reachable and the Infinispan cluster is fully formed by comparing realm data across nodes. Check active session count and warn if high — users on nodes without sticky sessions will be logged out during restart. Confirm available disk at `backup_path` is sufficient for DB dump and realm exports.

2. **Snapshot** — Dump the Keycloak database using the appropriate tool (`pg_dump`, `mysqldump`, or `mariadb-dump`) to `backup_path/db-<timestamp>.sql`. Export realm configuration for each realm via `GET /auth/admin/realms/{realm}/export` and write each as a JSON file to `backup_path/realms/`. Record the current node list and version in CR state. The executor surfaces the backup path prominently in the approval summary so operators know exactly what restore artifact exists before approving.

3. **Upgrade** — For each node in sequence: (1) drain the node from the load balancer pool if `lb_type` is set (haproxy socket command, nginx upstream removal, or no-op for `"none"`); (2) stop Keycloak (`systemctl stop keycloak` or `bin/kc.sh stop`); (3) unpack the new binary archive or upgrade via package manager; (4) start Keycloak (`bin/kc.sh start` or `systemctl start keycloak`) — the first node will run DB schema migration automatically on startup; (5) poll `GET /health/ready` until HTTP 200 within `health_timeout_seconds`; (6) restore the node to the LB pool; (7) advance to the next node. Remaining nodes will detect the schema is already current and skip migration.

4. **Verify** — Query `GET /auth/admin/serverinfo` on all nodes and confirm `systemInfo.version` matches `target_version`. Perform a login flow test using the provided admin credentials to confirm the auth service is functional. Inspect the DB migration audit table (`keycloak_migration` for older versions, `databasechangelog` for newer) and confirm the latest entry matches the target version.

5. **Rollback** — Before the first node starts: stop all nodes, restore the DB from the pg_dump/mysqldump backup, downgrade the binary on each node, restart. After the first node has started (schema migration has run): DB restore is required to return to the old version — stop all nodes, restore DB from snapshot, downgrade binaries, restart. The executor marks schema migration start as the point of no return in execution state and surfaces this in the approval summary. Realm JSON exports are available to re-import if selective data recovery is needed without a full DB restore.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_version` | string | yes | — | Target Keycloak version, e.g. `"23.0.4"` |
| `nodes` | list[dict] | yes | — | List of `{host, ssh_host, admin_port, admin_user, admin_password}` |
| `db_host` | string | yes | — | Database hostname |
| `db_port` | int | no | `5432` | Database port |
| `db_name` | string | yes | — | Keycloak database name |
| `db_user` | string | yes | — | Database user |
| `db_password` | string | no | — | Database password (prefer connector credentials) |
| `db_engine` | string | no | `"postgresql"` | Database engine: `"postgresql"`, `"mysql"`, `"mariadb"` |
| `backup_path` | string | no | `/var/backup/keycloak` | Directory for DB dump and realm JSON exports |
| `lb_type` | string | no | `"none"` | Load balancer type for node drain/restore: `"haproxy"`, `"nginx"`, `"none"` |
| `lb_host` | string | no | — | Load balancer host (required if `lb_type` is not `"none"`) |
| `health_timeout_seconds` | int | no | `120` | How long to poll `/health/ready` before failing a node |
| `dry_run` | bool | no | `false` | Plan and validate without executing any changes |

## Rollback Capability

**PARTIAL** — Rollback is fully reversible before the first upgraded node starts (schema migration has not yet run). Once the first node starts and schema migration completes, rollback requires restoring the database from the pre-upgrade dump — the old Keycloak binary cannot read the new schema. The executor surfaces this as the point of no return in the approval summary, labels it explicitly in execution state, and does not proceed past node 1's startup without that state being recorded. Realm JSON exports are available as a secondary restore artifact for selective realm recovery without a full DB rollback.

## Smoke Test Requirements

Single Keycloak node with a PostgreSQL backend, provisioned via docker-compose on EC2. Smoke test upgrades from Keycloak 22.x to 23.x. Verification steps: preflight version check via admin REST API, DB dump to backup path, upgrade binary, health poll until ready, `serverinfo` version confirmation, login flow test with admin credentials, and DB migration table inspection. Also test rollback path: stop the upgraded container, restore the DB dump, start the old binary, and confirm the old version is serving. AMI-cache the initial Keycloak + PostgreSQL provisioning state to avoid cold setup on repeated smoke runs.

## Key Risks

- **Schema migration is the point of no return:** Once the first upgraded node starts, the DB schema is updated and the old Keycloak version cannot read it. The executor records this moment explicitly in CR execution state and the approval summary warns operators before they approve. A DB snapshot is always taken in the Snapshot phase so restore is possible, but it requires downtime for all nodes simultaneously.
- **Session disruption without sticky sessions:** In a clustered deployment, Infinispan caches sessions in memory. If sticky sessions are not configured at the load balancer, users on a node being restarted will be logged out. The preflight check detects `lb_type` and session count and emits a warning in the approval summary — operators must acknowledge this before execution proceeds.
- **Realm export completeness:** Keycloak's built-in realm export excludes some entities (e.g., user passwords hashed with older algorithms, certain federation provider state). The executor notes this limitation in the approval summary. The DB dump is the authoritative restore artifact; realm JSON exports are supplementary for selective recovery only.
