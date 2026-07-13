# Migration Workflows Design

## Product Context

Nexplane is an operating system for infrastructure intent, not a CLI augmentation tool. Operators and LLMs express *what they want done*; the platform handles discovery, sequencing, verification, and rollback. Commands, parameters, and implementation details are available but subdued — the primary surface is intent.

These workflows are composed by an LLM via the MCP server or project workflow orchestration. Individual CR types are primitives. The workflows documented here are the canonical compositions of those primitives for three common operational use cases.

---

## Scope

Three use cases, one shared verification foundation:

1. **Linux OS Upgrade** — in-place upgrade or new-host migration depending on viability
2. **Legacy Application Containerization** — build container image, run alongside, canary cutover, decommission
3. **PostgreSQL Version Migration** — forced upgrade (cost/EOL pressure), data integrity verified, old instance decommissioned

All three share the same discovery and behavioral baseline primitives, and all use the same verification layer to establish "production quality restored."

---

## Design Philosophy

### Operators speak in intent

A CR's primary display is its intent summary — what is being done, to which asset, with what expected outcome and rollback guarantee. Implementation details (parameters, commands, query strings, rsync flags) are present in the data model and accessible in the UI but visually subdued. Approvers modify intent, not commands.

### Discovery is autonomous

The platform does not require operators to pre-register application structure. Discovery CRs interrogate running systems using the Nexplane agent, OS tooling (`lsof`, `ss`, `ps`, `systemctl`, `ldd`), and common config file locations. The resulting Application Profile is the source of truth for all downstream migration steps.

### Production quality has four layers

A CR reaching `completed` means the executor exited successfully. That is not sufficient. Production quality requires all four layers to pass:

| Layer | What it means |
|---|---|
| Infrastructure | Host reachable, agent connected, cloud API accessible |
| Service | Process running, port listening, systemd unit active |
| Application | Endpoints returning expected responses within latency threshold |
| Data | Query results within expected range, schema version correct |

The `verify_against_baseline` CR enforces all four layers. Failure in Application or Data triggers FILO rollback. Failure in Infrastructure or Service is surfaced as a warning — operator decision.

### AI-assisted escalation

When automated execution reaches the boundary of what deterministic rules can handle, the CR escalates to `ai_assisted` mode. The platform parks execution, hands structured context to the LLM, receives a proposed resolution, requires operator approval, then re-enters the workflow. This is not a failure state — it is a first-class escalation path. The operator still speaks in intent; the LLM reasons; the platform enforces approval and rollback.

---

## New CR Types

### `discover_application_profile`

**Intent:** Map what is running on a host and what it depends on, without requiring operator knowledge of the application.

**Executor behavior:**

*Runtime pass* via Nexplane agent:
- `lsof -nP -i` — listening ports mapped to process + PID
- `ss -tulpn` — supplementary socket state
- `ps auxf` — process tree with arguments
- `systemctl list-units --type=service --state=running` — service names and unit files
- `ldd /proc/<pid>/exe` — linked library versions per process
- `/proc/<pid>/environ` — environment variables (filtered: connection strings, config paths, port bindings)
- `netstat -an` or `ss -an` — established outbound connections (remote host, port)

*Static pass* via config file discovery:
- Walks `/etc`, `/opt`, `/usr/local/etc`, `/home/*/`, `/var/*/conf*`
- Parses: nginx/apache vhost blocks, systemd unit files, `.env`, `application.properties`, `database.yml`, `pg_hba.conf`, `my.cnf`, `docker-compose.yml`, `/etc/hosts`
- Extracts: upstream targets, database DSNs, bind addresses, declared health check paths, referenced config paths

**Output — Application Profile (stored on asset):**
```
{
  processes: [{name, pid, executable, service_unit, listening_ports, outbound_connections, linked_libraries}],
  endpoints: [{protocol, port, process, declared_health_path, source: runtime|config}],
  dependencies: [{type: db|http|socket|file, host, port, dsn_template, source: runtime|config, confidence: both|runtime_only|config_only}],
  config_files: [{path, format, extracted_keys}],
  library_versions: [{name, version, path}]
}
```

