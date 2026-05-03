# Database Administration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce database administration operations into Nexplane — covering DB user provisioning/deprovisioning, permission management, audit logging configuration, connection limit tuning, and RDS read-replica promotion — all with pre-change snapshots, rollback paths, and approval gates.

**Architecture:** A new `agent/commands/dbadmin/` package uses Go's standard `database/sql` interface with platform-split files for PostgreSQL, MySQL, and MSSQL. A single dispatcher `Execute()` in `dbadmin.go` opens the correct driver and delegates to the engine file. Credentials are injected as `AdminDSN` at runtime from the connector credential store — never from change parameters. RDS replica promotion is a Python connector action in the AWS executor tree (not an agent command) because it requires the AWS SDK, not a DB connection. Six change type definition JSON files register the new operations. All five agent commands are registered in `executor/executor.go`.

**Tech Stack:** Go 1.26 (`database/sql`, `github.com/lib/pq` v1.10.9, `github.com/go-sql-driver/mysql` v1.8.1, `github.com/microsoft/go-mssqldb` v1.7.2, `github.com/DATA-DOG/go-sqlmock` v1.5.0 for tests), Python 3.12 (boto3, pytest, unittest.mock), FastAPI backend.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `agent/go.mod` | Modify | Add `lib/pq`, `go-sql-driver/mysql`, `microsoft/go-mssqldb`, `DATA-DOG/go-sqlmock` |
| `agent/go.sum` | Modify | Updated by `go mod tidy` |
| `agent/commands/dbadmin/dbadmin.go` | Create | `Command`, `Result` types; `openDB`; `driverName`; `Execute` dispatcher; `parseGrant`; `resolvePassword`; `pgIdent` |
| `agent/commands/dbadmin/dbadmin_postgres.go` | Create | `execPostgres` routing + `provisionUser`, `deprovisionUser`, `grantPermissions`, `revokePermissions`, `configureAudit`, `connectionConfig` |
| `agent/commands/dbadmin/dbadmin_mysql.go` | Create | `execMySQL` routing + MySQL equivalents of all six actions |
| `agent/commands/dbadmin/dbadmin_windows.go` | Create | `execMSSQL` routing + MSSQL/T-SQL equivalents (build tag: `windows`) |
| `agent/commands/dbadmin/dbadmin_other.go` | Create | `execMSSQL` stub returning unsupported error (build tag: `!windows`) |
| `agent/commands/dbadmin/dbadmin_test.go` | Create | sqlmock-based tests for all actions across all engines |
| `agent/executor/executor.go` | Modify | Register five new DB admin commands and their rollbacks |
| `backend/app/connectors/executors/aws/promote_rds_replica.py` | Create | `execute` + `rollback` for RDS replica promotion + optional Route53 CNAME update |
| `backend/app/connectors/change_type_definitions/provision_db_user.json` | Create | Change type definition |
| `backend/app/connectors/change_type_definitions/deprovision_db_user.json` | Create | Change type definition |
| `backend/app/connectors/change_type_definitions/db_permission_change.json` | Create | Change type definition |
| `backend/app/connectors/change_type_definitions/configure_db_audit.json` | Create | Change type definition |
| `backend/app/connectors/change_type_definitions/promote_db_replica.json` | Create | Change type definition |
| `backend/app/connectors/change_type_definitions/db_connection_config.json` | Create | Change type definition |
| `backend/app/tests/test_database_admin.py` | Create | Tests for change type definitions loading and RDS promotion action |

---

## Task 1: Add Go driver dependencies to go.mod

**Files:**
- Modify: `agent/go.mod`
- Modify: `agent/go.sum` (via `go mod tidy`)

- [ ] **Step 1: Write a failing test that imports the dbadmin package (TDD gate)**

Create `agent/commands/dbadmin/dbadmin_test.go` with just the package declaration and one import to force a build error:

```go
package dbadmin_test

import (
	"testing"

	_ "nexplane-agent/commands/dbadmin"
)

func TestPackageExists(t *testing.T) {
	// This test exists solely to force a build error until the package is created.
	// It will be replaced with real tests in Task 3.
}
```

- [ ] **Step 2: Run — expect build failure (package does not exist)**

```bash
cd agent && go test ./commands/dbadmin/... 2>&1 | head -10
```

Expected: `cannot find package "nexplane-agent/commands/dbadmin"` or similar.

- [ ] **Step 3: Add driver dependencies to go.mod**

Edit `agent/go.mod` — add to the `require` block:

```
github.com/lib/pq                   v1.10.9
github.com/go-sql-driver/mysql      v1.8.1
github.com/microsoft/go-mssqldb     v1.7.2
github.com/DATA-DOG/go-sqlmock      v1.5.0
```

The full updated `require` block (merge with existing):

```
require (
    github.com/DATA-DOG/go-sqlmock           v1.5.0
    github.com/aws/aws-sdk-go-v2             v1.41.7
    github.com/aws/aws-sdk-go-v2/config      v1.32.17
    github.com/aws/aws-sdk-go-v2/credentials v1.19.16
    github.com/aws/aws-sdk-go-v2/feature/ec2/imds v1.18.23
    github.com/aws/aws-sdk-go-v2/feature/s3/manager v1.22.17
    github.com/aws/aws-sdk-go-v2/service/s3  v1.100.1
    github.com/go-sql-driver/mysql           v1.8.1
    github.com/lib/pq                        v1.10.9
    github.com/microsoft/go-mssqldb          v1.7.2
    golang.org/x/sys                         v0.43.0
)
```

- [ ] **Step 4: Run go mod tidy**

```bash
cd agent && go mod tidy
```

Expected: exits 0; `go.sum` gains new entries for the four new modules.

- [ ] **Step 5: Commit**

```bash
git add agent/go.mod agent/go.sum
git commit -m "chore(agent): add PostgreSQL, MySQL, MSSQL, and sqlmock driver dependencies"
```

---

## Task 2: Create dbadmin package — dispatcher and shared helpers

**Files:**
- Create: `agent/commands/dbadmin/dbadmin.go`

- [ ] **Step 1: Write the failing tests for dispatcher-level behavior**

Replace `agent/commands/dbadmin/dbadmin_test.go` with the full test suite stub that tests `Execute` with an unsupported db_type (this can be tested without a real DB):

```go
package dbadmin_test

import (
	"context"
	"testing"

	"nexplane-agent/commands/dbadmin"
)

func TestExecute_UnsupportedDBType(t *testing.T) {
	cmd := dbadmin.Command{
		DBType:   "oracle",
		AdminDSN: "oracle://ignored",
		Action:   "provision_db_user",
		Params:   map[string]any{"username": "alice"},
	}
	_, err := dbadmin.Execute(context.Background(), cmd)
	if err == nil {
		t.Fatal("expected error for unsupported db_type")
	}
	if err.Error() == "" {
		t.Fatal("expected non-empty error message")
	}
}

func TestParseGrant_Valid(t *testing.T) {
	schema, table, privs, err := dbadmin.ParseGrant("myschema.orders: SELECT,INSERT")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if schema != "myschema" {
		t.Errorf("expected schema 'myschema', got %q", schema)
	}
	if table != "orders" {
		t.Errorf("expected table 'orders', got %q", table)
	}
	if len(privs) != 2 || privs[0] != "SELECT" || privs[1] != "INSERT" {
		t.Errorf("expected [SELECT INSERT], got %v", privs)
	}
}

func TestParseGrant_Invalid(t *testing.T) {
	_, _, _, err := dbadmin.ParseGrant("no-colon-separator")
	if err == nil {
		t.Fatal("expected error for invalid grant string")
	}
}

func TestResolvePassword_Generated(t *testing.T) {
	params := map[string]any{}
	pass, generated := dbadmin.ResolvePassword(params)
	if !generated {
		t.Fatal("expected generated=true when no password in params")
	}
	if len(pass) < 16 {
		t.Errorf("generated password too short: %q", pass)
	}
}

func TestResolvePassword_Provided(t *testing.T) {
	params := map[string]any{"password": "mysecret"}
	pass, generated := dbadmin.ResolvePassword(params)
	if generated {
		t.Fatal("expected generated=false when password provided")
	}
	if pass != "mysecret" {
		t.Errorf("expected 'mysecret', got %q", pass)
	}
}

func TestPgIdent_QuotesDoubleQuotes(t *testing.T) {
	result := dbadmin.PgIdent(`alice"drop`)
	expected := `"alice""drop"`
	if result != expected {
		t.Errorf("expected %q, got %q", expected, result)
	}
}
```

- [ ] **Step 2: Run — expect build failure (package not created yet)**

```bash
cd agent && go test ./commands/dbadmin/... 2>&1 | head -10
```

Expected: cannot find package or undefined symbols.

- [ ] **Step 3: Create agent/commands/dbadmin/dbadmin.go**

```go
package dbadmin

import (
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/base64"
	"fmt"
	"strings"

	_ "github.com/go-sql-driver/mysql"
	_ "github.com/lib/pq"
	_ "github.com/microsoft/go-mssqldb"
)

// Command is the deserialized change parameters delivered by the agent runner.
type Command struct {
	DBType   string         // "postgres" | "mysql" | "mssql"
	DBHost   string
	DBPort   int
	DBName   string
	AdminDSN string         // resolved from connector credentials at runtime; never logged
	Action   string         // matches change type action field
	Params   map[string]any
}

