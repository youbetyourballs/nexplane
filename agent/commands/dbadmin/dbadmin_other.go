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
