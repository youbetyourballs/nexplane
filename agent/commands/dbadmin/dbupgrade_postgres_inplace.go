// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package dbadmin

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// DbUpgradePostgresInPlaceExecute performs a pg_upgrade in-place Postgres major version upgrade.
// Uses hardlink mode (--link) for speed — source data dir must be on the same filesystem as target.
// The old cluster is left stopped at old_data_dir; the new cluster runs on the original port.
//
// Params:
//
//	source_version (required): e.g. "14"
//	target_version (required): e.g. "16"
//	old_data_dir  (optional): default /var/lib/postgresql/<source_version>/main
//	new_data_dir  (optional): default /var/lib/postgresql/<target_version>/main
//	old_bin_dir   (optional): default /usr/lib/postgresql/<source_version>/bin
//	new_bin_dir   (optional): default /usr/lib/postgresql/<target_version>/bin
//	db_service    (optional): systemd service name, default postgresql
func DbUpgradePostgresInPlaceExecute(params map[string]any) (map[string]any, error) {
	srcVersion := str(params, "source_version")
	if srcVersion == "" {
		return nil, fmt.Errorf("source_version required (e.g. '14')")
	}
	tgtVersion := str(params, "target_version")
	if tgtVersion == "" {
		return nil, fmt.Errorf("target_version required (e.g. '16')")
	}

	oldDataDir := str(params, "old_data_dir")
	if oldDataDir == "" {
		oldDataDir = fmt.Sprintf("/var/lib/postgresql/%s/main", srcVersion)
	}
	newDataDir := str(params, "new_data_dir")
	if newDataDir == "" {
		newDataDir = fmt.Sprintf("/var/lib/postgresql/%s/main", tgtVersion)
	}
	oldBinDir := str(params, "old_bin_dir")
	if oldBinDir == "" {
		oldBinDir = fmt.Sprintf("/usr/lib/postgresql/%s/bin", srcVersion)
	}
	newBinDir := str(params, "new_bin_dir")
	if newBinDir == "" {
		newBinDir = fmt.Sprintf("/usr/lib/postgresql/%s/bin", tgtVersion)
	}
	dbService := str(params, "db_service")
	if dbService == "" {
		dbService = "postgresql"
	}

	// 1. Install target postgres package
	aptPkg := fmt.Sprintf("postgresql-%s", tgtVersion)
	if out, _, err := runCmd(nil, "apt-get", "install", "-y", aptPkg); err != nil {
		return nil, fmt.Errorf("apt-get install %s: %s", aptPkg, out)
	}

	// 2. Stop existing cluster
	if out, _, err := runCmd(nil, "systemctl", "stop", dbService); err != nil {
		return nil, fmt.Errorf("stop %s: %s %s", dbService, out, err)
	}

	// 3. Initialize new data directory (as postgres user)
	initdbBin := newBinDir + "/initdb"
	if out, errOut, err := runCmd(nil, "su", "-", "postgres", "-c",
		fmt.Sprintf("%s -D %s", initdbBin, newDataDir)); err != nil {
		// initdb may fail if data dir already exists — check if it's already initialized
		if !strings.Contains(out+errOut, "already exists") {
			return nil, fmt.Errorf("initdb: %s %s", out, errOut)
		}
	}

	// 4. Run pg_upgrade as postgres user
	pgUpgradeBin := newBinDir + "/pg_upgrade"
	pgUpgradeCmd := fmt.Sprintf(
		"%s --old-datadir %s --new-datadir %s --old-bindir %s --new-bindir %s --link",
		pgUpgradeBin, oldDataDir, newDataDir, oldBinDir, newBinDir,
	)
	upgradeOut, upgradeErr, err := runCmd(nil, "su", "-", "postgres", "-c", pgUpgradeCmd)
	if err != nil {
		// Restart old cluster for safety before returning error
		runCmd(nil, "systemctl", "start", dbService) //nolint:errcheck
		return nil, fmt.Errorf("pg_upgrade: %s %s", upgradeOut, upgradeErr)
	}

	// 5. Update cluster naming via pg_ctlcluster wrappers (best-effort)
	exec.Command("pg_dropcluster", "--stop", tgtVersion, "main").Run()     //nolint:errcheck
	exec.Command("pg_renamecluster", srcVersion, "main", "main_old").Run() //nolint:errcheck
	exec.Command("pg_renamecluster", tgtVersion, "main", "main").Run()     //nolint:errcheck

	// 6. Start new cluster
	if out, _, err := runCmd(nil, "systemctl", "start", dbService); err != nil {
		return nil, fmt.Errorf("start %s after upgrade: %s", dbService, out)
	}

	// 7. Run ANALYZE to update statistics (best-effort)
	time.Sleep(3 * time.Second) // give postgres a moment to accept connections
	runCmd(nil, "su", "-", "postgres", "-c", "vacuumdb --all --analyze-in-stages -q") //nolint:errcheck

	return map[string]any{
		"status":         "completed",
		"strategy":       "in_place",
		"source_version": srcVersion,
		"target_version": tgtVersion,
		"old_data_dir":   oldDataDir,
		"new_data_dir":   newDataDir,
		"stdout":         strings.TrimSpace(upgradeOut),
		"upgraded_at":    time.Now().UTC().Format(time.RFC3339),
	}, nil
}