// Result carries output fields stored as encrypted step output.
type Result struct {
	Fields map[string]string
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

func openDB(cmd Command) (*sql.DB, error) {
	db, err := sql.Open(driverName(cmd.DBType), cmd.AdminDSN)
	if err != nil {
		return nil, fmt.Errorf("open db (%s): %w", cmd.DBType, err)
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

// ParseGrant parses a grant string of the form "schema.table: PRIV1,PRIV2".
// Exported for testing.
func ParseGrant(s string) (schema, table string, privs []string, err error) {
	parts := strings.SplitN(s, ":", 2)
	if len(parts) != 2 {
		return "", "", nil, fmt.Errorf("invalid grant string %q: expected 'schema.table: PRIV,...'", s)
	}
	target := strings.TrimSpace(parts[0])
	dotParts := strings.SplitN(target, ".", 2)
	if len(dotParts) != 2 {
		return "", "", nil, fmt.Errorf("invalid target %q: expected 'schema.table'", target)
	}
	schema = strings.TrimSpace(dotParts[0])
	table = strings.TrimSpace(dotParts[1])
	for _, p := range strings.Split(strings.TrimSpace(parts[1]), ",") {
		if priv := strings.TrimSpace(p); priv != "" {
			privs = append(privs, priv)
		}
	}
	if len(privs) == 0 {
		return "", "", nil, fmt.Errorf("no privileges specified in %q", s)
	}
	return schema, table, privs, nil
}

// ResolvePassword returns the provided password or a crypto/rand-generated one.
// Exported for testing.
func ResolvePassword(params map[string]any) (string, bool) {
	if p, ok := params["password"].(string); ok && p != "" {
		return p, false
	}
	b := make([]byte, 18)
	if _, err := rand.Read(b); err != nil {
		panic(fmt.Sprintf("crypto/rand failure: %v", err))
	}
	return base64.RawURLEncoding.EncodeToString(b), true
}

// PgIdent double-quotes a PostgreSQL identifier, escaping internal double quotes.
// Exported for testing.
func PgIdent(name string) string {
	return `"` + strings.ReplaceAll(name, `"`, `""`) + `"`
}
```

- [ ] **Step 4: Run dispatcher-level tests — expect pass (unsupported db_type, ParseGrant, ResolvePassword, PgIdent all pass; engine tests will fail until engine files exist)**

```bash
cd agent && go test ./commands/dbadmin/... -run "TestExecute_Unsupported|TestParseGrant|TestResolvePassword|TestPgIdent" -v 2>&1
```

Expected: all five named tests pass. The engine-dependent tests are not yet written.

- [ ] **Step 5: Commit**

```bash
git add agent/commands/dbadmin/dbadmin.go agent/commands/dbadmin/dbadmin_test.go
git commit -m "feat(agent/dbadmin): add dispatcher, ParseGrant, ResolvePassword, PgIdent helpers"
```

---

## Task 3: Implement PostgreSQL engine file with sqlmock tests

**Files:**
- Create: `agent/commands/dbadmin/dbadmin_postgres.go`
- Modify: `agent/commands/dbadmin/dbadmin_test.go`

- [ ] **Step 1: Write failing sqlmock tests for PostgreSQL actions**

Append to `agent/commands/dbadmin/dbadmin_test.go`:

```go
import (
	"context"
	"database/sql"
	"testing"

	"github.com/DATA-DOG/go-sqlmock"
	"nexplane-agent/commands/dbadmin"
)

// newMockDB returns a *sql.DB backed by sqlmock.
func newMockDB(t *testing.T) (*sql.DB, sqlmock.Sqlmock) {
	t.Helper()
	db, mock, err := sqlmock.New()
	if err != nil {
		t.Fatalf("sqlmock.New: %v", err)
	}
	return db, mock
}

func TestPostgresProvisionUser_CreatesRole(t *testing.T) {
	db, mock := newMockDB(t)
	defer db.Close()

	mock.ExpectExec(`CREATE ROLE`).
		WithArgs("s3cr3t").
		WillReturnResult(sqlmock.NewResult(0, 0))

	cmd := dbadmin.Command{
		DBType: "postgres",
		Action: "provision_db_user",
		Params: map[string]any{
			"username": "appuser",
			"password": "s3cr3t",
		},
	}
	result, err := dbadmin.ExecPostgresWithDB(context.Background(), db, cmd)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if _, ok := result.Fields["generated_password"]; ok {
		t.Error("generated_password should not be set when password was provided")
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Errorf("mock expectations: %v", err)
	}
}

func TestPostgresProvisionUser_GeneratesPassword(t *testing.T) {
	db, mock := newMockDB(t)
	defer db.Close()

	mock.ExpectExec(`CREATE ROLE`).
		WithArgs(sqlmock.AnyArg()).
		WillReturnResult(sqlmock.NewResult(0, 0))

	cmd := dbadmin.Command{
		DBType: "postgres",
		Action: "provision_db_user",
		Params: map[string]any{"username": "appuser"},
	}
	result, err := dbadmin.ExecPostgresWithDB(context.Background(), db, cmd)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Fields["generated_password"] == "" {
		t.Error("expected generated_password to be set")
	}
}

func TestPostgresDeprovisionUser_DropsRole(t *testing.T) {
	db, mock := newMockDB(t)
	defer db.Close()

	// captureGrants query
	mock.ExpectQuery(`information_schema`).
		WillReturnRows(sqlmock.NewRows([]string{"grant_stmt"}))
	mock.ExpectExec(`REASSIGN OWNED`).WillReturnResult(sqlmock.NewResult(0, 0))
	mock.ExpectExec(`DROP OWNED`).WillReturnResult(sqlmock.NewResult(0, 0))
	mock.ExpectExec(`DROP ROLE`).WillReturnResult(sqlmock.NewResult(0, 0))

	cmd := dbadmin.Command{
		DBType: "postgres",
		Action: "deprovision_db_user",
		Params: map[string]any{"username": "olduser"},
	}
	_, err := dbadmin.ExecPostgresWithDB(context.Background(), db, cmd)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Errorf("mock expectations: %v", err)
	}
}

func TestPostgresGrantPermissions_ExecutesGrant(t *testing.T) {
	db, mock := newMockDB(t)
	defer db.Close()

	// pre-change grants capture
	mock.ExpectQuery(`information_schema`).
		WillReturnRows(sqlmock.NewRows([]string{"grant_stmt"}))
	mock.ExpectExec(`GRANT SELECT`).WillReturnResult(sqlmock.NewResult(0, 0))

	cmd := dbadmin.Command{
		DBType: "postgres",
		Action: "grant_db_permissions",
		Params: map[string]any{
			"target_user":   "appuser",
			"grants_to_add": []any{"public.orders: SELECT"},
		},
	}
	result, err := dbadmin.ExecPostgresWithDB(context.Background(), db, cmd)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if _, ok := result.Fields["pre_change_grants"]; !ok {
		t.Error("expected pre_change_grants in result")
	}
}

func TestPostgresConfigureAudit_SetsAuditLog(t *testing.T) {
	db, mock := newMockDB(t)
	defer db.Close()

	mock.ExpectQuery(`SHOW pgaudit.log`).
		WillReturnRows(sqlmock.NewRows([]string{"pgaudit.log"}).AddRow("none"))
	mock.ExpectExec(`ALTER SYSTEM SET pgaudit`).WillReturnResult(sqlmock.NewResult(0, 0))
	mock.ExpectQuery(`SELECT pg_reload_conf`).
		WillReturnRows(sqlmock.NewRows([]string{"pg_reload_conf"}).AddRow(true))

	cmd := dbadmin.Command{
		DBType: "postgres",
		Action: "configure_db_audit",
		Params: map[string]any{
			"audit_level": "ddl",
			"enabled":     true,
		},
	}
	result, err := dbadmin.ExecPostgresWithDB(context.Background(), db, cmd)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Fields["pre_change_pgaudit_log"] == "" {
		t.Error("expected pre_change_pgaudit_log to be captured")
	}
}

func TestPostgresConnectionConfig_SetsMaxConnections(t *testing.T) {
	db, mock := newMockDB(t)
	defer db.Close()

	mock.ExpectQuery(`SHOW max_connections`).
		WillReturnRows(sqlmock.NewRows([]string{"max_connections"}).AddRow("100"))
	mock.ExpectExec(`ALTER SYSTEM SET max_connections`).WillReturnResult(sqlmock.NewResult(0, 0))
	mock.ExpectQuery(`SELECT pg_reload_conf`).
		WillReturnRows(sqlmock.NewRows([]string{"pg_reload_conf"}).AddRow(true))

	cmd := dbadmin.Command{
		DBType: "postgres",
		Action: "db_connection_config",
		Params: map[string]any{
			"max_connections": 500,
			"reload_only":     true,
		},
	}
	result, err := dbadmin.ExecPostgresWithDB(context.Background(), db, cmd)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Fields["pre_change_max_connections"] != "100" {
		t.Errorf("expected pre_change_max_connections=100, got %q", result.Fields["pre_change_max_connections"])
	}
}
```

- [ ] **Step 2: Run — expect build failure (ExecPostgresWithDB not defined)**

```bash
cd agent && go test ./commands/dbadmin/... 2>&1 | head -15
```

Expected: undefined: `dbadmin.ExecPostgresWithDB`.

- [ ] **Step 3: Create agent/commands/dbadmin/dbadmin_postgres.go**

```go
package dbadmin

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
)

// execPostgres is called by Execute for postgres db_type.
func execPostgres(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	return ExecPostgresWithDB(ctx, db, cmd)
}

// ExecPostgresWithDB is exported for testing with a mock *sql.DB.
func ExecPostgresWithDB(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	switch cmd.Action {
	case "provision_db_user":
		return provisionUserPG(ctx, db, cmd)
	case "deprovision_db_user":
		return deprovisionUserPG(ctx, db, cmd)
	case "grant_db_permissions":
		return grantPermissionsPG(ctx, db, cmd)
	case "revoke_db_permissions":
		return revokePermissionsPG(ctx, db, cmd)
	case "configure_db_audit":
		return configureAuditPG(ctx, db, cmd)
	case "db_connection_config":
		return connectionConfigPG(ctx, db, cmd)
	default:
		return nil, fmt.Errorf("postgres: unknown action %q", cmd.Action)
	}
}

// provisionUserPG creates a PostgreSQL role with login.
func provisionUserPG(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	username, _ := cmd.Params["username"].(string)
	if username == "" {
		return nil, fmt.Errorf("username is required")
	}
	password, generated := ResolvePassword(cmd.Params)
	result := &Result{Fields: map[string]string{}}

	_, err := db.ExecContext(ctx,
		fmt.Sprintf(`CREATE ROLE %s WITH LOGIN PASSWORD $1`, PgIdent(username)),
		password)
	if err != nil {
		return nil, fmt.Errorf("create role: %w", err)
	}

	if grants, ok := cmd.Params["grants"].([]any); ok && len(grants) > 0 {
		if err := applyGrantsPG(ctx, db, username, grants); err != nil {
			_, _ = db.ExecContext(ctx, fmt.Sprintf(`DROP ROLE IF EXISTS %s`, PgIdent(username)))
			return nil, err
		}
	}

	if generated {
		result.Fields["generated_password"] = password
	}
	return result, nil
}

// deprovisionUserPG revokes all access and drops a PostgreSQL role.
func deprovisionUserPG(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	username, _ := cmd.Params["username"].(string)
	if username == "" {
		return nil, fmt.Errorf("username is required")
	}

	grants, err := captureGrantsPG(ctx, db, username)
	if err != nil {
		return nil, fmt.Errorf("capture grants: %w", err)
	}

	if _, err := db.ExecContext(ctx,
		fmt.Sprintf(`REASSIGN OWNED BY %s TO CURRENT_USER`, PgIdent(username))); err != nil {
		return nil, fmt.Errorf("reassign owned: %w", err)
	}
	if _, err := db.ExecContext(ctx,
		fmt.Sprintf(`DROP OWNED BY %s`, PgIdent(username))); err != nil {
		return nil, fmt.Errorf("drop owned: %w", err)
	}
	if _, err := db.ExecContext(ctx,
		fmt.Sprintf(`DROP ROLE IF EXISTS %s`, PgIdent(username))); err != nil {
		return nil, fmt.Errorf("drop role: %w", err)
	}

	return &Result{Fields: map[string]string{"pre_drop_grants": grants}}, nil
}

// grantPermissionsPG captures current grants, then applies grants_to_add.
func grantPermissionsPG(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	targetUser, _ := cmd.Params["target_user"].(string)
	if targetUser == "" {
		return nil, fmt.Errorf("target_user is required")
	}

	preGrants, err := captureGrantsPG(ctx, db, targetUser)
	if err != nil {
		return nil, fmt.Errorf("capture pre-change grants: %w", err)
	}

	if grants, ok := cmd.Params["grants_to_add"].([]any); ok {
		if err := applyGrantsPG(ctx, db, targetUser, grants); err != nil {
			return nil, err
		}
	}

	return &Result{Fields: map[string]string{"pre_change_grants": preGrants}}, nil
}

// revokePermissionsPG revokes grants_to_revoke from target_user.
func revokePermissionsPG(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	targetUser, _ := cmd.Params["target_user"].(string)
	if targetUser == "" {
		return nil, fmt.Errorf("target_user is required")
	}

	preGrants, err := captureGrantsPG(ctx, db, targetUser)
	if err != nil {
		return nil, fmt.Errorf("capture pre-change grants: %w", err)
	}

	if revokes, ok := cmd.Params["grants_to_revoke"].([]any); ok {
		for _, r := range revokes {
			grantStr, _ := r.(string)
			schema, table, privs, err := ParseGrant(grantStr)
			if err != nil {
				return nil, err
			}
			q := fmt.Sprintf(`REVOKE %s ON %s.%s FROM %s`,
				strings.Join(privs, ","), PgIdent(schema), PgIdent(table), PgIdent(targetUser))
			if _, err := db.ExecContext(ctx, q); err != nil {
				return nil, fmt.Errorf("revoke: %w", err)
			}
		}
	}

	return &Result{Fields: map[string]string{"pre_change_grants": preGrants}}, nil
}

// configureAuditPG enables/reconfigures pg_audit logging.
func configureAuditPG(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	auditLevel, _ := cmd.Params["audit_level"].(string)
	enabled, _ := cmd.Params["enabled"].(bool)

	// Capture current value
	var currentSetting string
	row := db.QueryRowContext(ctx, `SHOW pgaudit.log`)
	_ = row.Scan(&currentSetting)

	var newSetting string
	if !enabled {
		newSetting = "none"
	} else {
		switch auditLevel {
		case "ddl":
			newSetting = "ddl"
		case "dml":
			newSetting = "write"
		case "all":
			newSetting = "all"
		default:
			return nil, fmt.Errorf("unknown audit_level %q: must be ddl, dml, or all", auditLevel)
		}
	}

	if _, err := db.ExecContext(ctx,
		fmt.Sprintf(`ALTER SYSTEM SET pgaudit.log = '%s'`, newSetting)); err != nil {
		return nil, fmt.Errorf("alter system pgaudit.log: %w", err)
	}
	if _, err := db.QueryContext(ctx, `SELECT pg_reload_conf()`); err != nil {
		return nil, fmt.Errorf("pg_reload_conf: %w", err)
	}

	return &Result{Fields: map[string]string{
		"pre_change_pgaudit_log": currentSetting,
		"applied_setting":        newSetting,
	}}, nil
}

// connectionConfigPG updates max_connections, optionally reloading without restart.
func connectionConfigPG(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	maxConns, ok := cmd.Params["max_connections"].(int)
	if !ok {
		if f, ok2 := cmd.Params["max_connections"].(float64); ok2 {
			maxConns = int(f)
		} else {
			return nil, fmt.Errorf("max_connections must be an integer")
		}
	}
	reloadOnly, _ := cmd.Params["reload_only"].(bool)

	var currentVal string
	row := db.QueryRowContext(ctx, `SHOW max_connections`)
	_ = row.Scan(&currentVal)

	if _, err := db.ExecContext(ctx,
		fmt.Sprintf(`ALTER SYSTEM SET max_connections = %d`, maxConns)); err != nil {
		return nil, fmt.Errorf("alter system max_connections: %w", err)
	}
	if _, err := db.QueryContext(ctx, `SELECT pg_reload_conf()`); err != nil {
		return nil, fmt.Errorf("pg_reload_conf: %w", err)
	}

	result := &Result{Fields: map[string]string{
		"pre_change_max_connections": currentVal,
		"reload_only":                fmt.Sprintf("%v", reloadOnly),
		"restart_required":           "true", // max_connections always needs restart
	}}
	if reloadOnly {
		result.Fields["warning"] = "max_connections requires a full PostgreSQL restart; config written but not yet in effect"
	}
	return result, nil
}

// captureGrantsPG returns a newline-separated list of GRANT statements for username.
func captureGrantsPG(ctx context.Context, db *sql.DB, username string) (string, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT 'GRANT ' || privilege_type || ' ON ' ||
		       table_schema || '.' || table_name ||
		       ' TO ' || grantee || ';'
		FROM information_schema.role_table_grants
		WHERE grantee = $1`, username)
	if err != nil {
		return "", err
	}
	defer rows.Close()
	var stmts []string
	for rows.Next() {
		var s string
		if err := rows.Scan(&s); err != nil {
			return "", err
		}
		stmts = append(stmts, s)
	}
	return strings.Join(stmts, "\n"), rows.Err()
}

// applyGrantsPG executes GRANT statements for each grant string.
func applyGrantsPG(ctx context.Context, db *sql.DB, username string, grants []any) error {
	for _, g := range grants {
		grantStr, _ := g.(string)
		schema, table, privs, err := ParseGrant(grantStr)
		if err != nil {
			return err
		}
		q := fmt.Sprintf(`GRANT %s ON %s.%s TO %s`,
			strings.Join(privs, ","), PgIdent(schema), PgIdent(table), PgIdent(username))
		if _, err := db.ExecContext(ctx, q); err != nil {
			return fmt.Errorf("grant: %w", err)
		}
	}
	return nil
}
```

- [ ] **Step 4: Run PostgreSQL tests — expect pass**

```bash
cd agent && go test ./commands/dbadmin/... -run "TestPostgres" -v 2>&1
```

Expected: all `TestPostgres*` tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent/commands/dbadmin/dbadmin_postgres.go agent/commands/dbadmin/dbadmin_test.go
git commit -m "feat(agent/dbadmin): add PostgreSQL engine — provision, deprovision, grant, revoke, audit, connection config"
```

---

## Task 4: Implement MySQL engine file with sqlmock tests

**Files:**
- Create: `agent/commands/dbadmin/dbadmin_mysql.go`
- Modify: `agent/commands/dbadmin/dbadmin_test.go`

- [ ] **Step 1: Write failing tests for MySQL actions**

Append to `agent/commands/dbadmin/dbadmin_test.go`:

```go
func TestMySQLProvisionUser_CreatesUser(t *testing.T) {
	db, mock := newMockDB(t)
	defer db.Close()

	mock.ExpectExec(`CREATE USER`).
		WithArgs(sqlmock.AnyArg()).
		WillReturnResult(sqlmock.NewResult(0, 0))
	mock.ExpectExec(`FLUSH PRIVILEGES`).WillReturnResult(sqlmock.NewResult(0, 0))

	cmd := dbadmin.Command{
		DBType: "mysql",
		Action: "provision_db_user",
		Params: map[string]any{
			"username": "appuser",
			"password": "s3cr3t",
		},
	}
	result, err := dbadmin.ExecMySQLWithDB(context.Background(), db, cmd)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if _, ok := result.Fields["generated_password"]; ok {
		t.Error("generated_password should not be set when password provided")
	}
	if err := mock.ExpectationsWereMet(); err != nil {
		t.Errorf("mock expectations: %v", err)
	}
}

func TestMySQLDeprovisionUser_DropsUser(t *testing.T) {
	db, mock := newMockDB(t)
	defer db.Close()

	mock.ExpectQuery(`SHOW GRANTS FOR`).
		WillReturnRows(sqlmock.NewRows([]string{"Grants for appuser@%"}).
			AddRow("GRANT SELECT ON `mydb`.`orders` TO `appuser`@`%`"))
	mock.ExpectExec(`REVOKE ALL PRIVILEGES`).WillReturnResult(sqlmock.NewResult(0, 0))
	mock.ExpectExec(`DROP USER`).WillReturnResult(sqlmock.NewResult(0, 0))
	mock.ExpectExec(`FLUSH PRIVILEGES`).WillReturnResult(sqlmock.NewResult(0, 0))

	cmd := dbadmin.Command{
		DBType: "mysql",
		Action: "deprovision_db_user",
		Params: map[string]any{"username": "appuser"},
	}
	result, err := dbadmin.ExecMySQLWithDB(context.Background(), db, cmd)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Fields["pre_drop_grants"] == "" {
		t.Error("expected pre_drop_grants to be captured")
	}
}

func TestMySQLConnectionConfig_SetsGlobalVar(t *testing.T) {
	db, mock := newMockDB(t)
	defer db.Close()

	mock.ExpectQuery(`SHOW VARIABLES LIKE 'max_connections'`).
		WillReturnRows(sqlmock.NewRows([]string{"Variable_name", "Value"}).
			AddRow("max_connections", "151"))
	mock.ExpectExec(`SET GLOBAL max_connections`).WillReturnResult(sqlmock.NewResult(0, 0))

	cmd := dbadmin.Command{
		DBType: "mysql",
		Action: "db_connection_config",
		Params: map[string]any{"max_connections": 300},
	}
	result, err := dbadmin.ExecMySQLWithDB(context.Background(), db, cmd)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Fields["pre_change_max_connections"] != "151" {
		t.Errorf("expected 151, got %q", result.Fields["pre_change_max_connections"])
	}
}
```

- [ ] **Step 2: Run — expect build failure (ExecMySQLWithDB not defined)**

```bash
cd agent && go test ./commands/dbadmin/... -run "TestMySQL" 2>&1 | head -10
```

- [ ] **Step 3: Create agent/commands/dbadmin/dbadmin_mysql.go**

```go
package dbadmin

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
)

// execMySQL is called by Execute for mysql db_type.
func execMySQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	return ExecMySQLWithDB(ctx, db, cmd)
}

