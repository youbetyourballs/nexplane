package backup

import (
	"context"
	"fmt"
	"strings"
)

// BackupParams mirrors the create_backup change request parameters.
type BackupParams struct {
	BackupName     string   `json:"backup_name"`
	RetentionDays  int      `json:"retention_days"`
	Paths          []string `json:"paths"`
	ResticRepo     string   `json:"restic_repo"`
	ResticPassword string   `json:"restic_password"`
}

// RestoreParams mirrors the restore_files change request parameters.
type RestoreParams struct {
	BackupSnapshotID string   `json:"backup_snapshot_id"`
	RestorePaths     []string `json:"restore_paths"`
	DestinationPath  string   `json:"destination_path"`
	ResticRepo       string   `json:"restic_repo"`
	ResticPassword   string   `json:"restic_password"`
}

// Execute is the entry point called by executor.Dispatch for "create_backup".
func Execute(params map[string]any) (map[string]any, error) {
	p, err := parseBackupParams(params)
	if err != nil {
		return nil, err
	}
	return executeOS(context.Background(), p)
}

// Rollback for create_backup is a deliberate no-op: snapshots are retained
// until the retention policy expires. Use restore_files to recover data.
func Rollback(params map[string]any) (map[string]any, error) {
	return map[string]any{
		"rolled_back": false,
		"reason":      "backup snapshots are immutable — use restore_files to recover data",
	}, nil
}

// RestoreExecute is the entry point for "restore_files".
func RestoreExecute(params map[string]any) (map[string]any, error) {
	p, err := parseRestoreParams(params)
	if err != nil {
		return nil, err
	}
	return restoreOS(context.Background(), p)
}

// RestoreRollback is a no-op — restored files are already on disk.
func RestoreRollback(params map[string]any) (map[string]any, error) {
	return map[string]any{
		"rolled_back": false,
		"reason":      "file restore has no automatic rollback — remove restored files manually if needed",
	}, nil
}

func parseBackupParams(params map[string]any) (BackupParams, error) {
	repo, _ := params["restic_repo"].(string)
	if repo == "" {
		return BackupParams{}, fmt.Errorf("restic_repo is required")
	}
	rawPaths, ok := params["paths"].([]any)
	if !ok || len(rawPaths) == 0 {
		return BackupParams{}, fmt.Errorf("paths is required and must be non-empty")
	}
	var paths []string
	for _, p := range rawPaths {
		if s, ok := p.(string); ok && s != "" {
			paths = append(paths, s)
		}
	}
	if len(paths) == 0 {
		return BackupParams{}, fmt.Errorf("paths must contain at least one non-empty string")
	}
	name, _ := params["backup_name"].(string)
	if name == "" {
		name = "nexplane-backup"
	}
	retention := 30
	if r, ok := params["retention_days"].(float64); ok {
		retention = int(r)
	}
	password, _ := params["restic_password"].(string)
	return BackupParams{
		BackupName:     name,
		RetentionDays:  retention,
		Paths:          paths,
		ResticRepo:     repo,
		ResticPassword: password,
	}, nil
}

func parseRestoreParams(params map[string]any) (RestoreParams, error) {
	snap, _ := params["backup_snapshot_id"].(string)
	if snap == "" {
		return RestoreParams{}, fmt.Errorf("backup_snapshot_id is required")
	}
	dest, _ := params["destination_path"].(string)
	if dest == "" {
		return RestoreParams{}, fmt.Errorf("destination_path is required")
	}
	repo, _ := params["restic_repo"].(string)
	if repo == "" {
		return RestoreParams{}, fmt.Errorf("restic_repo is required")
	}
	rawPaths, _ := params["restore_paths"].([]any)
	var paths []string
	for _, p := range rawPaths {
		if s, ok := p.(string); ok && s != "" {
			paths = append(paths, s)
		}
	}
	password, _ := params["restic_password"].(string)
	return RestoreParams{
		BackupSnapshotID: snap,
		RestorePaths:     paths,
		DestinationPath:  dest,
		ResticRepo:       repo,
		ResticPassword:   password,
	}, nil
}

// VerifyChecksums compares restored-file checksums against expected values.
// Returns a list of mismatch descriptions; empty slice means all OK.
// Exported so tests can call it directly.
func VerifyChecksums(restored, expected map[string]string) []string {
	var mismatches []string
	for path, expHash := range expected {
		got, ok := restored[path]
		if !ok {
			mismatches = append(mismatches, fmt.Sprintf("%s: not restored", path))
		} else if !strings.EqualFold(got, expHash) {
			mismatches = append(mismatches, fmt.Sprintf("%s: expected %s got %s", path, expHash, got))
		}
	}
	return mismatches
}
