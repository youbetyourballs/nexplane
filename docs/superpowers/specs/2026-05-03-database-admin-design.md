# Database Administration — Design Spec

**Date:** 2026-05-03
**Status:** Approved
**Scope:** Agent-executed and connector-executed database administration operations covering user provisioning/deprovisioning, permission grants/revokes, audit logging configuration, read replica promotion (RDS), and connection limit management across PostgreSQL, MySQL, and MSSQL.

---

## Background

Nexplane has no database administration coverage today. DBAs and platform engineers currently perform user provisioning, permission changes, and failover operations manually — outside any change management system, with no audit trail, no rollback strategy, and no approval gates. This spec introduces five change types backed by a new `agent/commands/dbadmin/` package and one AWS connector action, covering the most common day-two DB operations teams need to automate safely.

---

## Design Decisions

- **New agent package `agent/commands/dbadmin/`:** Platform-split files keep engine-specific SQL isolated. The dispatcher in `dbadmin.go` reads `db_type` from the change parameters and delegates to the correct file.
- **Credentials from connector, not hardcoded:** The agent receives DB admin credentials through the connector's credential store (same path as all other connector-backed commands). No credentials appear in change parameters or logs.
- **Go `database/sql` with named drivers:** `lib/pq` for PostgreSQL, `go-sql-driver/mysql` for MySQL, `github.com/microsoft/go-mssqldb` for MSSQL. All three use the standard `database/sql` interface; the dispatcher opens the correct DSN and passes `*sql.DB` down.
- **Generated passwords via `crypto/rand`:** When `provision_db_user` omits `password`, the agent generates a 24-character random password (base64url, trimmed). The plaintext is returned as a step output field, encrypted at rest by the existing step-output encryption path.
- **RDS promotion via AWS connector, not agent:** Promoting a read replica requires the AWS SDK, not a DB connection. It is a connector action that calls `rds:PromoteReadReplicaDBInstance`, updates a Route 53 CNAME, and verifies write acceptance. The Go agent is not involved.
- **Rollback is first-class:** Every change type records pre-change state (grants, config values, audit settings) as a step output blob. The rollback step replays the inverse SQL or API call using that blob.
- **`reload_only` flag for connection config:** PostgreSQL supports `pg_reload_conf()` for `max_connections` changes without a restart when using a config file reload. The flag exposes that option; MSSQL always requires a service restart.
- **No schema migrations in this spec:** This spec covers operational DB administration only. Application schema changes (Flyway, Alembic, etc.) are out of scope.

---

## Section 1: Go Driver Dependencies

Add to `agent/go.mod`:

```
require (
    github.com/lib/pq                     v1.10.9
    github.com/go-sql-driver/mysql        v1.8.1
    github.com/microsoft/go-mssqldb       v1.7.2
)
```

Blank imports to register drivers go in `agent/commands/dbadmin/dbadmin.go`:

```go
import (
    _ "github.com/lib/pq"
    _ "github.com/go-sql-driver/mysql"
    _ "github.com/microsoft/go-mssqldb"
)
```

---

## Section 2: Package Structure

```
agent/commands/dbadmin/
    dbadmin.go            // dispatcher: openDB(), Execute(cmd Command) error
    dbadmin_postgres.go   // PostgreSQL implementations
    dbadmin_mysql.go      // MySQL implementations
    dbadmin_windows.go    // MSSQL implementations (build tag: windows OR db_type==mssql)
```

### `dbadmin.go` — dispatcher interface