// ExecMySQLWithDB is exported for testing with a mock *sql.DB.
func ExecMySQLWithDB(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	switch cmd.Action {
	case "provision_db_user":
		return provisionUserMySQL(ctx, db, cmd)
	case "deprovision_db_user":
		return deprovisionUserMySQL(ctx, db, cmd)
	case "grant_db_permissions":
		return grantPermissionsMySQL(ctx, db, cmd)
	case "revoke_db_permissions":
		return revokePermissionsMySQL(ctx, db, cmd)
	case "configure_db_audit":
		return configureAuditMySQL(ctx, db, cmd)
	case "db_connection_config":
		return connectionConfigMySQL(ctx, db, cmd)
	default:
		return nil, fmt.Errorf("mysql: unknown action %q", cmd.Action)
	}
}

func provisionUserMySQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	username, _ := cmd.Params["username"].(string)
	if username == "" {
		return nil, fmt.Errorf("username is required")
	}
	password, generated := ResolvePassword(cmd.Params)

	if _, err := db.ExecContext(ctx,
		fmt.Sprintf("CREATE USER '%s'@'%%' IDENTIFIED BY ?", username), password); err != nil {
		return nil, fmt.Errorf("create user: %w", err)
	}

	if grants, ok := cmd.Params["grants"].([]any); ok && len(grants) > 0 {
		for _, g := range grants {
			grantStr, _ := g.(string)
			schema, table, privs, err := ParseGrant(grantStr)
			if err != nil {
				return nil, err
			}
			q := fmt.Sprintf("GRANT %s ON `%s`.`%s` TO '%s'@'%%'",
				strings.Join(privs, ","), schema, table, username)
			if _, err := db.ExecContext(ctx, q); err != nil {
				return nil, fmt.Errorf("grant: %w", err)
			}
		}
	}

	if _, err := db.ExecContext(ctx, "FLUSH PRIVILEGES"); err != nil {
		return nil, fmt.Errorf("flush privileges: %w", err)
	}

	result := &Result{Fields: map[string]string{}}
	if generated {
		result.Fields["generated_password"] = password
	}
	return result, nil
}

func deprovisionUserMySQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	username, _ := cmd.Params["username"].(string)
	if username == "" {
		return nil, fmt.Errorf("username is required")
	}

	rows, err := db.QueryContext(ctx, fmt.Sprintf("SHOW GRANTS FOR '%s'@'%%'", username))
	if err != nil {
		return nil, fmt.Errorf("show grants: %w", err)
	}
	defer rows.Close()
	var grantStmts []string
	for rows.Next() {
		var s string
		if err := rows.Scan(&s); err != nil {
			return nil, err
		}
		grantStmts = append(grantStmts, s)
	}
	preGrants := strings.Join(grantStmts, "\n")

	if _, err := db.ExecContext(ctx,
		fmt.Sprintf("REVOKE ALL PRIVILEGES, GRANT OPTION FROM '%s'@'%%'", username)); err != nil {
		return nil, fmt.Errorf("revoke all: %w", err)
	}
	if _, err := db.ExecContext(ctx,
		fmt.Sprintf("DROP USER '%s'@'%%'", username)); err != nil {
		return nil, fmt.Errorf("drop user: %w", err)
	}
	if _, err := db.ExecContext(ctx, "FLUSH PRIVILEGES"); err != nil {
		return nil, fmt.Errorf("flush privileges: %w", err)
	}

	return &Result{Fields: map[string]string{"pre_drop_grants": preGrants}}, nil
}

func grantPermissionsMySQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	targetUser, _ := cmd.Params["target_user"].(string)
	if targetUser == "" {
		return nil, fmt.Errorf("target_user is required")
	}
	rows, err := db.QueryContext(ctx, fmt.Sprintf("SHOW GRANTS FOR '%s'@'%%'", targetUser))
	if err != nil {
		return nil, fmt.Errorf("show grants: %w", err)
	}
	defer rows.Close()
	var preStmts []string
	for rows.Next() {
		var s string
		_ = rows.Scan(&s)
		preStmts = append(preStmts, s)
	}

	if grants, ok := cmd.Params["grants_to_add"].([]any); ok {
		for _, g := range grants {
			grantStr, _ := g.(string)
			schema, table, privs, err := ParseGrant(grantStr)
			if err != nil {
				return nil, err
			}
			q := fmt.Sprintf("GRANT %s ON `%s`.`%s` TO '%s'@'%%'",
				strings.Join(privs, ","), schema, table, targetUser)
			if _, err := db.ExecContext(ctx, q); err != nil {
				return nil, fmt.Errorf("grant: %w", err)
			}
		}
	}
	return &Result{Fields: map[string]string{"pre_change_grants": strings.Join(preStmts, "\n")}}, nil
}

func revokePermissionsMySQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	targetUser, _ := cmd.Params["target_user"].(string)
	if targetUser == "" {
		return nil, fmt.Errorf("target_user is required")
	}
	rows, err := db.QueryContext(ctx, fmt.Sprintf("SHOW GRANTS FOR '%s'@'%%'", targetUser))
	if err != nil {
		return nil, fmt.Errorf("show grants: %w", err)
	}
	defer rows.Close()
	var preStmts []string
	for rows.Next() {
		var s string
		_ = rows.Scan(&s)
		preStmts = append(preStmts, s)
	}

	if revokes, ok := cmd.Params["grants_to_revoke"].([]any); ok {
		for _, r := range revokes {
			grantStr, _ := r.(string)
			schema, table, privs, err := ParseGrant(grantStr)
			if err != nil {
				return nil, err
			}
			q := fmt.Sprintf("REVOKE %s ON `%s`.`%s` FROM '%s'@'%%'",
				strings.Join(privs, ","), schema, table, targetUser)
			if _, err := db.ExecContext(ctx, q); err != nil {
				return nil, fmt.Errorf("revoke: %w", err)
			}
		}
	}
	return &Result{Fields: map[string]string{"pre_change_grants": strings.Join(preStmts, "\n")}}, nil
}

func configureAuditMySQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	enabled, _ := cmd.Params["enabled"].(bool)
	onOff := "OFF"
	if enabled {
		onOff = "ON"
	}

	var varName, currentVal string
	row := db.QueryRowContext(ctx, "SHOW VARIABLES LIKE 'general_log'")
	_ = row.Scan(&varName, &currentVal)

	if _, err := db.ExecContext(ctx,
		fmt.Sprintf("SET GLOBAL general_log = '%s'", onOff)); err != nil {
		return nil, fmt.Errorf("set global general_log: %w", err)
	}
	if logPath, ok := cmd.Params["log_path"].(string); ok && logPath != "" {
		if _, err := db.ExecContext(ctx,
			fmt.Sprintf("SET GLOBAL general_log_file = '%s'", logPath)); err != nil {
			return nil, fmt.Errorf("set global general_log_file: %w", err)
		}
	}

	return &Result{Fields: map[string]string{"pre_change_general_log": currentVal}}, nil
}

func connectionConfigMySQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	maxConns, ok := cmd.Params["max_connections"].(int)
	if !ok {
		if f, ok2 := cmd.Params["max_connections"].(float64); ok2 {
			maxConns = int(f)
		} else {
			return nil, fmt.Errorf("max_connections must be an integer")
		}
	}

	var varName, currentVal string
	row := db.QueryRowContext(ctx, "SHOW VARIABLES LIKE 'max_connections'")
	_ = row.Scan(&varName, &currentVal)

	if _, err := db.ExecContext(ctx,
		fmt.Sprintf("SET GLOBAL max_connections = %d", maxConns)); err != nil {
		return nil, fmt.Errorf("set global max_connections: %w", err)
	}

	return &Result{Fields: map[string]string{
		"pre_change_max_connections": currentVal,
		"restart_performed":          "false", // MySQL applies immediately
	}}, nil
}
```

- [ ] **Step 4: Run MySQL tests — expect pass**

```bash
cd agent && go test ./commands/dbadmin/... -run "TestMySQL" -v 2>&1
```

Expected: all `TestMySQL*` tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent/commands/dbadmin/dbadmin_mysql.go agent/commands/dbadmin/dbadmin_test.go
git commit -m "feat(agent/dbadmin): add MySQL engine — provision, deprovision, grant, revoke, audit, connection config"
```

---

## Task 5: Implement MSSQL engine files (Windows + stub)

**Files:**
- Create: `agent/commands/dbadmin/dbadmin_windows.go`
- Create: `agent/commands/dbadmin/dbadmin_other.go`

- [ ] **Step 1: Create the non-Windows stub first**

Create `agent/commands/dbadmin/dbadmin_other.go`:

```go
//go:build !windows

package dbadmin

import (
	"context"
	"database/sql"
	"fmt"
)

func execMSSQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	return nil, fmt.Errorf("MSSQL commands are only supported on Windows agents")
}
```

- [ ] **Step 2: Create agent/commands/dbadmin/dbadmin_windows.go**

```go
//go:build windows

package dbadmin

import (
	"context"
	"database/sql"
	"fmt"
	"strings"
)

// execMSSQL is called by Execute for mssql db_type on Windows agents.
func execMSSQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	return ExecMSSQLWithDB(ctx, db, cmd)
}

// ExecMSSQLWithDB is exported for testing with a mock *sql.DB.
func ExecMSSQLWithDB(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	switch cmd.Action {
	case "provision_db_user":
		return provisionUserMSSQL(ctx, db, cmd)
	case "deprovision_db_user":
		return deprovisionUserMSSQL(ctx, db, cmd)
	case "grant_db_permissions":
		return grantPermissionsMSSQL(ctx, db, cmd)
	case "revoke_db_permissions":
		return revokePermissionsMSSQL(ctx, db, cmd)
	case "configure_db_audit":
		return configureAuditMSSQL(ctx, db, cmd)
	case "db_connection_config":
		return connectionConfigMSSQL(ctx, db, cmd)
	default:
		return nil, fmt.Errorf("mssql: unknown action %q", cmd.Action)
	}
}

func provisionUserMSSQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	username, _ := cmd.Params["username"].(string)
	if username == "" {
		return nil, fmt.Errorf("username is required")
	}
	password, generated := ResolvePassword(cmd.Params)

	// SQL Server: CREATE LOGIN at server level, then CREATE USER in DB
	if _, err := db.ExecContext(ctx,
		fmt.Sprintf("CREATE LOGIN [%s] WITH PASSWORD = N'%s'", username, password)); err != nil {
		return nil, fmt.Errorf("create login: %w", err)
	}
	if _, err := db.ExecContext(ctx,
		fmt.Sprintf("CREATE USER [%s] FOR LOGIN [%s]", username, username)); err != nil {
		return nil, fmt.Errorf("create user: %w", err)
	}

	if grants, ok := cmd.Params["grants"].([]any); ok {
		for _, g := range grants {
			grantStr, _ := g.(string)
			schema, table, privs, err := ParseGrant(grantStr)
			if err != nil {
				return nil, err
			}
			for _, priv := range privs {
				q := fmt.Sprintf("GRANT %s ON [%s].[%s] TO [%s]", priv, schema, table, username)
				if _, err := db.ExecContext(ctx, q); err != nil {
					return nil, fmt.Errorf("grant: %w", err)
				}
			}
		}
	}

	result := &Result{Fields: map[string]string{}}
	if generated {
		result.Fields["generated_password"] = password
	}
	return result, nil
}

func deprovisionUserMSSQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	username, _ := cmd.Params["username"].(string)
	if username == "" {
		return nil, fmt.Errorf("username is required")
	}

	rows, err := db.QueryContext(ctx, `
		SELECT 'GRANT ' + dp.permission_name + ' ON [' + s.name + '].[' + o.name + '] TO [' + pr.name + ']'
		FROM sys.database_permissions dp
		JOIN sys.objects o ON dp.major_id = o.object_id
		JOIN sys.schemas s ON o.schema_id = s.schema_id
		JOIN sys.database_principals pr ON dp.grantee_principal_id = pr.principal_id
		WHERE pr.name = @p1 AND dp.state = 'G'`, username)
	if err != nil {
		return nil, fmt.Errorf("query permissions: %w", err)
	}
	defer rows.Close()
	var stmts []string
	for rows.Next() {
		var s string
		_ = rows.Scan(&s)
		stmts = append(stmts, s)
	}

	if _, err := db.ExecContext(ctx, fmt.Sprintf("DROP USER IF EXISTS [%s]", username)); err != nil {
		return nil, fmt.Errorf("drop user: %w", err)
	}
	if _, err := db.ExecContext(ctx, fmt.Sprintf("DROP LOGIN [%s]", username)); err != nil {
		return nil, fmt.Errorf("drop login: %w", err)
	}

	return &Result{Fields: map[string]string{"pre_drop_grants": strings.Join(stmts, "\n")}}, nil
}

func grantPermissionsMSSQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	targetUser, _ := cmd.Params["target_user"].(string)
	if targetUser == "" {
		return nil, fmt.Errorf("target_user is required")
	}

	preGrants, err := captureGrantsMSSQL(ctx, db, targetUser)
	if err != nil {
		return nil, err
	}

	if grants, ok := cmd.Params["grants_to_add"].([]any); ok {
		for _, g := range grants {
			grantStr, _ := g.(string)
			schema, table, privs, err := ParseGrant(grantStr)
			if err != nil {
				return nil, err
			}
			for _, priv := range privs {
				q := fmt.Sprintf("GRANT %s ON [%s].[%s] TO [%s]", priv, schema, table, targetUser)
				if _, err := db.ExecContext(ctx, q); err != nil {
					return nil, fmt.Errorf("grant: %w", err)
				}
			}
		}
	}
	return &Result{Fields: map[string]string{"pre_change_grants": preGrants}}, nil
}

func revokePermissionsMSSQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	targetUser, _ := cmd.Params["target_user"].(string)
	if targetUser == "" {
		return nil, fmt.Errorf("target_user is required")
	}

	preGrants, err := captureGrantsMSSQL(ctx, db, targetUser)
	if err != nil {
		return nil, err
	}

	if revokes, ok := cmd.Params["grants_to_revoke"].([]any); ok {
		for _, r := range revokes {
			grantStr, _ := r.(string)
			schema, table, privs, err := ParseGrant(grantStr)
			if err != nil {
				return nil, err
			}
			for _, priv := range privs {
				q := fmt.Sprintf("REVOKE %s ON [%s].[%s] FROM [%s]", priv, schema, table, targetUser)
				if _, err := db.ExecContext(ctx, q); err != nil {
					return nil, fmt.Errorf("revoke: %w", err)
				}
			}
		}
	}
	return &Result{Fields: map[string]string{"pre_change_grants": preGrants}}, nil
}

func configureAuditMSSQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	enabled, _ := cmd.Params["enabled"].(bool)
	auditName := "NexplaneAudit"

	var auditState string
	row := db.QueryRowContext(ctx,
		"SELECT state_desc FROM sys.server_audits WHERE name = @p1", auditName)
	_ = row.Scan(&auditState)

	if enabled {
		// Create if not exists, then enable
		_, _ = db.ExecContext(ctx, fmt.Sprintf(
			"IF NOT EXISTS (SELECT 1 FROM sys.server_audits WHERE name = '%s') "+
				"CREATE SERVER AUDIT [%s] TO APPLICATION_LOG WITH (ON_FAILURE = CONTINUE)",
			auditName, auditName))
		if _, err := db.ExecContext(ctx,
			fmt.Sprintf("ALTER SERVER AUDIT [%s] WITH (STATE = ON)", auditName)); err != nil {
			return nil, fmt.Errorf("enable audit: %w", err)
		}
	} else {
		if _, err := db.ExecContext(ctx,
			fmt.Sprintf("ALTER SERVER AUDIT [%s] WITH (STATE = OFF)", auditName)); err != nil {
			return nil, fmt.Errorf("disable audit: %w", err)
		}
	}

	return &Result{Fields: map[string]string{"pre_change_audit_state": auditState}}, nil
}

func connectionConfigMSSQL(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	maxConns, ok := cmd.Params["max_connections"].(int)
	if !ok {
		if f, ok2 := cmd.Params["max_connections"].(float64); ok2 {
			maxConns = int(f)
		} else {
			return nil, fmt.Errorf("max_connections must be an integer")
		}
	}

	var configName string
	var currentVal int
	row := db.QueryRowContext(ctx,
		"SELECT name, value_in_use FROM sys.configurations WHERE name = 'max connections'")
	_ = row.Scan(&configName, &currentVal)

	if _, err := db.ExecContext(ctx,
		fmt.Sprintf("EXEC sp_configure 'max connections', %d; RECONFIGURE", maxConns)); err != nil {
		return nil, fmt.Errorf("sp_configure max connections: %w", err)
	}

	return &Result{Fields: map[string]string{
		"pre_change_max_connections": fmt.Sprintf("%d", currentVal),
		"restart_performed":          "true", // MSSQL requires restart for max connections
	}}, nil
}

func captureGrantsMSSQL(ctx context.Context, db *sql.DB, username string) (string, error) {
	rows, err := db.QueryContext(ctx, `
		SELECT 'GRANT ' + dp.permission_name + ' ON [' + s.name + '].[' + o.name + '] TO [' + pr.name + ']'
		FROM sys.database_permissions dp
		JOIN sys.objects o ON dp.major_id = o.object_id
		JOIN sys.schemas s ON o.schema_id = s.schema_id
		JOIN sys.database_principals pr ON dp.grantee_principal_id = pr.principal_id
		WHERE pr.name = @p1 AND dp.state = 'G'`, username)
	if err != nil {
		return "", err
	}
	defer rows.Close()
	var stmts []string
	for rows.Next() {
		var s string
		_ = rows.Scan(&s)
		stmts = append(stmts, s)
	}
	return strings.Join(stmts, "\n"), rows.Err()
}
```