**Rollback:** Non-mutating. No rollback needed.

---

### `capture_behavioral_baseline`

**Intent:** Record how the application behaves now, so any post-migration state can be compared against it.

**Executor behavior:**

Reads the Application Profile from `discover_application_profile`. For each discovered endpoint and dependency:

1. Probes actively: HTTP GET (status, latency p50/p95, content signature), TCP connect, DB connection + `SELECT 1`, port scan
2. Monitors passively via agent for actual traffic confirmation
3. Tracks per-dependency observation state: `pending → observed | unverified`

**Adaptive extension logic:**

For each dependency where `state = pending` after the initial window (default: 20 minutes):
- Confidence `both` (seen in runtime AND config): extend by 10 minutes, up to 2-hour max
- Confidence `config_only` (process may be idle/scheduled): extend once by 20 minutes, mark `low_confidence` if still no traffic
- Confidence `runtime_only`: already observed — mark `observed`
- If max window elapsed and still no traffic: mark `unverified`, continue with warning

Unverified dependencies surface in the operator approval view: *"Database connection detected in configuration but no traffic observed during the 2-hour baseline window. Post-migration verification for this dependency will have lower confidence."*

**Output — Behavioral Baseline (stored on asset):**
```
{
  captured_at, observation_duration_seconds,
  endpoints: [{url, probe_type, status_code, response_ms_p50, response_ms_p95, content_signature, confidence}],
  services: [{name, state, active_connections, port}],
  dependencies: [{target, type, latency_ms_p50, query_sample_result, row_count_sample, confidence}],
  library_versions: [{name, version}]
}
```

**Rollback:** Non-mutating. No rollback needed.

---

### `verify_against_baseline`

**Intent:** Confirm the migrated system matches the pre-migration behavioral benchmark across all four production quality layers.

**Executor behavior:**

Reads the Behavioral Baseline. Probes each entry against the target environment (may be same host post-upgrade, new host, new container, or new DB instance — target is a CR parameter). Compares results with thresholds:

| Check type | Pass condition |
|---|---|
| HTTP endpoint status | Matches baseline status code |
| HTTP endpoint latency | Within 150% of baseline p95 |
| HTTP content signature | Baseline signature present in response |
| Service state | `active (running)` |
| Port listening | Same ports open |
| DB connection | Connects and query executes |
| DB row count | Within 5% of baseline sample |
| Library version | ≥ baseline version (downgrade = warning) |

For `low_confidence` and `unverified` baseline dependencies: runs probe but reports result as advisory — does not block pass/fail determination.

**Verification Report layers:**
- **Infrastructure**: host reachable, agent connected
- **Service**: processes running, ports open
- **Application**: endpoint status, latency, content — *blocks completion on fail*
- **Data**: query results, row counts — *blocks completion on fail*

On Application or Data layer failure: executor returns `{failed: true}`, triggering FILO rollback of the current project sequence.

**Rollback:** Non-mutating. FILO unwind is triggered by the workflow, not this CR.

---

### `run_os_upgrade`

**Intent:** Upgrade the OS to the target version with service continuity.

**Parameters (subdued):** target version, upgrade strategy (dist-upgrade / release-upgrade), packages to hold, pre/post hooks.

**Executor behavior:**
- Validates upgrade path is supported from current version to target
- Puts services into graceful stop order (derived from Application Profile dependency graph)
- Runs upgrade
- Restarts services in reverse stop order
- Returns structured result: packages upgraded count, held packages, services restarted

**Rollback:** Restore from `lvm_snapshot` or `backup_capture` taken in the preceding CR.

---

### `provision_host`

**Intent:** Stand up a new host with a modern OS and kernel matching the target specification.