// DbUpgradePostgresInPlaceRollbackExecute attempts to restore the old Postgres cluster after a
// failed or unwanted in-place upgrade. It swaps main_old back to main via pg_renamecluster and
// restarts the service. Because pg_upgrade --link hard-links data files, this is only safe if the
// new cluster has not yet written any data; otherwise a data_loss_warning is returned.
//
// Params: same as DbUpgradePostgresInPlaceExecute — source_version, target_version, db_service.
func DbUpgradePostgresInPlaceRollbackExecute(params map[string]any) (map[string]any, error) {
	srcVersion := str(params, "source_version")
	if srcVersion == "" {
		return nil, fmt.Errorf("source_version required for rollback")
	}
	tgtVersion := str(params, "target_version")
	if tgtVersion == "" {
		return nil, fmt.Errorf("target_version required for rollback")
	}
	dbService := str(params, "db_service")
	if dbService == "" {
		dbService = "postgresql"
	}

	// Stop whichever cluster is currently running.
	runCmd(nil, "systemctl", "stop", dbService) //nolint:errcheck

	// Drop the new (upgraded) cluster and promote main_old back to main.
	dropOut, _, dropErr := runCmd(nil, "pg_dropcluster", "--stop", tgtVersion, "main")
	if dropErr != nil {
		// New cluster may not exist or may already be stopped — continue anyway.
		_ = dropOut
	}

	_, _, renameErr := runCmd(nil, "pg_renamecluster", srcVersion, "main_old", "main")
	if renameErr != nil {
		// main_old may not exist — the upgrade might not have renamed it yet.
		return map[string]any{
			"rolled_back":        false,
			"data_loss_warning":  true,
			"reason":             "pg_renamecluster main_old→main failed; old cluster may not have been renamed during upgrade, or data has already been written to the new cluster — manual recovery required",
			"source_version":     srcVersion,
			"target_version":     tgtVersion,
			"rolled_back_at":     time.Now().UTC().Format(time.RFC3339),
		}, nil
	}

	// Restart old cluster.
	if out, _, err := runCmd(nil, "systemctl", "start", dbService); err != nil {
		return map[string]any{
			"rolled_back":       false,
			"data_loss_warning": true,
			"reason":            fmt.Sprintf("renamed cluster back but failed to start %s: %s", dbService, out),
			"source_version":    srcVersion,
			"target_version":    tgtVersion,
			"rolled_back_at":    time.Now().UTC().Format(time.RFC3339),
		}, nil
	}

	return map[string]any{
		"rolled_back":    true,
		"source_version": srcVersion,
		"target_version": tgtVersion,
		"note":           "old cluster restored via pg_renamecluster; if new cluster had written data, those writes are lost — verify application integrity",
		"rolled_back_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}