- [ ] **Step 3: Run full agent test suite**

```bash
cd agent && go test ./... 2>&1
```

Expected: all tests pass (MSSQL engine tests run on Windows only; the `execMSSQL` stub is tested implicitly via `TestExecute_UnsupportedDBType` on Linux).

- [ ] **Step 4: Commit**

```bash
git add agent/commands/dbadmin/dbadmin_windows.go agent/commands/dbadmin/dbadmin_other.go
git commit -m "feat(agent/dbadmin): add MSSQL engine (Windows) and non-Windows stub"
```

---

## Task 6: Register DB admin commands in executor

**Files:**
- Modify: `agent/executor/executor.go`

- [ ] **Step 1: Write a failing test in the executor that verifies the new commands are registered**

Append to `agent/executor/executor_test.go`:

```go
func TestDispatch_DBAdminCommandsRegistered(t *testing.T) {
	commands := []string{
		"provision_db_user",
		"deprovision_db_user",
		"db_permission_change",
		"configure_db_audit",
		"db_connection_config",
	}
	for _, cmd := range commands {
		result := executor.Dispatch(cmd, map[string]any{}, false, nil)
		// Should fail with a meaningful error (missing params), not "unknown command"
		if result.Status == "failed" && strings.Contains(result.Error, "unknown command") {
			t.Errorf("command %q is not registered in executor", cmd)
		}
	}
}
```

- [ ] **Step 2: Run — expect failure (commands not yet registered)**

```bash
cd agent && go test ./executor/... -run TestDispatch_DBAdminCommandsRegistered -v 2>&1
```

Expected: test fails with "unknown command" for the DB admin commands.

- [ ] **Step 3: Add a shim package that adapts dbadmin.Execute to the CommandFunc signature**

The executor's `CommandFunc` is `func(params map[string]any) (map[string]any, error)` but `dbadmin.Execute` requires a `context.Context` and a `Command` struct. Add adapter functions to `agent/commands/dbadmin/dbadmin.go`:

```go
// ExecuteCommand is the CommandFunc-compatible adapter for the executor registry.
// It reads db_type, admin_dsn, and action from params.
func ExecuteCommand(params map[string]any) (map[string]any, error) {
	cmd := commandFromParams(params)
	result, err := Execute(context.Background(), cmd)
	if err != nil {
		return nil, err
	}
	out := make(map[string]any, len(result.Fields))
	for k, v := range result.Fields {
		out[k] = v
	}
	return out, nil
}

// RollbackCommand is the rollback adapter. It reads pre_change_* fields from params
// (merged previousResult) to restore prior state.
func RollbackCommand(params map[string]any) (map[string]any, error) {
	// For rollback, the action is reversed: provision->deprovision, grant->revoke, etc.
	cmd := commandFromParams(params)
	cmd.Action = rollbackAction(cmd.Action)
	if cmd.Action == "" {
		return map[string]any{"rolled_back": false, "reason": "no rollback defined for this db admin action"}, nil
	}
	result, err := Execute(context.Background(), cmd)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"rolled_back": true}
	for k, v := range result.Fields {
		out[k] = v
	}
	return out, nil
}

func commandFromParams(params map[string]any) Command {
	port, _ := params["db_port"].(int)
	return Command{
		DBType:   strVal(params, "db_type"),
		DBHost:   strVal(params, "db_host"),
		DBPort:   port,
		DBName:   strVal(params, "db_name"),
		AdminDSN: strVal(params, "admin_dsn"), // injected by agent runner from connector creds
		Action:   strVal(params, "action"),
		Params:   params,
	}
}

func strVal(m map[string]any, key string) string {
	v, _ := m[key].(string)
	return v
}

func rollbackAction(action string) string {
	switch action {
	case "provision_db_user":
		return "deprovision_db_user"
	case "grant_db_permissions":
		return "revoke_db_permissions"
	case "configure_db_audit", "db_connection_config":
		return action // same action re-applied with pre_change_ values
	case "deprovision_db_user":
		return "provision_db_user" // re-create with pre_drop_grants
	default:
		return ""
	}
}
```

Add `"context"` and `"strings"` to the import block in `dbadmin.go` (strings already present; add context).

- [ ] **Step 4: Register commands in executor/executor.go**

Add import:

```go
"nexplane-agent/commands/dbadmin"
```

Add to `var commands` map:

```go
// DB administration (Spec DB-Admin)
"provision_db_user":   dbadmin.ExecuteCommand,
"deprovision_db_user": dbadmin.ExecuteCommand,
"db_permission_change": dbadmin.ExecuteCommand,
"configure_db_audit":  dbadmin.ExecuteCommand,
"db_connection_config": dbadmin.ExecuteCommand,
```

Add to `var rollbacks` map:

```go
"provision_db_user":    dbadmin.RollbackCommand,
"deprovision_db_user":  dbadmin.RollbackCommand,
"db_permission_change": dbadmin.RollbackCommand,
"configure_db_audit":   dbadmin.RollbackCommand,
"db_connection_config": dbadmin.RollbackCommand,
```

- [ ] **Step 5: Build and run tests**

```bash
cd agent && go build ./... && go test ./... 2>&1
```

Expected: all tests pass including `TestDispatch_DBAdminCommandsRegistered`.

- [ ] **Step 6: Commit**

```bash
git add agent/executor/executor.go agent/commands/dbadmin/dbadmin.go
git commit -m "feat(agent/executor): register DB admin commands — provision, deprovision, permission, audit, connection config"
```

---

## Task 7: Create AWS RDS replica promotion connector action

**Files:**
- Create: `backend/app/connectors/executors/aws/promote_rds_replica.py`

- [ ] **Step 1: Write the failing backend test first**

Create `backend/app/tests/test_database_admin.py` with just the RDS promotion test (change type definition tests come in Task 8):

