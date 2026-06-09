//go:build darwin

package credrotation

import (
	"context"
	"fmt"
	"os/exec"
)

var execCommandDB = exec.Command

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
	cmd := buildDBCmdDarwin(p, stmt)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("update db user password: %w — %s", err, out)
	}
	return nil
}

func buildDBCmdDarwin(p DBRotateParams, stmt string) *exec.Cmd {
	switch p.DBEngine {
	case "postgres":
		return execCommandDB("psql",
			fmt.Sprintf("host=%s port=%d user=%s sslmode=require", p.DBHost, p.DBPort, p.DBUsername),
			"-c", stmt)
	case "mysql":
		return execCommandDB("mysql",
			fmt.Sprintf("-h%s", p.DBHost),
			fmt.Sprintf("-P%d", p.DBPort),
			fmt.Sprintf("-u%s", p.DBUsername),
			"-e", stmt)
	default:
		return execCommandDB("false")
	}
}

func restartService(ctx context.Context, serviceName string) error {
	plists := []string{
		"/Library/LaunchDaemons/" + serviceName + ".plist",
		"/System/Library/LaunchDaemons/" + serviceName + ".plist",
		"/Library/LaunchDaemons/homebrew.mxcl." + serviceName + ".plist",
	}
	for _, plist := range plists {
		if _, err := execCommandDB("launchctl", "unload", plist).CombinedOutput(); err == nil {
			if out, err := execCommandDB("launchctl", "load", "-w", plist).CombinedOutput(); err != nil {
				return fmt.Errorf("launchctl load %s: %w — %s", plist, err, out)
			}
			return nil
		}
	}
	cmd := execCommandDB("brew", "services", "restart", serviceName)
	if out, err := cmd.CombinedOutput(); err != nil {
		return fmt.Errorf("restart %s: %w — %s", serviceName, err, out)
	}
	return nil
}