```go
package dbadmin

import (
    "context"
    "database/sql"
    "fmt"

    _ "github.com/go-sql-driver/mysql"
    _ "github.com/lib/pq"
    _ "github.com/microsoft/go-mssqldb"
)

// Command is the deserialized change parameters delivered by the agent runner.
type Command struct {
    DBType      string            // "postgres" | "mysql" | "mssql"
    DBHost      string
    DBPort      int
    DBName      string
    AdminDSN    string            // resolved from connector credentials at runtime
    Action      string            // matches change type action field
    Params      map[string]any
}

// Result carries output fields that are stored as encrypted step output.
type Result struct {
    Fields map[string]string // e.g. {"generated_password": "...", "pre_change_grants": "..."}
}

func openDB(cmd Command) (*sql.DB, error) {
    db, err := sql.Open(driverName(cmd.DBType), cmd.AdminDSN)
    if err != nil {
        return nil, fmt.Errorf("open db: %w", err)
    }
    db.SetMaxOpenConns(1)
    return db, nil
}

func driverName(dbType string) string {
    switch dbType {
    case "postgres":
        return "postgres"
    case "mysql":
        return "mysql"
    case "mssql":
        return "sqlserver"
    default:
        return dbType
    }
}

// Execute dispatches to the correct engine implementation.
func Execute(ctx context.Context, cmd Command) (*Result, error) {
    db, err := openDB(cmd)
    if err != nil {
        return nil, err
    }
    defer db.Close()
    if err := db.PingContext(ctx); err != nil {
        return nil, fmt.Errorf("db ping: %w", err)
    }
    switch cmd.DBType {
    case "postgres":
        return execPostgres(ctx, db, cmd)
    case "mysql":
        return execMySQL(ctx, db, cmd)
    case "mssql":
        return execMSSQL(ctx, db, cmd)
    default:
        return nil, fmt.Errorf("unsupported db_type: %q", cmd.DBType)
    }
}
```

---

## Section 3: Change Type — `provision_db_user`

**Purpose:** Create a DB login/user with an initial grant set. If `password` is omitted, generate one via `crypto/rand`.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db_type` | string | yes | `"postgres"`, `"mysql"`, or `"mssql"` |
| `db_host` | string | yes | Hostname or IP of the DB server |
| `db_port` | int | no | Defaults: 5432 / 3306 / 1433 |
| `db_name` | string | yes | Database to connect to for the grant step |
| `username` | string | yes | Login/user to create |
| `password` | string | no | Plaintext; generated if absent |
| `grants` | []string | no | e.g. `["myschema.orders: SELECT,INSERT"]` |
| `connector_id` | string | yes | Connector whose credentials provide admin DSN |

### Step Output (encrypted at rest)

```json
{
  "generated_password": "<only present if password was generated>",
  "grants_applied": ["myschema.orders: SELECT,INSERT"]
}
```

### Agent implementation sketch (`dbadmin_postgres.go`)

```go
func provisionUser(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
    username := cmd.Params["username"].(string)
    password, generated := resolvePassword(cmd.Params)
    result := &Result{Fields: map[string]string{}}

    // CREATE ROLE is idempotent-guarded via IF NOT EXISTS (PG 9.6+)
    _, err := db.ExecContext(ctx,
        fmt.Sprintf(`CREATE ROLE %s WITH LOGIN PASSWORD $1`, pgIdent(username)), password)
    if err != nil {
        return nil, fmt.Errorf("create role: %w", err)
    }

    if err := applyGrants(ctx, db, username, cmd.Params["grants"]); err != nil {
        // Best-effort rollback: drop the role we just created
        _, _ = db.ExecContext(ctx, fmt.Sprintf(`DROP ROLE IF EXISTS %s`, pgIdent(username)))
        return nil, err
    }

    if generated {
        result.Fields["generated_password"] = password
    }
    return result, nil
}

// resolvePassword returns the provided password or a crypto/rand-generated one.
func resolvePassword(params map[string]any) (string, bool) {
    if p, ok := params["password"].(string); ok && p != "" {
        return p, false
    }
    b := make([]byte, 18)
    if _, err := rand.Read(b); err != nil {
        panic(err) // crypto/rand failure is fatal
    }
    return base64.RawURLEncoding.EncodeToString(b), true
}

