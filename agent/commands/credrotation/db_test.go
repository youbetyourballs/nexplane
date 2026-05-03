package credrotation_test

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"nexplane-agent/commands/credrotation"
)

// ── generatePassword ──────────────────────────────────────────────────────────

func TestGeneratePassword_ProducesNewPasswordSlot(t *testing.T) {
	p := credrotation.DBRotateParams{Action: "generate"}
	out, err := p.Execute(context.Background())
	if err != nil {
		t.Fatalf("generate: unexpected error: %v", err)
	}
	pw, ok := out["new_password"]
	if !ok {
		t.Fatal("expected 'new_password' key in output")
	}
	if len(pw) < 20 {
		t.Errorf("password too short: %q", pw)
	}
}

func TestGeneratePassword_IsUnique(t *testing.T) {
	p := credrotation.DBRotateParams{Action: "generate"}
	out1, _ := p.Execute(context.Background())
	out2, _ := p.Execute(context.Background())
	if out1["new_password"] == out2["new_password"] {
		t.Error("two generated passwords should not be identical")
	}
}

// ── backupConfigFiles ─────────────────────────────────────────────────────────

func TestBackupConfigFiles_CreatesBackupFile(t *testing.T) {
	dir := t.TempDir()
	original := filepath.Join(dir, "db.env")
	content := "DB_PASSWORD=oldvalue\n"
	os.WriteFile(original, []byte(content), 0600)

	p := credrotation.DBRotateParams{
		Action:      "backup_config",
		ConfigPaths: []string{original},
	}
	out, err := p.Execute(context.Background())
	if err != nil {
		t.Fatalf("backup: %v", err)
	}

	backupPathsStr, ok := out["config_backup_paths"]
	if !ok {
		t.Fatal("expected 'config_backup_paths' in output")
	}
	backupPath := strings.Split(backupPathsStr, ",")[0]
	if !strings.Contains(backupPath, ".nexplane-bak-") {
		t.Errorf("backup path doesn't contain expected suffix: %s", backupPath)
	}

	data, err := os.ReadFile(backupPath)
	if err != nil {
		t.Fatalf("reading backup: %v", err)
	}
	if string(data) != content {
		t.Errorf("backup content mismatch: got %q", data)
	}
}

func TestBackupConfigFiles_MissingFileFails(t *testing.T) {
	p := credrotation.DBRotateParams{
		Action:      "backup_config",
		ConfigPaths: []string{"/nonexistent/path/db.env"},
	}
	_, err := p.Execute(context.Background())
	if err == nil {
		t.Fatal("expected error for missing file")
	}
}

// ── updateConfigFiles ─────────────────────────────────────────────────────────

func TestUpdateConfigFiles_ReplacesPassword(t *testing.T) {
	dir := t.TempDir()
	envFile := filepath.Join(dir, "db.env")
	os.WriteFile(envFile, []byte("DB_USER=appuser\nDB_PASSWORD=oldpass\nDB_HOST=localhost\n"), 0600)

	p := credrotation.DBRotateParams{
		Action:      "update_config",
		ConfigPaths: []string{envFile},
		NewPassword: "newpass123",
		DBUsername:  "appuser",
	}
	_, err := p.Execute(context.Background())
	if err != nil {
		t.Fatalf("update_config: %v", err)
	}

	updated, _ := os.ReadFile(envFile)
	if strings.Contains(string(updated), "oldpass") {
		t.Error("old password still present after update")
	}
	if !strings.Contains(string(updated), "newpass123") {
		t.Error("new password not found after update")
	}
}

// ── restoreConfigFiles ────────────────────────────────────────────────────────

func TestRestoreConfigFiles_RestoresOriginal(t *testing.T) {
	dir := t.TempDir()
	original := filepath.Join(dir, "db.env")
	origContent := "DB_PASSWORD=original\n"
	os.WriteFile(original, []byte(origContent), 0600)

	// Simulate backup
	bakName := original + ".nexplane-bak-" + time.Now().Format("20060102T150405")
	os.WriteFile(bakName, []byte(origContent), 0600)

	// Overwrite original with "new" content
	os.WriteFile(original, []byte("DB_PASSWORD=new\n"), 0600)

	p := credrotation.DBRotateParams{
		Action:            "restore_config",
		ConfigBackupPaths: []string{bakName},
	}
	_, err := p.Execute(context.Background())
	if err != nil {
		t.Fatalf("restore: %v", err)
	}

	restored, _ := os.ReadFile(original)
	if string(restored) != origContent {
		t.Errorf("restore did not recover original content: got %q", restored)
	}
}

// ── unknown action ────────────────────────────────────────────────────────────

func TestExecute_UnknownActionFails(t *testing.T) {
	p := credrotation.DBRotateParams{Action: "explode"}
	_, err := p.Execute(context.Background())
	if err == nil {
		t.Fatal("expected error for unknown action")
	}
	if !strings.Contains(err.Error(), "unknown db rotation action") {
		t.Errorf("unexpected error message: %v", err)
	}
}

// ── replaceCredentialInContent ────────────────────────────────────────────────

func TestReplaceCredentialInContent_EnvFormat(t *testing.T) {
	input := "DB_USER=appuser\nDB_PASSWORD=oldpass\nDB_HOST=db.local\n"
	got := credrotation.ReplaceCredentialInContent(input, "appuser", "newpass")
	if strings.Contains(got, "oldpass") {
		t.Error("old password still present")
	}
	if !strings.Contains(got, "newpass") {
		t.Error("new password not found")
	}
}

func TestReplaceEnvVarInContent_ReplacesLine(t *testing.T) {
	input := "FOO=bar\nAPI_KEY=old-key-value\nBAZ=qux\n"
	got := credrotation.ReplaceEnvVarInContent(input, "API_KEY", "new-key-value")
	if strings.Contains(got, "old-key-value") {
		t.Error("old value still present")
	}
	if !strings.Contains(got, "API_KEY=new-key-value") {
		t.Error("new value not found")
	}
	// Unrelated vars preserved
	if !strings.Contains(got, "FOO=bar") {
		t.Error("FOO line lost")
	}
}
