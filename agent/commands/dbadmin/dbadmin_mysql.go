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
		"restart_performed":          "false",
	}}, nil
}
