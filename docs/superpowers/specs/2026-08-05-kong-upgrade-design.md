# Kong Gateway Rolling Upgrade Design

**Goal:** Upgrade Kong Gateway to a target version across one or more nodes using a rolling restart strategy for DB-backed deployments or a config swap for DB-less deployments, with a hard gate before irreversible migration finalization.

**Architecture:** The CR handles two distinct Kong deployment modes selected via the `mode` parameter. In DB-backed mode, new-version Kong nodes are brought up one at a time while `kong migrations up` adds backward-compatible schema changes; all old nodes continue serving traffic during the transition, and `kong migrations finish` (the irreversible step) is gated behind an explicit `finalize_migrations` parameter that defaults to false. In DB-less mode, the upgrade is always reversible: the declarative config file is swapped and Kong is reloaded with no schema migration required. Executor is Python async and communicates with each node over SSH via the Nexplane connector.

## Phases

1. **Preflight** — (DB-backed) Run `kong version` on all nodes and record current versions. Run `kong migrations status` and assert no pending migrations from a prior partial upgrade. Call `GET /` on the Admin API of each node and assert HTTP 200. Run `kong check` to verify DB connectivity. Check for Kong Enterprise license if applicable. (DB-less) Parse and validate the new declarative config file with `deck validate` or `kong config parse`. Check Admin API health on all nodes.

2. **Snapshot** — (DB-backed) Export full Kong configuration with `kong config db_export > <backup_path>/kong-backup-<timestamp>.yaml`. Run `pg_dump` (or equivalent for Cassandra) directly against the database as a secondary backup. Record current Kong version string per node. (DB-less) Copy the current declarative config file to `<backup_path>/kong.yml.<timestamp>` on each node. Record current version per node.

3. **Upgrade** — (DB-backed rolling) For each node in sequence: stop Kong (`kong stop`); upgrade the binary or package via `package_manager`; run `kong migrations up` on the first node only — this adds new columns and tables without breaking remaining old-version nodes; start Kong (`kong start`); verify node health via `GET /` on Admin API before proceeding to the next node. After all nodes are upgraded: if `finalize_migrations=true`, run `kong migrations finish` to complete the migration and remove deprecated columns — this is the point of no return. (DB-less) On each node: replace the config file at `declarative_config_path`; run `kong reload` or `POST /config` to the Admin API with the new config content.

4. **Verify** — Call `GET /` on all nodes and assert the response reports the target version. (DB-backed) Run `kong migrations status` and assert "No pending migrations" (or "finalized" if `finalize_migrations` was set). Call `GET /services` and `GET /routes` on the Admin API and assert expected config is present. Send a test request through the Kong proxy and assert a non-5xx response.

5. **Rollback** — (DB-backed, before `migrations finish`) Downgrade the binary to the previous version on affected nodes and restart; the DB schema is backward-compatible with old nodes after `migrations up` alone, so old-version Kong reads it without errors. (DB-backed, after `migrations finish`) Restore from the pg_dump snapshot: stop all Kong nodes, drop and restore the DB, reinstall the old binary, restart. (DB-less) Restore the previous config file from backup on each node and run `kong reload`.

## CR Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `target_version` | string | yes | — | Target Kong version, e.g. "3.6.1" |
| `mode` | string | no | "db" | "db" for DB-backed, "db_less" for declarative |
| `nodes` | list[dict] | yes | — | Node list: `{host, ssh_host, admin_port}` |
| `admin_port` | int | no | 8001 | Kong Admin API port (applied to all nodes unless overridden per node) |
| `db_host` | string | no* | — | Database hostname; required if mode=db |
| `db_name` | string | no* | — | Database name; required if mode=db |
| `db_user` | string | no* | — | Database user; required if mode=db |
| `db_password` | string | no* | — | DB password sourced from connector credentials |
| `declarative_config_path` | string | no* | — | Path to kong.yml on nodes; required if mode=db_less |
| `backup_path` | string | no | "/var/backup/kong" | Directory for backups on each node |
| `finalize_migrations` | bool | no | false | Run `kong migrations finish`; set true to accept irreversibility |
| `package_manager` | string | no | "apt" | Package manager for binary upgrade: "apt", "yum", or "rpm" |
| `dry_run` | bool | no | false | Plan without executing any node operations |

## Rollback Capability

**PARTIAL (DB-backed):** Reversible before `kong migrations finish` by downgrading the binary — old-version Kong reads the DB without errors because `migrations up` is backward-compatible. After `migrations finish` the schema changes are permanent; recovery requires restoring from the pg_dump snapshot, which may lose writes made after the backup was taken. The `finalize_migrations` gate (defaulting to false) ensures operators consciously accept this boundary. **FULL (DB-less):** Always reversible by restoring the previous config file and reloading Kong.

## Smoke Test Requirements

A single-node Kong Gateway (version 3.5) plus PostgreSQL on EC2 running in docker-compose, with a configured Nexplane SSH connector pointing at the Kong node and a valid Admin API endpoint. The smoke run upgrades Kong from 3.5 to 3.6, verifies the migrations gate by asserting the executor stops before `migrations finish` when `finalize_migrations=false`, then re-runs with `finalize_migrations=true` and asserts `kong migrations status` reports finalized. A proxy route is created and tested via curl before rollback. Rollback phase restores from pg_dump and asserts the old version is serving again.

## Key Risks

- **Mixed-version window and `migrations finish` timing:** Running old and new Kong nodes against the same DB is safe only for minor upgrades. If the upgrade spans a major version, Kong may reject the mixed state. The preflight phase checks the version delta and blocks the upgrade with an error if major version boundaries are crossed without explicit confirmation via a future `allow_major_upgrade` parameter.
- **Node left in a stopped state if upgrade fails mid-node:** If the binary upgrade or `kong start` fails on a node, that node is down and not automatically reverted. The executor catches this condition, logs the affected node, and raises a rollback signal before proceeding to subsequent nodes, preventing a partial cluster where some nodes are down and others are on mismatched versions.
- **pg_dump snapshot may be stale by rollback time:** In high-traffic deployments the DB backup taken during Snapshot phase may be minutes old at the time rollback is triggered. The CR notes this gap in the execution result and recommends operators assess write loss tolerance before confirming the rollback. For zero-RPO requirements, operators should pause writes at the load balancer before triggering rollback.
