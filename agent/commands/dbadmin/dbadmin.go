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

// RollbackCommand is the rollback adapter.
func RollbackCommand(params map[string]any) (map[string]any, error) {
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
		AdminDSN: strVal(params, "admin_dsn"),
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
		return action
	case "deprovision_db_user":
		return "provision_db_user"
	default:
		return ""
	}
}
