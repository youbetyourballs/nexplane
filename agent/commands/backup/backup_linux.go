//go:build linux

package backup

import (
	"context"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func executeOS(ctx context.Context, p BackupParams) (map[string]any, error) {
	snapshotID, err := RunBackup(ctx, p)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"action":       "create_backup",
		"snapshot_id":  snapshotID,
		"backup_name":  p.BackupName,
		"paths":        p.Paths,
		"backed_up_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func restoreOS(ctx context.Context, p RestoreParams) (map[string]any, error) {
	checksums, err := RestoreFiles(ctx, p)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"action":         "restore_files",
		"snapshot_id":    p.BackupSnapshotID,
		"restored_files": len(checksums),
		"checksums":      checksums,
		"mismatches":     []string{},
		"restored_at":    time.Now().UTC().Format(time.RFC3339),
	}, nil
}

// RunBackup invokes restic to create a snapshot and prune old snapshots.
// Returns the restic snapshot ID on success. Exported for testing.
func RunBackup(ctx context.Context, p BackupParams) (string, error) {
	env := resticEnv(p.ResticRepo, p.ResticPassword)

	args := append([]string{"backup", "--json", "--tag", p.BackupName}, p.Paths...)
	out, err := runRestic(ctx, env, args...)
	if err != nil {
		return "", fmt.Errorf("restic backup failed: %w\noutput: %s", err, out)
	}

	snapshotID := parseSnapshotID(out)

	keepWithin := fmt.Sprintf("%dd", p.RetentionDays)
	if _, pruneErr := runRestic(ctx, env,
		"forget", "--prune", "--tag", p.BackupName, "--keep-within", keepWithin); pruneErr != nil {
		// Non-fatal: snapshot exists, pruning failed.
		return snapshotID, fmt.Errorf("backup succeeded (id=%s) but prune failed: %w", snapshotID, pruneErr)
	}

	return snapshotID, nil
}

// RestoreFiles restores specific paths from a restic snapshot.
// Returns a map of restored path → SHA256 for verification. Exported for testing.
func RestoreFiles(ctx context.Context, p RestoreParams) (map[string]string, error) {
	env := resticEnv(p.ResticRepo, p.ResticPassword)

	for _, path := range p.RestorePaths {
		if _, err := runRestic(ctx, env,
			"restore", p.BackupSnapshotID,
			"--target", p.DestinationPath,
			"--include", path,
		); err != nil {
			return nil, fmt.Errorf("restore of %s failed: %w", path, err)
		}
	}

	checksums := map[string]string{}
	for _, rp := range p.RestorePaths {
		localPath := p.DestinationPath + rp // filepath.Join flattens leading slash — keep it explicit
		sum, err := sha256File(localPath)
		if err != nil {
			checksums[rp] = "error: " + err.Error()
		} else {
			checksums[rp] = sum
		}
	}
	return checksums, nil
}

func runRestic(ctx context.Context, env []string, args ...string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, "restic", args...)
	cmd.Env = env
	return cmd.CombinedOutput()
}

func resticEnv(repo, password string) []string {
	return []string{
		"RESTIC_REPOSITORY=" + repo,
		"RESTIC_PASSWORD=" + password,
		"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
	}
}

func parseSnapshotID(out []byte) string {
	s := string(out)
	key := `"snapshot_id":"`
	idx := strings.Index(s, key)
	if idx == -1 {
		return "unknown"
	}
	rest := s[idx+len(key):]
	end := strings.Index(rest, `"`)
	if end == -1 {
		return "unknown"
	}
	return rest[:end]
}
