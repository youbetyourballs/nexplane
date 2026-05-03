//go:build windows

package credrotation

import (
	"context"
	"fmt"
	"os/exec"
)

func updateDBUserPassword(ctx context.Context, p DBRotateParams) error {
	return fmt.Errorf("rotate_db_credentials: update_db_user not supported on Windows — run against the database host directly")
}

func buildDBCmd(ctx context.Context, p DBRotateParams, stmt string) *exec.Cmd {
	// Unreachable on Windows — updateDBUserPassword returns an error first
	return exec.CommandContext(ctx, "cmd", "/c", "echo", "unsupported")
}

func restartService(ctx context.Context, serviceName string) error {
	cmd := exec.CommandContext(ctx, "net", "stop", serviceName)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("net stop %s: %w — %s", serviceName, err, out)
	}
	cmd = exec.CommandContext(ctx, "net", "start", serviceName)
	out, err = cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("net start %s: %w — %s", serviceName, err, out)
	}
	return nil
}