**Parameters (subdued):** OS version, instance type, networking (inherits from source host's Application Profile), SSH key.

**Creates:** Host asset, linked to the source host asset as migration target.

**Rollback:** Terminate new host.

---

### `migrate_host_config`

**Intent:** Backport network, security, and system configuration from old host to new host.

**Executor behavior:**
- Reads config files discovered in Application Profile
- Translates distro-specific syntax where needed (e.g., iptables → nftables, sysvinit → systemd)
- Applies: network interfaces, firewall rules, sysctl tunables, mount points, cron jobs, `/etc/hosts` entries
- Does NOT copy OS-level files verbatim — translates to target OS conventions

**Rollback:** Remove applied configs from new host. New host was not serving traffic, so rollback is low-risk.

---

### `migrate_identity`

**Intent:** Move local users, groups, and access credentials from old host to new host.

**Executor behavior:**
- Copies local user accounts (filtering OS default UIDs), groups, `/etc/sudoers.d/` entries
- Migrates SSH `authorized_keys` per user
- Applies PAM configuration from source where compatible with target OS
- Detects and reports UID/GID collisions with new OS defaults — resolves by remapping

**Rollback:** Remove migrated users and groups from new host.

---

### `rsync_application`

**Intent:** Transfer application files from old host to new host.

**Executor behavior:**
- Uses Application Profile's config file map and process executable paths to determine what to transfer
- Excludes OS-level paths (`/bin`, `/sbin`, `/lib`, `/usr/bin`, `/proc`, etc.)
- Includes: application directories, config files, data directories (if not DB-backed), log directories
- Runs `rsync -az --checksum` with structured output (files transferred, bytes, errors)

**Rollback:** Remove transferred files from new host.

---

### `resolve_dependencies`

**Intent:** Detect and resolve missing library or package dependencies on the new host after application transfer.

**Executor behavior:**

Iterative resolution loop (max 5 iterations):
1. Attempt to start the application (via systemd unit or discovered start command)
2. Capture startup errors: missing `.so` files, wrong library versions, missing binaries
3. Cross-reference old host's installed package list (captured in Application Profile)
4. Map packages to equivalents on new distro/version using known package name mappings + `apt-cache search` / `dnf provides`
5. Install resolved packages
6. Retry

If iteration limit reached with unresolved dependencies: escalate to **`ai_assisted_cr`** — passes structured failure report (missing libraries, old package names, new distro available packages) to the LLM for reasoning. LLM proposes resolution; operator approves; execution continues.

**Rollback:** Remove installed packages from new host.

---

### `ai_assisted_cr`

**Intent:** Escalate an execution decision to LLM reasoning when automated resolution is exhausted.

**Behavior:**
- Parks workflow at current FILO position
- Passes structured context to LLM via MCP: failure report, asset state, available options
- LLM returns proposed next action as a structured CR step
- Platform presents proposed action to operator for approval (with full detail available, subdued by default)
- On approval: proposed action executes within current FILO position
- On rejection: operator can modify proposed action or abort

This is not a failure state. It is the platform's mechanism for handling cases where rules-based execution is insufficient. The rollback guarantee is preserved — any action taken through `ai_assisted_cr` is itself a CR step with rollback declared.

---

### `migrate_postgres_instance`

**Intent:** Copy schema and data from a source PostgreSQL instance to a target instance of a different version.

**Parameters (subdued):** source DSN, target DSN, migration strategy (dump/restore or logical replication), excluded tables, parallelism.

**Executor behavior:**

*Dump/restore strategy (default):*
- `pg_dump --format=custom` from source
- `pg_restore` to target
- Streams via pipe — does not require intermediate storage for large databases
- Reports: tables migrated, rows copied, errors

*Logical replication strategy (zero-downtime option):*
- Creates publication on source, subscription on target
- Monitors replication lag until < 1 second
- Signals readiness for connection string cutover
- Replication subscription removed after cutover confirmed

**Rollback:** Drop schema on target instance. Source instance untouched.

---

### `verify_data_integrity`

**Intent:** Confirm migrated database contains complete, structurally valid data matching the pre-migration baseline.

**Checks:**
- Row counts per table: within 5% of baseline (configurable)
- Foreign key constraint validation: `pg_constraint` / `FOREIGN KEY` checks pass
- Schema version: matches baseline (migration tool metadata — alembic `alembic_version`, flyway `flyway_schema_history`)
- Sample query results: configurable spot-check queries with expected result ranges
- Index validity: no invalid indexes (`pg_index.indisvalid`)

**Rollback:** Non-mutating.

---

### `run_schema_migration`

**Intent:** Apply database schema migrations required for version compatibility or application changes.

**Parameters (subdued):** migration tool (alembic/flyway/liquibase/raw SQL), migration target version, migration script path.

**Executor behavior:**
- Detects migration tool from Application Profile or parameter
- Runs migration to target version
- Records before/after schema version in CR result
- Captures rollback migration command (down-migration) in `rollback_data`

**Rollback:** Run down-migration to previous schema version.

---

### `update_connection_strings`

**Intent:** Point applications at the new database instance by updating all discovered connection references.

**Executor behavior:**
- Reads all ApplicationProfiles linked to the source DatabaseInstance asset
- For each: locates connection string in discovered config files, environment variable files, systemd unit `Environment=` directives
- Replaces source DSN with target DSN
- Restarts affected services (in Application Profile dependency order)

**Rollback:** Revert connection strings to source DSN, restart services.

---

### `decommission_database_instance` / `decommission_legacy_host`

**Intent:** Terminate and remove an instance that has been fully migrated away from.

These CRs are intentionally gated. The platform enforces that the preceding `verify_against_baseline` CR must be in `completed` state before either decommission CR can be approved.

The approval view includes an explicit callout: *"After this step, rollback requires restoring from the snapshot taken at [step N] and may take [estimated time]. Verify the preceding verification step passed before approving."*

**Rollback:** Restore from the most recent backup/snapshot CR in the FILO stack. Connection strings revert via the preceding `update_connection_strings` rollback.

---

### `shift_traffic_weight`

**Intent:** Move a percentage of traffic from one target to another via DNS weighted routing or load balancer weight adjustment.

**Parameters (subdued):** source target, destination target, weight percentage, stability window (minutes before auto-advancing or halting).

**Operates on:** `DnsRecord` assets (weighted CNAME targets) or load balancer listener assets.

**Rollback:** Restore prior weight distribution.

---

### `containerize_application`

**Intent:** Build a container image from a discovered application profile.

**Executor behavior:**
- Reads Application Profile: executable, libraries, config files, listening ports, environment variables
- Generates Dockerfile: appropriate base image for detected runtime (Python, Java, Node, Go binary, etc.), copies application files, installs detected library dependencies, exposes discovered ports, sets entrypoint from discovered start command
- Builds image: tagged with registry, repository, and version
- Runs container locally for startup validation before publishing

**Creates:** `ContainerImage` asset linked to source `ApplicationProfile`.

**Rollback:** Remove built image from registry.

---

### `deploy_container_alongside`

**Intent:** Run the containerized application in parallel with the legacy application, with no traffic routed to it yet.

**Parameters (subdued):** target host or cluster, resource limits, namespace.

**Rollback:** Stop and remove container/pod. Legacy application unaffected.

---

## New Asset Types

### `ApplicationProfile`
Created by `discover_application_profile`. Represents a discovered workload — not the host, not the service, but the application with its full dependency graph. One profile per discovered workload. Linked to host asset.

### `ServiceEndpoint`
A probed application entry point. Stores baseline probe behavior: expected status, latency envelope, content signature. Linked to its `ApplicationProfile`. Can be manually registered for endpoints discovery missed (e.g., a known health check path not found in config).

### `DatabaseInstance`
A running database, independent of its host. Stores: engine, version, connection DSN template, schema version, migration tool hint, baseline row counts per table. The asset that schema migration and data integrity CRs operate on. When migrating, both source and target are `DatabaseInstance` assets linked by the migration CR.

### `ContainerImage`
A built container image with registry, repository, tag, digest, and a link to the `ApplicationProfile` it was built from. Enables before/after equivalence reasoning.

### Extended: `DnsRecord`
Add: weighted routing targets (list of `{target, weight}` pairs), TTL, CNAME chain (what this record ultimately resolves to). Application Profile discovery auto-links DNS records found in `/etc/hosts`, nginx upstreams, and detected DNS lookups to the ApplicationProfile that uses them.

### Extended: `NetworkPort`
Promote from implicit host metadata to a linkable asset: `{host_asset_id, port, protocol, process_name, service_name}`. Created by discovery. Verified post-migration by `verify_against_baseline`.

---

## Workflow Compositions

### Workflow A: Linux OS Upgrade

The planning engine assesses upgrade viability from the Application Profile after discovery. Path A (in-place) is selected when: target version is one major release ahead, no detected packages with known cross-version incompatibilities, and fewer than 10 distinct outbound dependencies. Path B (new-host migration) is selected when: two or more major version jumps are required, known incompatible packages are detected, or application complexity (dependency count, custom kernel modules, non-standard init system) exceeds threshold. The LLM presents the selected path with rationale; the operator approves the strategy.

```
1. discover_application_profile     → target host
   Intent: "Map what's running before any changes"

2. capture_behavioral_baseline      → ApplicationProfile
   Intent: "Record how the application behaves now — the success benchmark"
   [Adaptive window: extends if dependencies seen but no traffic observed]

3. backup_capture (lvm_snapshot)    → target host
   Intent: "Full disk snapshot — the rollback anchor"

── PATH A: In-place upgrade ───────────────────────────────────────────

4a. run_os_upgrade                  → target host
    Intent: "Upgrade OS to [target version] with service continuity"

5a. verify_against_baseline         → target host (post-upgrade)
    Intent: "Confirm all application behavior matches pre-upgrade benchmark"
    On fail: FILO unwind → restore snapshot

── PATH B: New-host migration ─────────────────────────────────────────

4b. provision_host                  → new host (modern kernel/OS)
    Intent: "Stand up new host with [target OS]"
    Rollback: terminate new host

5b. migrate_host_config             → old → new
    Intent: "Backport network, security, and system configuration"

6b. migrate_identity                → old → new
    Intent: "Move users, groups, and SSH access"

7b. rsync_application               → old → new
    Intent: "Transfer application files"

8b. resolve_dependencies            → new host
    Intent: "Detect and resolve missing libraries on the new host"
    [Escalates to ai_assisted_cr if automated resolution exhausted]

9b. verify_against_baseline         → new host
    Intent: "Confirm application matches pre-migration benchmark"
    On fail: FILO unwind new-host steps, legacy host untouched

10b. shift_traffic_weight           → 10% to new host (canary)
     Intent: "Move initial traffic to new host, monitor stability"

11b. shift_traffic_weight           → 100% to new host
     Intent: "Complete traffic cutover"

12b. decommission_legacy_host       → old host
     Intent: "Remove old host — gated on step 9b being completed"
     [Point of no return callout in approval view]
```

---

### Workflow B: Legacy App to Container Migration

```
1. discover_application_profile     → legacy host
   Intent: "Map billing service dependencies and endpoints"

2. capture_behavioral_baseline      → ApplicationProfile
   Intent: "Record service behavior — what the container must match"
   [Adaptive: billing jobs may be infrequent]

3. backup_capture                   → legacy host
   Intent: "Snapshot before any changes"

4. containerize_application         → ApplicationProfile
   Intent: "Build container image from discovered service profile"
   Creates: ContainerImage linked to ApplicationProfile

5. deploy_container_alongside       → staging / same host
   Intent: "Run containerized service with no traffic yet"
   Rollback: stop and remove container

6. verify_against_baseline          → running container (probed at container's exposed ports)
   Intent: "Confirm container matches pre-migration benchmark"
   On fail: FILO unwind → remove container, legacy untouched

7. shift_traffic_weight             → 10% to container
   Intent: "Move initial traffic to container, monitor stability window"
   Rollback: restore prior weights

8. shift_traffic_weight             → 100% to container
   Intent: "Complete traffic cutover"
   Rollback: restore to step 7 state (canary), not all the way to legacy

9. decommission_legacy_process      → legacy host
   Intent: "Stop and disable legacy service — gated on step 6 completed"
   Rollback: re-enable service, restore traffic weights
```

**Note on step 8 rollback:** Restores to step 7 state (10% canary), not to legacy. This is intentional — if full cutover has problems, the canary is a stable intermediate state. Only if canary also fails does FILO continue unwinding to legacy.

---

### Workflow C: PostgreSQL Version Migration

Forcing function: the old version is EOL or expensively priced on the managed service. Decommission of old instance is part of the workflow, not optional — but gated behind verified cutover.

```
1. discover_application_profile     → source DB host / all connected app hosts
   Intent: "Discover what connects to this database and how"
   Creates: DatabaseInstance asset (engine: postgres, version: 12)

2. capture_behavioral_baseline      → DatabaseInstance (source)
   Intent: "Record query patterns, row counts, connection behavior"
   [Adaptive: extends if scheduled batch jobs haven't fired]

3. managed_db_snapshot              → source DatabaseInstance
   Intent: "Point-in-time snapshot — the rollback artifact"

4. provision_database_instance      → new PostgreSQL 16 host/RDS
   Intent: "Stand up new PostgreSQL 16 instance"
   Creates: DatabaseInstance asset (target)
   Rollback: terminate new instance

5. migrate_postgres_instance        → source → target DatabaseInstance
   Intent: "Copy schema and data to PostgreSQL 16 instance"
   [Strategy: dump/restore or logical replication — subdued]
   Rollback: drop schema on target, source untouched

6. verify_data_integrity            → target DatabaseInstance
   Intent: "Confirm migrated database matches pre-migration benchmark"
   [Row counts, FK constraints, schema version, index validity]
   On fail: FILO unwind

7. run_schema_migration             → target (if PG16 compatibility changes needed)
   Intent: "Apply schema changes required for PostgreSQL 16 compatibility"
   Rollback: run down-migration

8. update_connection_strings        → all ApplicationProfiles linked to source DB
   Intent: "Point applications at the new database instance"
   [Discovers connection strings from config files found in step 1]
   Rollback: revert connection strings, restart services

9. verify_against_baseline          → all affected ApplicationProfiles
   Intent: "Confirm applications work correctly with the new database"
   On fail: FILO unwind → revert connection strings → apps reconnect to PG12

10. decommission_database_instance  → source DatabaseInstance (PG12)
    Intent: "Shut down old instance — removes the cost pressure"
    [Point of no return callout. Gated on step 9 completed.
     Rollback: restore from step 3 snapshot, revert connection strings — estimated time shown]
```

---

## Irreversibility Thresholds

Any decommission CR (`decommission_database_instance`, `decommission_legacy_host`, `decommission_legacy_process`) is an irreversibility threshold. The platform enforces:

1. The immediately preceding `verify_against_baseline` CR must be in `completed` state before the decommission CR can enter `awaiting_approval`
2. The approval view includes an explicit threshold callout — estimated rollback time and what it requires
3. The decommission CR's rollback_data captures the snapshot/backup reference needed to restore

This is not a soft warning — it is a hard gate in the state machine.

---

## UI Principles

**Primary view (approval / detail):**
- Intent summary sentence
- Target asset(s)
- Expected outcome
- Estimated duration
- Rollback availability and estimated rollback time
- Irreversibility threshold callout (where applicable)

**Subdued / expandable:**
- CR parameters (flags, strategies, thresholds)
- Executor implementation details
- Verification query contents
- Baseline comparison data
- Raw discovery output

Approvers interact with intent. They expand to modify parameters if needed. The platform re-plans with modified inputs before execution proceeds.

---

## Open Items / Future Expansion

- **`shift_traffic_weight` stability window:** The canary step should support an automatic stability window — hold at N% for M minutes, monitor error rate from the Behavioral Baseline's endpoint probes, auto-advance or auto-halt based on threshold. Requires metrics integration.
- **`ai_assisted_cr` scope:** Dependency resolution is the first use case. Other candidates: ambiguous config translation in `migrate_host_config`, novel library version mappings, unusual application startup sequences.
- **Windows analog:** `migrate_host_config` and `migrate_identity` have Windows equivalents (Group Policy, Local Security Policy, Registry export). Defer until WinRM connector expands.
- **Multi-host application profiles:** Applications spanning multiple hosts (app tier + cache + DB) need a `CompositeApplicationProfile` asset that links constituent profiles. Discovery should detect cross-host dependencies and suggest grouping.
