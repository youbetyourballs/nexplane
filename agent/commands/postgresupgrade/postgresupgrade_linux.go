//go:build linux

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package postgresupgrade

import (
	"fmt"
	"os/exec"
	"strings"
)

func str(params map[string]any, key string) string {
	v, _ := params[key].(string)
	return v
}

func runCmd(name string, args ...string) (string, error) {
	out, err := exec.Command(name, args...).CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

func runCmdDir(dir, name string, args ...string) (string, error) {
	cmd := exec.Command(name, args...)
	cmd.Dir = dir
	out, err := cmd.CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

func pgVersion() (string, error) {
	// Prefer pg_lsclusters: returns the OLDEST running cluster (the one to upgrade from).
	// On systems with multiple PG versions installed, psql --version returns the latest,
	// which is wrong when the running cluster is an older version.
	out, err := exec.Command("pg_lsclusters", "--no-header").Output()
	if err == nil {
		lines := strings.Split(strings.TrimSpace(string(out)), "\n")
		for _, line := range lines {
			fields := strings.Fields(line)
			if len(fields) >= 3 && fields[2] == "online" {
				return fields[0], nil
			}
		}
		// Fall back to first line if no online cluster
		if len(lines) > 0 && lines[0] != "" {
			fields := strings.Fields(lines[0])
			if len(fields) > 0 {
				return fields[0], nil
			}
		}
	}
	out, err = exec.Command("psql", "--version").Output()
	if err == nil {
		parts := strings.Fields(strings.TrimSpace(string(out)))
		if len(parts) >= 3 {
			return parts[2], nil
		}
	}
	return "unknown", nil
}

func preflightPG(params map[string]any) (map[string]any, error) {
	targetVersion := str(params, "target_version")
	if targetVersion == "" {
		return nil, fmt.Errorf("target_version is required")
	}
	currentVersion, _ := pgVersion()

	pgUpgradePath, err := exec.LookPath("pg_upgrade")
	if err != nil {
		// pg_upgrade lives under /usr/lib/postgresql/{version}/bin/ on Debian/Ubuntu.
		// Check the common system location directly.
		for _, v := range []string{targetVersion, currentVersion} {
			candidate := fmt.Sprintf("/usr/lib/postgresql/%s/bin/pg_upgrade", v)
			if _, statErr := exec.Command("test", "-f", candidate).CombinedOutput(); statErr == nil {
				pgUpgradePath = candidate
				break
			}
		}
	}

	oldDataDir := str(params, "old_data_dir")
	if oldDataDir == "" {
		oldDataDir = fmt.Sprintf("/var/lib/postgresql/%s/main", currentVersion)
	}
	_, statErr := exec.Command("test", "-d", oldDataDir).CombinedOutput()
	dataDirExists := statErr == nil

	return map[string]any{
		"status":          "preflight_ok",
		"current_version": currentVersion,
		"target_version":  targetVersion,
		"pg_upgrade_path": pgUpgradePath,
		"old_data_dir":    oldDataDir,
		"data_dir_exists": dataDirExists,
	}, nil
}

func executePG(params map[string]any) (map[string]any, error) {
	targetVersion := str(params, "target_version")
	if targetVersion == "" {
		return nil, fmt.Errorf("target_version is required")
	}
	currentVersion, _ := pgVersion()
	oldDataDir := str(params, "old_data_dir")
	newDataDir := str(params, "new_data_dir")

	if oldDataDir == "" {
		oldDataDir = fmt.Sprintf("/var/lib/postgresql/%s/main", currentVersion)
	}
	if newDataDir == "" {
		newDataDir = fmt.Sprintf("/var/lib/postgresql/%s/main", targetVersion)
	}
	oldBinDir := fmt.Sprintf("/usr/lib/postgresql/%s/bin", currentVersion)
	newBinDir := fmt.Sprintf("/usr/lib/postgresql/%s/bin", targetVersion)

	_, err := runCmd("apt-get", "install", "-y",
		fmt.Sprintf("postgresql-%s", targetVersion))
	if err != nil {
		_, err2 := runCmd("dnf", "install", "-y",
			fmt.Sprintf("postgresql%s-server", targetVersion))
		if err2 != nil {
			return nil, fmt.Errorf("failed to install postgresql-%s: apt error: %v; dnf error: %v", targetVersion, err, err2)
		}
		oldBinDir = fmt.Sprintf("/usr/pgsql-%s/bin", currentVersion)
		newBinDir = fmt.Sprintf("/usr/pgsql-%s/bin", targetVersion)
		oldDataDir = fmt.Sprintf("/var/lib/pgsql/%s/data", currentVersion)
		newDataDir = fmt.Sprintf("/var/lib/pgsql/%s/data", targetVersion)
		runCmd(fmt.Sprintf("/usr/pgsql-%s/bin/postgresql-%s-setup", targetVersion, targetVersion), "initdb")
	}

	runCmd("systemctl", "stop", fmt.Sprintf("postgresql@%s-main", currentVersion))
	runCmd("systemctl", "stop", "postgresql")

	// pg_upgrade is not in the postgres user's PATH; use the full path from newBinDir.
	// Run from /tmp so the postgres user has write access (needed for pg_upgrade log files).
	// On Debian/Ubuntu, postgresql.conf lives in /etc/postgresql/{version}/main/, not in the
	// data directory. Pass -o/-O so pg_upgrade's internal pg_ctl can find the config files.
	pgUpgradeBin := newBinDir + "/pg_upgrade"
	oldConf := fmt.Sprintf("/etc/postgresql/%s/main/postgresql.conf", currentVersion)
	newConf := fmt.Sprintf("/etc/postgresql/%s/main/postgresql.conf", targetVersion)
	pgUpgradeArgs := []string{"-u", "postgres", pgUpgradeBin,
		"-b", oldBinDir,
		"-B", newBinDir,
		"-d", oldDataDir,
		"-D", newDataDir,
	}
	// Only pass config file options if the Debian-style config directory exists.
	if _, err2 := exec.Command("test", "-f", oldConf).CombinedOutput(); err2 == nil {
		pgUpgradeArgs = append(pgUpgradeArgs,
			"-o", fmt.Sprintf("-c config_file=%s", oldConf),
			"-O", fmt.Sprintf("-c config_file=%s", newConf),
		)
	}
	out, err := runCmdDir("/tmp", "sudo", append([]string{}, pgUpgradeArgs...)...)
	if err != nil {
		return nil, fmt.Errorf("pg_upgrade failed: %v\nOutput: %s", err, out)
	}

	runCmd("systemctl", "start", fmt.Sprintf("postgresql@%s-main", targetVersion))
	runCmd("systemctl", "start", "postgresql")

	newVersion, _ := pgVersion()
	return map[string]any{
		"status":            "completed",
		"previous_version":  currentVersion,
		"new_version":       newVersion,
		"old_data_dir":      oldDataDir,
		"new_data_dir":      newDataDir,
		"pg_upgrade_output": out,
	}, nil
}

func verifyPG(params map[string]any) (map[string]any, error) {
	expectedVersion := str(params, "expected_version")
	currentVersion, err := pgVersion()
	if err != nil {
		return nil, fmt.Errorf("could not determine postgres version: %v", err)
	}
	_, connErr := runCmd("sudo", "-u", "postgres", "psql", "-c", "SELECT version();")

	result := map[string]any{
		"status":          "verified",
		"current_version": currentVersion,
		"can_connect":     connErr == nil,
	}
	if expectedVersion != "" && !strings.HasPrefix(currentVersion, expectedVersion) {
		result["status"] = "version_mismatch"
		result["expected_version"] = expectedVersion
	}
	return result, nil
}

func rollbackPG(params map[string]any) (map[string]any, error) {
	previousVersion := str(params, "previous_version")
	if previousVersion == "" {
		return map[string]any{
			"rolled_back": false,
			"reason":      "previous_version not provided — cannot determine old postgres version",
		}, nil
	}

	out, err := runCmd("sudo", "-u", "postgres", "bash", "./rollback.sh")
	if err != nil {
		out, err = runCmd("sudo", "-u", "postgres", "bash",
			fmt.Sprintf("/var/lib/postgresql/rollback.sh"))
		if err != nil {
			return map[string]any{
				"rolled_back": false,
				"reason":      fmt.Sprintf("rollback.sh not found or failed: %v, output: %s", err, out),
			}, nil
		}
	}

	return map[string]any{
		"rolled_back":      true,
		"previous_version": previousVersion,
		"rollback_output":  out,
	}, nil
}
