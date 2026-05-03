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
		"restart_performed":          "true",
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
