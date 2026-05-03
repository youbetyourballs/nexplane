//go:build linux

package credrotation

import (
	"context"
	"fmt"
	"os/exec"
)

func updateDBUserPassword(ctx context.Context, p DBRotateParams) error {
	var stmt string
	switch p.DBEngine {
	case "postgres":
		stmt = fmt.Sprintf("ALTER USER %s WITH PASSWORD '%s';", p.DBUsername, p.NewPassword)
	case "mysql":
		stmt = fmt.Sprintf("ALTER USER '%s'@'%%' IDENTIFIED BY '%s';", p.DBUsername, p.NewPassword)
	default:
		return fmt.Errorf("unsupported db engine: %s", p.DBEngine)
	}
	cmd := buildDBCmd(ctx, p, stmt)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("update db user password: %w — %s", err, out)
	}
	return nil
}

func buildDBCmd(ctx context.Context, p DBRotateParams, stmt string) *exec.Cmd {
	switch p.DBEngine {
	case "postgres":
		return exec.CommandContext(ctx,
			"psql",
			fmt.Sprintf("host=%s port=%d user=%s sslmode=require", p.DBHost, p.DBPort, p.DBUsername),
			"-c", stmt,
		)
	case "mysql":
		return exec.CommandContext(ctx,
			"mysql",
			fmt.Sprintf("-h%s", p.DBHost),
			fmt.Sprintf("-P%d", p.DBPort),
			fmt.Sprintf("-u%s", p.DBUsername),
			"-e", stmt,
		)
	default:
		// Unreachable — guarded in updateDBUserPassword
		cmd := exec.CommandContext(ctx, "false")
		return cmd
	}
}

func restartService(ctx context.Context, serviceName string) error {
	cmd := exec.CommandContext(ctx, "systemctl", "restart", serviceName)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("restart %s: %w — %s", serviceName, err, out)
	}
	return nil
}