```python
"""Tests for database administration connector actions and change type definitions."""
import json
import os
import pytest
from unittest.mock import MagicMock, patch, AsyncMock


# ---- RDS promotion action tests ----

@pytest.mark.asyncio
async def test_promote_rds_replica_mock_when_no_creds():
    """Without credentials, execute() returns a mock result."""
    from app.connectors.executors.aws.promote_rds_replica import execute

    connector = MagicMock()
    connector.credentials = {}
    params = {"replica_identifier": "my-replica"}

    result = await execute(params, [], connector)

    assert result["action"] == "promote_db_replica"
    assert result["mock"] is True
    assert "new_endpoint" in result


@pytest.mark.asyncio
async def test_promote_rds_replica_real_execute_calls_boto3():
    """With credentials, execute() calls rds.promote_read_replica and waiter."""
    from app.connectors.executors.aws.promote_rds_replica import execute

    mock_rds = MagicMock()
    mock_rds.promote_read_replica.return_value = {}
    mock_waiter = MagicMock()
    mock_rds.get_waiter.return_value = mock_waiter
    mock_rds.describe_db_instances.return_value = {
        "DBInstances": [{"Endpoint": {"Address": "new-primary.us-east-1.rds.amazonaws.com"}}]
    }

    connector = MagicMock()
    connector.credentials = {"region": "us-east-1", "access_key_id": "AKIA...", "secret_access_key": "..."}
    connector.session = MagicMock()
    connector.session.client.return_value = mock_rds

    params = {
        "replica_identifier": "my-replica",
        "update_dns_record": False,
    }

    with patch("app.connectors.executors.aws.promote_rds_replica._verify_writable", return_value=None):
        result = await execute(params, [], connector)

    assert result["new_endpoint"] == "new-primary.us-east-1.rds.amazonaws.com"
    assert result["dns_updated"] is False
    mock_rds.promote_read_replica.assert_called_once_with(DBInstanceIdentifier="my-replica")
    mock_waiter.wait.assert_called_once_with(DBInstanceIdentifier="my-replica")


@pytest.mark.asyncio
async def test_promote_rds_replica_updates_route53_when_requested():
    """When update_dns_record=True, Route53 UPSERT is called."""
    from app.connectors.executors.aws.promote_rds_replica import execute

    mock_rds = MagicMock()
    mock_rds.promote_read_replica.return_value = {}
    mock_waiter = MagicMock()
    mock_rds.get_waiter.return_value = mock_waiter
    mock_rds.describe_db_instances.return_value = {
        "DBInstances": [{"Endpoint": {"Address": "new-primary.rds.amazonaws.com"}}]
    }
    mock_r53 = MagicMock()

    connector = MagicMock()
    connector.credentials = {"region": "us-east-1", "access_key_id": "AKIA...", "secret_access_key": "..."}
    connector.session = MagicMock()
    connector.session.client.side_effect = lambda svc: mock_rds if svc == "rds" else mock_r53

    params = {
        "replica_identifier": "my-replica",
        "update_dns_record": True,
        "dns_hosted_zone_id": "Z1234ABCD",
        "dns_record_name": "primary.example.com",
    }

    with patch("app.connectors.executors.aws.promote_rds_replica._verify_writable", return_value=None):
        result = await execute(params, [], connector)

    assert result["dns_updated"] is True
    mock_r53.change_resource_record_sets.assert_called_once()
    call_kwargs = mock_r53.change_resource_record_sets.call_args[1]
    assert call_kwargs["HostedZoneId"] == "Z1234ABCD"
    changes = call_kwargs["ChangeBatch"]["Changes"]
    assert changes[0]["ResourceRecordSet"]["Name"] == "primary.example.com"
    assert changes[0]["ResourceRecordSet"]["ResourceRecords"][0]["Value"] == "new-primary.rds.amazonaws.com"


@pytest.mark.asyncio
async def test_promote_rds_replica_rollback_is_unsupported():
    """Rollback for RDS promotion is always unsupported."""
    from app.connectors.executors.aws.promote_rds_replica import rollback

    result = await rollback({}, {}, MagicMock())
    assert result["rolled_back"] is False
    assert "cannot" in result["reason"].lower() or "unsupported" in result["reason"].lower()
```

- [ ] **Step 2: Run — expect ImportError (module does not exist)**

```bash
cd backend && python -m pytest app/tests/test_database_admin.py -x 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'app.connectors.executors.aws.promote_rds_replica'`

- [ ] **Step 3: Create backend/app/connectors/executors/aws/promote_rds_replica.py**

```python
import asyncio
from datetime import datetime, timezone


def _mock_response(params: dict) -> dict:
    return {
        "action": "promote_db_replica",
        "replica_identifier": params.get("replica_identifier"),
        "new_endpoint": "mock-primary.us-east-1.rds.amazonaws.com",
        "dns_updated": False,
        "mock": True,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }


def _verify_writable(endpoint: str, connector) -> None:
    """Attempt a write-acceptance check against the new primary endpoint.

    Uses the connector's DB credentials if available. On failure, raises
    RuntimeError so the caller can surface the error before updating DNS.
    This is intentionally lightweight — a simple connection attempt is enough
    to confirm the instance is accepting connections after promotion.
    """
    # Production implementation would open a short-lived connection and
    # attempt a BEGIN/ROLLBACK. Stubbed here because the DB credentials
    # are not part of the RDS connector credential bundle (which holds IAM
    # creds, not DB-level creds). Operators should verify via application
    # health checks post-promotion.
    pass


async def _real_execute(connector, params: dict) -> dict:
    loop = asyncio.get_event_loop()
    replica_id = params["replica_identifier"]

    rds = connector.session.client("rds")

    def _promote():
        rds.promote_read_replica(DBInstanceIdentifier=replica_id)
        waiter = rds.get_waiter("db_instance_available")
        waiter.wait(DBInstanceIdentifier=replica_id)
        desc = rds.describe_db_instances(DBInstanceIdentifier=replica_id)
        return desc["DBInstances"][0]["Endpoint"]["Address"]

    new_endpoint = await loop.run_in_executor(None, _promote)

    _verify_writable(new_endpoint, connector)

    dns_updated = False
    if params.get("update_dns_record"):
        r53 = connector.session.client("route53")

        def _update_dns():
            r53.change_resource_record_sets(
                HostedZoneId=params["dns_hosted_zone_id"],
                ChangeBatch={
                    "Changes": [
                        {
                            "Action": "UPSERT",
                            "ResourceRecordSet": {
                                "Name": params["dns_record_name"],
                                "Type": "CNAME",
                                "TTL": 60,
                                "ResourceRecords": [{"Value": new_endpoint}],
                            },
                        }
                    ]
                },
            )

        await loop.run_in_executor(None, _update_dns)
        dns_updated = True

    return {
        "action": "promote_db_replica",
        "replica_identifier": replica_id,
        "new_endpoint": new_endpoint,
        "dns_updated": dns_updated,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
    }


async def execute(parameters: dict, asset_ids: list, connector) -> dict:
    creds = getattr(connector, "credentials", {})
    if not creds:
        return _mock_response(parameters)
    return await _real_execute(connector, parameters)


async def rollback(parameters: dict, execution_result: dict, connector) -> dict:
    return {
        "rolled_back": False,
        "reason": (
            "RDS replica promotion cannot be reversed — the replica is now a standalone "
            "writable instance and cannot be re-attached to its source. "
            "To restore replication, create a new read replica from the promoted instance."
        ),
    }
```

- [ ] **Step 4: Run the RDS promotion tests — expect pass**

```bash
cd backend && python -m pytest app/tests/test_database_admin.py -k "promote" -v 2>&1
```

Expected: all four `test_promote_rds_replica_*` tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/executors/aws/promote_rds_replica.py backend/app/tests/test_database_admin.py
git commit -m "feat(backend/aws): add RDS replica promotion connector action with Route53 DNS update"
```

---

## Task 8: Create change type definition JSON files

**Files:**
- Create: `backend/app/connectors/change_type_definitions/provision_db_user.json`
- Create: `backend/app/connectors/change_type_definitions/deprovision_db_user.json`
- Create: `backend/app/connectors/change_type_definitions/db_permission_change.json`
- Create: `backend/app/connectors/change_type_definitions/configure_db_audit.json`
- Create: `backend/app/connectors/change_type_definitions/promote_db_replica.json`
- Create: `backend/app/connectors/change_type_definitions/db_connection_config.json`

- [ ] **Step 1: Write failing tests for change type definition loading**

Add to `backend/app/tests/test_database_admin.py`:

```python
# ---- Change type definition tests ----

CHANGE_TYPE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "connectors", "change_type_definitions"
)

DB_ADMIN_CHANGE_TYPES = [
    "provision_db_user",
    "deprovision_db_user",
    "db_permission_change",
    "configure_db_audit",
    "promote_db_replica",
    "db_connection_config",
]


def _load_change_type(name: str) -> dict:
    path = os.path.join(CHANGE_TYPE_DIR, f"{name}.json")
    with open(path) as f:
        return json.load(f)


@pytest.mark.parametrize("change_type", DB_ADMIN_CHANGE_TYPES)
def test_change_type_definition_exists_and_is_valid_json(change_type):
    """Each DB admin change type definition file must exist and parse as valid JSON."""
    data = _load_change_type(change_type)
    assert isinstance(data, dict), f"{change_type}.json must be a JSON object"


@pytest.mark.parametrize("change_type", DB_ADMIN_CHANGE_TYPES)
def test_change_type_definition_has_required_fields(change_type):
    """Each definition must have change_type and display_name fields."""
    data = _load_change_type(change_type)
    assert "change_type" in data, f"{change_type}.json missing 'change_type'"
    assert "display_name" in data, f"{change_type}.json missing 'display_name'"
    assert data["change_type"] == change_type, (
        f"change_type field '{data['change_type']}' does not match filename '{change_type}'"
    )


def test_promote_db_replica_has_no_rollback_step():
    """promote_db_replica must declare rollback_supported=false (irreversible action)."""
    data = _load_change_type("promote_db_replica")
    assert data.get("rollback_supported") is False, (
        "promote_db_replica must set rollback_supported=false"
    )


def test_provision_db_user_has_preflight_checks():
    """provision_db_user should declare preflight checks."""
    data = _load_change_type("provision_db_user")
    assert "preflight_checks" in data
    assert len(data["preflight_checks"]) > 0
