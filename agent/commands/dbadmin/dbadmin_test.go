package dbadmin_test

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

// ---- PostgreSQL tests ----

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

// ---- MySQL tests ----

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