// pgIdent quotes an identifier safely (no user input in format strings).
func pgIdent(name string) string {
    return `"` + strings.ReplaceAll(name, `"`, `""`) + `"`
}
```

### Rollback

The rollback step calls `deprovision_db_user` using the same `username`. The `grants_applied` field from step output is used only for logging; no re-grant is needed because the user is dropped.

---

## Section 4: Change Type — `deprovision_db_user`

**Purpose:** Revoke all grants and drop a DB user. Records existing grants before dropping for audit and rollback.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db_type` | string | yes | |
| `db_host` | string | yes | |
| `db_port` | int | no | |
| `db_name` | string | yes | |
| `username` | string | yes | User to drop |
| `connector_id` | string | yes | |

### Step Output (encrypted at rest)

```json
{
  "pre_drop_grants": "GRANT SELECT ON myschema.orders TO \"appuser\"; ..."
}
```

The `pre_drop_grants` blob is the output of `\dp` / `SHOW GRANTS FOR` / `sys.database_permissions` captured before the drop, serialized as a newline-separated list of GRANT statements. Rollback can replay these to restore the user if re-created.

### Agent implementation sketch (`dbadmin_postgres.go`)

```go
func deprovisionUser(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
    username := cmd.Params["username"].(string)

    grants, err := captureGrants(ctx, db, username)
    if err != nil {
        return nil, fmt.Errorf("capture grants: %w", err)
    }

    // Reassign owned objects to the session user, then drop
    _, err = db.ExecContext(ctx,
        fmt.Sprintf(`REASSIGN OWNED BY %s TO CURRENT_USER`, pgIdent(username)))
    if err != nil {
        return nil, fmt.Errorf("reassign owned: %w", err)
    }
    _, err = db.ExecContext(ctx,
        fmt.Sprintf(`DROP OWNED BY %s`, pgIdent(username)))
    if err != nil {
        return nil, fmt.Errorf("drop owned: %w", err)
    }
    _, err = db.ExecContext(ctx,
        fmt.Sprintf(`DROP ROLE IF EXISTS %s`, pgIdent(username)))
    if err != nil {
        return nil, fmt.Errorf("drop role: %w", err)
    }

    return &Result{Fields: map[string]string{"pre_drop_grants": grants}}, nil
}
```

### Rollback

Rollback for a deprovisioning is inherently destructive — the user no longer exists. Rollback re-creates the user with a new generated password and replays `pre_drop_grants`. The new password is returned as step output. Operators must communicate the new credential to the affected service.

---

## Section 5: Change Type — `db_permission_change`

