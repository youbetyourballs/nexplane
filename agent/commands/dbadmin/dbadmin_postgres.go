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

func configureAuditPG(ctx context.Context, db *sql.DB, cmd Command) (*Result, error) {
	auditLevel, _ := cmd.Params["audit_level"].(string)
	enabled, _ := cmd.Params["enabled"].(bool)

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
		"restart_required":           "true",
	}}
	if reloadOnly {
		result.Fields["warning"] = "max_connections requires a full PostgreSQL restart; config written but not yet in effect"
	}
	return result, nil
}

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
