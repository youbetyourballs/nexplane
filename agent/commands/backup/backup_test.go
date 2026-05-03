package backup_test

import (
	"context"
	"strings"
	"testing"

	"nexplane-agent/commands/backup"
)

// ---------------------------------------------------------------------------
// RunBackup — unit tests using fakes (restic not installed in CI)
// ---------------------------------------------------------------------------

func TestBackupParamsValidation_MissingRepo(t *testing.T) {
	_, err := backup.Execute(map[string]any{
		"backup_name":    "nightly",
		"retention_days": 30,
		"paths":          []any{"/etc"},
		// restic_repo intentionally omitted
	})
	if err == nil {
		t.Fatal("expected error for missing restic_repo")
	}
}

func TestBackupParamsValidation_MissingPaths(t *testing.T) {
	_, err := backup.Execute(map[string]any{
		"backup_name":     "nightly",
		"retention_days":  30,
		"restic_repo":     "s3:s3.amazonaws.com/mybucket/restic",
		"restic_password": "secret",
		// paths intentionally omitted
	})
	if err == nil {
		t.Fatal("expected error for missing paths")
	}
}

// ---------------------------------------------------------------------------
// RestoreFiles — unit tests
// ---------------------------------------------------------------------------

func TestRestoreParamsValidation_MissingSnapshotID(t *testing.T) {
	_, err := backup.RestoreExecute(map[string]any{
		"restore_paths":    []any{"/etc/nginx/nginx.conf"},
		"destination_path": "/tmp/restore",
		"restic_repo":      "s3:s3.amazonaws.com/mybucket/restic",
		"restic_password":  "secret",
	})
	if err == nil {
		t.Fatal("expected error for missing backup_snapshot_id")
	}
}

func TestRestoreParamsValidation_MissingDestination(t *testing.T) {
	_, err := backup.RestoreExecute(map[string]any{
		"backup_snapshot_id": "abc123",
		"restore_paths":      []any{"/etc/nginx/nginx.conf"},
		"restic_repo":        "s3:s3.amazonaws.com/mybucket/restic",
		"restic_password":    "secret",
	})
	if err == nil {
		t.Fatal("expected error for missing destination_path")
	}
}

// ---------------------------------------------------------------------------
// VerifyChecksums
// ---------------------------------------------------------------------------

func TestVerifyChecksums_AllMatch(t *testing.T) {
	restored := map[string]string{"/etc/nginx/nginx.conf": "aabb"}
	expected := map[string]string{"/etc/nginx/nginx.conf": "aabb"}
	mismatches := backup.VerifyChecksums(restored, expected)
	if len(mismatches) != 0 {
		t.Fatalf("expected no mismatches, got %v", mismatches)
	}
}

func TestVerifyChecksums_Mismatch(t *testing.T) {
	restored := map[string]string{"/etc/app.conf": "1234"}
	expected := map[string]string{"/etc/app.conf": "9999"}
	mismatches := backup.VerifyChecksums(restored, expected)
	if len(mismatches) != 1 {
		t.Fatalf("expected 1 mismatch, got %v", mismatches)
	}
	if !strings.Contains(mismatches[0], "/etc/app.conf") {
		t.Fatalf("mismatch message should name the file: %v", mismatches[0])
	}
}

func TestVerifyChecksums_MissingRestoredFile(t *testing.T) {
	restored := map[string]string{}
	expected := map[string]string{"/missing.conf": "aabb"}
	mismatches := backup.VerifyChecksums(restored, expected)
	if len(mismatches) != 1 {
		t.Fatalf("expected 1 mismatch for missing file, got %v", mismatches)
	}
}

// ---------------------------------------------------------------------------
// Rollback is a no-op (snapshot persists; no automatic undo)
// ---------------------------------------------------------------------------

func TestBackupRollback_ReturnsNoOp(t *testing.T) {
	result, err := backup.Rollback(map[string]any{})
	if err != nil {
		t.Fatalf("rollback should not error: %v", err)
	}
	if result["rolled_back"] == true {
		t.Fatal("backup rollback should be a no-op")
	}
}

// ---------------------------------------------------------------------------
// Context cancellation
// ---------------------------------------------------------------------------

func TestRunBackup_CancelledContext(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel() // already cancelled

	_, err := backup.RunBackup(ctx, backup.BackupParams{
		BackupName:     "test",
		RetentionDays:  7,
		Paths:          []string{"/tmp"},
		ResticRepo:     "s3:s3.amazonaws.com/bucket/repo",
		ResticPassword: "pass",
	})
	// Should fail because context is cancelled (restic never starts or fails immediately)
	if err == nil {
		t.Fatal("expected error with cancelled context")
	}
}