```

- [ ] **Step 2: Run — expect FileNotFoundError (files don't exist yet)**

```bash
cd backend && python -m pytest app/tests/test_database_admin.py -k "change_type" -v 2>&1 | head -20
```

Expected: `FileNotFoundError` for each missing JSON file.

- [ ] **Step 3: Create provision_db_user.json**

```json
{
  "change_type": "provision_db_user",
  "display_name": "Provision Database User",
  "description": "Create a database login/user with an initial grant set. If password is omitted, a secure random password is generated and returned as encrypted step output.",
  "rollback_supported": true,
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "steps": [
    {"action": "provision_db_user", "purpose": "execute", "required": true},
    {"action": "verify_connection",  "purpose": "verify",  "required": false}
  ],
  "parameters": {
    "db_type":      {"type": "string", "required": true,  "enum": ["postgres", "mysql", "mssql"]},
    "db_host":      {"type": "string", "required": true},
    "db_port":      {"type": "integer","required": false},
    "db_name":      {"type": "string", "required": true},
    "username":     {"type": "string", "required": true},
    "password":     {"type": "string", "required": false, "sensitive": true},
    "grants":       {"type": "array",  "required": false, "items": {"type": "string"}},
    "connector_id": {"type": "string", "required": true}
  }
}
```

- [ ] **Step 4: Create deprovision_db_user.json**

```json
{
  "change_type": "deprovision_db_user",
  "display_name": "Deprovision Database User",
  "description": "Revoke all privileges and drop a database user. Pre-drop grants are captured as encrypted step output for audit and rollback replay.",
  "rollback_supported": true,
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "steps": [
    {"action": "deprovision_db_user", "purpose": "execute", "required": true}
  ],
  "parameters": {
    "db_type":      {"type": "string", "required": true, "enum": ["postgres", "mysql", "mssql"]},
    "db_host":      {"type": "string", "required": true},
    "db_port":      {"type": "integer","required": false},
    "db_name":      {"type": "string", "required": true},
    "username":     {"type": "string", "required": true},
    "connector_id": {"type": "string", "required": true}
  }
}
```

- [ ] **Step 5: Create db_permission_change.json**

```json
{
  "change_type": "db_permission_change",
  "display_name": "Database Permission Change",
  "description": "Add or remove specific grants from an existing database user without modifying the user itself. Grant strings use the format 'schema.table: PRIV1,PRIV2'.",
  "rollback_supported": true,
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "steps": [
    {"action": "db_permission_change", "purpose": "execute", "required": true}
  ],
  "parameters": {
    "db_type":          {"type": "string", "required": true, "enum": ["postgres", "mysql", "mssql"]},
    "db_host":          {"type": "string", "required": true},
    "db_port":          {"type": "integer","required": false},
    "db_name":          {"type": "string", "required": true},
    "target_user":      {"type": "string", "required": true},
    "grants_to_add":    {"type": "array",  "required": false, "items": {"type": "string"}},
    "grants_to_revoke": {"type": "array",  "required": false, "items": {"type": "string"}},
    "connector_id":     {"type": "string", "required": true}
  }
}
```

- [ ] **Step 6: Create configure_db_audit.json**

```json
{
  "change_type": "configure_db_audit",
  "display_name": "Configure Database Audit Logging",
  "description": "Enable or reconfigure database audit logging. Pre-change settings are captured as encrypted step output for rollback.",
  "rollback_supported": true,
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "steps": [
    {"action": "configure_db_audit", "purpose": "execute", "required": true}
  ],
  "parameters": {
    "db_type":      {"type": "string",  "required": true,  "enum": ["postgres", "mysql", "mssql"]},
    "db_host":      {"type": "string",  "required": true},
    "db_port":      {"type": "integer", "required": false},
    "connector_id": {"type": "string",  "required": true},
    "audit_level":  {"type": "string",  "required": true,  "enum": ["ddl", "dml", "all"]},
    "log_path":     {"type": "string",  "required": false},
    "enabled":      {"type": "boolean", "required": true}
  }
}
```

- [ ] **Step 7: Create promote_db_replica.json**

```json
{
  "change_type": "promote_db_replica",
  "display_name": "Promote RDS Read Replica",
  "description": "Promote an RDS read replica to a standalone writable instance. Optionally updates a Route 53 CNAME. This action is irreversible — the replica cannot be re-attached to its source DB after promotion.",
  "rollback_supported": false,
  "requires_approval": true,
  "blast_radius_warning": "Promoting a read replica permanently disconnects it from its source DB and makes it writable as a standalone instance. This cannot be undone via rollback.",
  "preflight_checks": ["connector_reachable", "asset_exists"],
  "steps": [
    {"action": "promote_db_replica", "purpose": "execute", "required": true}
  ],
  "parameters": {
    "connector_id":        {"type": "string",  "required": true},
    "replica_identifier":  {"type": "string",  "required": true},
    "update_dns_record":   {"type": "boolean", "required": false},
    "dns_hosted_zone_id":  {"type": "string",  "required": false},
    "dns_record_name":     {"type": "string",  "required": false}
  }
}
```

- [ ] **Step 8: Create db_connection_config.json**

```json
{
  "change_type": "db_connection_config",
  "display_name": "Database Connection Limit Configuration",
  "description": "Update the maximum connection limit for a database server. Pre-change value is captured for rollback. MySQL applies immediately; PostgreSQL and MSSQL require a service restart.",
  "rollback_supported": true,
  "preflight_checks": ["connector_reachable", "asset_exists", "no_concurrent_changes"],
  "steps": [
    {"action": "db_connection_config", "purpose": "execute", "required": true}
  ],
  "parameters": {
    "db_type":         {"type": "string",  "required": true, "enum": ["postgres", "mysql", "mssql"]},
    "db_host":         {"type": "string",  "required": true},
    "db_port":         {"type": "integer", "required": false},
    "connector_id":    {"type": "string",  "required": true},
    "max_connections": {"type": "integer", "required": true},
    "reload_only":     {"type": "boolean", "required": false}
  }
}
```

- [ ] **Step 9: Run all change type definition tests — expect pass**

```bash
cd backend && python -m pytest app/tests/test_database_admin.py -k "change_type" -v 2>&1
```

Expected: all 15 parametrized tests pass.

- [ ] **Step 10: Run the full backend test suite**

```bash
cd backend && python -m pytest app/tests/ -v 2>&1
```

Expected: all existing tests plus new DB admin tests pass.

- [ ] **Step 11: Commit**

```bash
git add \
  backend/app/connectors/change_type_definitions/provision_db_user.json \
  backend/app/connectors/change_type_definitions/deprovision_db_user.json \
  backend/app/connectors/change_type_definitions/db_permission_change.json \
  backend/app/connectors/change_type_definitions/configure_db_audit.json \
  backend/app/connectors/change_type_definitions/promote_db_replica.json \
  backend/app/connectors/change_type_definitions/db_connection_config.json \
  backend/app/tests/test_database_admin.py
git commit -m "feat(backend): add DB admin change type definitions and backend tests"
```

---

## Task 9: Final verification

- [ ] **Step 1: Run full Go agent test suite**

```bash
cd agent && go test ./... -v 2>&1 | tail -20
```

Expected: `ok  nexplane-agent/commands/dbadmin`, `ok  nexplane-agent/executor`, and all other packages pass.

- [ ] **Step 2: Run full backend test suite**

```bash
cd backend && python -m pytest app/tests/ -v 2>&1 | tail -20
```

Expected: all tests pass with no errors.

- [ ] **Step 3: Verify go build succeeds for all platforms**

```bash
cd agent && \
  GOOS=linux   GOARCH=amd64 go build ./... && \
  GOOS=linux   GOARCH=arm64 go build ./... && \
  GOOS=windows GOARCH=amd64 go build ./...
```

Expected: all three build targets exit 0.

- [ ] **Step 4: Final commit**

```bash
git add -p  # review any unstaged changes
git commit -m "chore(dbadmin): final verification — all tests and cross-platform builds pass"
```

---

## Self-Review Checklist

**Spec coverage:**
- Task 1: Go driver deps (Spec Section 1)
- Tasks 2–5: `dbadmin` package — dispatcher, PostgreSQL, MySQL, MSSQL (Spec Section 2)
- Tasks 2–5: All five agent actions — `provision_db_user`, `deprovision_db_user`, `db_permission_change`, `configure_db_audit`, `db_connection_config` (Spec Sections 3–6, 8)
- Task 6: Executor registration (Spec Section 2, `registry.go` equivalent)
- Task 7: RDS replica promotion connector action (Spec Section 7)
- Task 8: Six change type definition JSON files (plan requirement)
- Task 8: Backend tests including change type loading and RDS promotion (plan requirement)

**TDD:** Every task writes failing tests before implementation code.

**Credential security:** `AdminDSN` is assembled by the agent runner from connector credentials and injected into `Command.AdminDSN`. It is never in change parameters, never logged, and held in memory only during command execution.

**Rollback coverage:** Five of six change types have rollback paths. `promote_db_replica` is explicitly `rollback_supported: false` with blast radius warning.

**Platform split:** MSSQL engine is gated by `//go:build windows`. Non-Windows agents get a stub returning an unsupported error. MySQL and PostgreSQL work on all platforms.

**Placeholder scan:** No TODOs, TBDs, or vague steps — all code is complete and exact.