**Purpose:** Add or remove specific grants from an existing user without touching the user itself.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db_type` | string | yes | |
| `db_host` | string | yes | |
| `db_port` | int | no | |
| `db_name` | string | yes | |
| `target_user` | string | yes | Existing DB user |
| `grants_to_add` | []string | no | `["schema.table: PRIVILEGE,...]"` |
| `grants_to_revoke` | []string | no | Same format |
| `connector_id` | string | yes | |

### Grant string format

```
"<schema>.<table>: <PRIVILEGE>[,<PRIVILEGE>...]"
```

Special values: `"*.*"` for all tables in all schemas; `"schema.*"` for all tables in a schema. Parsed by `parseGrant()` in `dbadmin.go`.

### Agent commands

```go
// grant_db_permissions
func grantPermissions(ctx context.Context, db *sql.DB, cmd Command) (*Result, error)

// revoke_db_permissions (used for grants_to_revoke and as rollback)
func revokePermissions(ctx context.Context, db *sql.DB, cmd Command) (*Result, error)
```

### Step Output

```json
{
  "pre_change_grants": "<serialized grant list for target_user before change>"
}
```

### Rollback

Reverse every operation: `GRANT` for each item in `grants_to_revoke`, `REVOKE` for each item in `grants_to_add`. Uses `pre_change_grants` to verify state before reversing.

---

## Section 6: Change Type — `configure_db_audit`

**Purpose:** Enable or reconfigure database audit logging. Records current configuration before changing it.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db_type` | string | yes | |
| `db_host` | string | yes | |
| `db_port` | int | no | |
| `connector_id` | string | yes | |
| `audit_level` | string | yes | `"ddl"`, `"dml"`, or `"all"` |
| `log_path` | string | no | Override log destination (PostgreSQL: `log_directory`; MySQL: `general_log_file`) |
| `enabled` | bool | yes | `true` to enable, `false` to disable |

### Engine-specific actions

**PostgreSQL (`pg_audit`):**
```sql
-- Check if pg_audit is loaded
SHOW shared_preload_libraries;
-- Set audit level (requires reload)
ALTER SYSTEM SET pgaudit.log = 'ddl,dml'; -- or 'all', 'ddl', 'dml'
SELECT pg_reload_conf();
```

**MySQL (`general_log` / `audit_log` plugin):**
```sql
SET GLOBAL general_log = 'ON';
SET GLOBAL general_log_file = '/var/log/mysql/audit.log';
```

**MSSQL (SQL Server Audit):**
Uses T-SQL `CREATE SERVER AUDIT` / `ALTER SERVER AUDIT` DDL; no `database/sql` exec limitation since T-SQL DDL is supported.

### Step Output

```json
{
  "pre_change_config": {
    "pgaudit.log": "none",
    "log_directory": "pg_log"
  }
}
```

### Rollback

Re-apply `pre_change_config` values via `ALTER SYSTEM SET` (PostgreSQL), `SET GLOBAL` (MySQL), or `ALTER SERVER AUDIT` (MSSQL), then reload.

---

## Section 7: Change Type — `promote_db_replica` (AWS Connector Action)

**Purpose:** Promote an RDS read replica to a standalone writable instance and optionally update a Route 53 CNAME to point to the new primary.

**This is a connector action, not an agent command.** The Python backend calls the AWS SDK directly using the connector's IAM credentials.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `connector_id` | string | yes | AWS connector with `rds:PromoteReadReplicaDBInstance` and `route53:ChangeResourceRecordSets` |
| `replica_identifier` | string | yes | RDS DB instance identifier of the replica |
| `update_dns_record` | bool | no | Whether to update a Route 53 record after promotion |
| `dns_hosted_zone_id` | string | conditional | Route 53 hosted zone ID (required if `update_dns_record` is true) |
| `dns_record_name` | string | conditional | FQDN to point at the new primary endpoint |

### Blast Radius Warning

Before approval is requested, the change preview renders:

> **High risk — read replica promotion**
> Promoting `{replica_identifier}` will:
> - Make the replica **writable** as a standalone instance
> - **Permanently disconnect** it from its source DB
> - This action **cannot be undone** via rollback — the replica cannot be re-attached
> If `update_dns_record` is true, `{dns_record_name}` will be updated to point to `{new_endpoint}`

Requires explicit approval. Approval gate is enforced at the change-execution layer.

### Connector action flow (`backend/connectors/aws/rds_promote.py`)

```python
async def promote_db_replica(connector: AWSConnector, params: dict) -> dict:
    rds = connector.session.client("rds")

    # 1. Initiate promotion
    rds.promote_read_replica(DBInstanceIdentifier=params["replica_identifier"])

    # 2. Wait for instance to become available
    waiter = rds.get_waiter("db_instance_available")
    waiter.wait(DBInstanceIdentifier=params["replica_identifier"])

    # 3. Fetch new endpoint
    desc = rds.describe_db_instances(DBInstanceIdentifier=params["replica_identifier"])
    new_endpoint = desc["DBInstances"][0]["Endpoint"]["Address"]

    # 4. Verify writes are accepted
    _verify_writable(new_endpoint, connector)

    # 5. Update DNS if requested
    if params.get("update_dns_record"):
        r53 = connector.session.client("route53")
        r53.change_resource_record_sets(
            HostedZoneId=params["dns_hosted_zone_id"],
            ChangeBatch={
                "Changes": [{
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": params["dns_record_name"],
                        "Type": "CNAME",
                        "TTL": 60,
                        "ResourceRecords": [{"Value": new_endpoint}],
                    },
                }]
            },
        )

    return {"new_endpoint": new_endpoint, "dns_updated": params.get("update_dns_record", False)}
```

### Rollback

Not available. The promotion is irreversible at the RDS level. Rollback is documented as: set up a new read replica from the promoted instance if replication is needed again. The change runner marks rollback as `unsupported` for this change type.

---

## Section 8: Change Type — `db_connection_config`

**Purpose:** Update the maximum connection limit for a database server and reload/restart the service.

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db_type` | string | yes | |
| `db_host` | string | yes | |
| `db_port` | int | no | |
| `connector_id` | string | yes | |
| `max_connections` | int | yes | New connection limit |
| `reload_only` | bool | no | If true, attempt reload without restart (PostgreSQL only) |

### Engine-specific actions

**PostgreSQL:**
```sql
-- Capture current value
SHOW max_connections;
-- Apply change
ALTER SYSTEM SET max_connections = 500;
-- Reload (reload_only=true) or signal restart needed
SELECT pg_reload_conf();
-- Note: max_connections requires a full restart even after ALTER SYSTEM;
-- pg_reload_conf() alone will not apply it. The agent must restart the
-- pg service via systemd (Linux) or SCM (Windows) unless reload_only=true,
-- in which case it applies the config and returns a warning that a restart
-- is required for max_connections to take effect.
```

**MySQL:**
```sql
-- Capture
SHOW VARIABLES LIKE 'max_connections';
-- Apply (takes effect immediately, no restart)
SET GLOBAL max_connections = 500;
-- Also persist to my.cnf via agent file write for durability
```

**MSSQL:**
```sql
-- sp_configure requires a restart
EXEC sp_configure 'max connections', 500;
RECONFIGURE;
-- Agent restarts SQL Server service via SCM
```

### Step Output

```json
{
  "pre_change_max_connections": "200",
  "restart_performed": "true"
}
```

### Rollback

Re-apply `pre_change_max_connections` using the same code path, then reload/restart.

---

## Section 9: Connector Credential Flow

The agent does not store DB credentials. At execution time:

1. The agent runner fetches the connector credential bundle for `connector_id` from the control plane (existing credential fetch path).
2. The credential bundle for a DB connector contains: `db_host`, `db_port`, `db_name`, `admin_username`, `admin_password`.
3. The runner assembles `AdminDSN` and injects it into `Command.AdminDSN` before calling `dbadmin.Execute`.
4. `AdminDSN` is never logged. It is held in memory only for the duration of the command.

DSN format per engine:

```
postgres:  postgres://<user>:<pass>@<host>:<port>/<db>?sslmode=require
mysql:     <user>:<pass>@tcp(<host>:<port>)/<db>?tls=true
mssql:     sqlserver://<user>:<pass>@<host>:<port>?database=<db>&encrypt=true
```

---

## Files Changed

| File | Change |
|------|--------|
| `agent/go.mod` | Add `github.com/lib/pq`, `go-sql-driver/mysql`, `microsoft/go-mssqldb` |
| `agent/commands/dbadmin/dbadmin.go` | New — dispatcher, `openDB`, `Execute`, `parseGrant`, `resolvePassword` |
| `agent/commands/dbadmin/dbadmin_postgres.go` | New — PostgreSQL implementations of all five actions |
| `agent/commands/dbadmin/dbadmin_mysql.go` | New — MySQL implementations |
| `agent/commands/dbadmin/dbadmin_windows.go` | New — MSSQL implementations |
| `agent/commands/registry.go` | Register `provision_db_user`, `deprovision_db_user`, `db_permission_change`, `configure_db_audit`, `db_connection_config` |
| `backend/connectors/aws/rds_promote.py` | New — `promote_db_replica` connector action |
| `backend/change_types/db_admin.py` | New — Pydantic models and validation for all six change types |
| `backend/routers/changes.py` | Wire `promote_db_replica` to the connector action dispatch path |
| `frontend/src/change-types/dbadmin/` | New — form components for each change type parameter set |
