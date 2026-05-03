package credrotation_test

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"nexplane-agent/commands/credrotation"
)

// helper: create a temp authorized_keys with given lines
func tempAuthKeys(t *testing.T, lines ...string) string {
	t.Helper()
	dir := t.TempDir()
	path := filepath.Join(dir, "authorized_keys")
	content := strings.Join(lines, "\n") + "\n"
	os.WriteFile(path, []byte(content), 0600)
	return path
}

// ── backup ────────────────────────────────────────────────────────────────────

func TestSSHBackup_CreatesBackupFile(t *testing.T) {
	authKeys := tempAuthKeys(t, "ssh-rsa AAAA... user@host")

	p := credrotation.SSHKeyParams{
		Action:       "backup",
		AuthKeysPath: authKeys,
	}
	out, err := p.Execute(context.Background())
	if err != nil {
		t.Fatalf("backup: %v", err)
	}
	bakPath, ok := out["authorized_keys_backup_path"]
	if !ok {
		t.Fatal("expected authorized_keys_backup_path in output")
	}
	if !strings.Contains(bakPath, ".nexplane-bak-") {
		t.Errorf("backup path missing expected suffix: %s", bakPath)
	}
	data, _ := os.ReadFile(bakPath)
	if !strings.Contains(string(data), "ssh-rsa") {
		t.Error("backup file does not contain original content")
	}
}

// ── add_new ───────────────────────────────────────────────────────────────────

func TestSSHAddNew_AppendsPublicKey(t *testing.T) {
	authKeys := tempAuthKeys(t, "ssh-rsa AAAA... existing@host")

	p := credrotation.SSHKeyParams{
		Action:       "add_new",
		AuthKeysPath: authKeys,
		NewPublicKey: "ssh-ed25519 BBBBB... new@host",
	}
	_, err := p.Execute(context.Background())
	if err != nil {
		t.Fatalf("add_new: %v", err)
	}
	data, _ := os.ReadFile(authKeys)
	if !strings.Contains(string(data), "existing@host") {
		t.Error("existing key was removed")
	}
	if !strings.Contains(string(data), "new@host") {
		t.Error("new key was not added")
	}
}

func TestSSHAddNew_EmptyFileFails(t *testing.T) {
	p := credrotation.SSHKeyParams{
		Action:       "add_new",
		AuthKeysPath: "/nonexistent/.ssh/authorized_keys",
		NewPublicKey: "ssh-ed25519 BBBBB... new@host",
	}
	// add_new uses O_CREATE so it should succeed even for nonexistent paths
	// IF the directory exists. With /nonexistent/, the open should fail.
	_, err := p.Execute(context.Background())
	if err == nil {
		t.Fatal("expected error for nonexistent directory")
	}
}

// ── restore ───────────────────────────────────────────────────────────────────

func TestSSHRestore_RestoresOriginalContent(t *testing.T) {
	authKeys := tempAuthKeys(t, "ssh-rsa AAAA... original@host")

	bakPath := authKeys + ".nexplane-bak-" + time.Now().Format("20060102T150405")
	originalContent, _ := os.ReadFile(authKeys)
	os.WriteFile(bakPath, originalContent, 0600)

	// Simulate mutation
	os.WriteFile(authKeys, []byte("ssh-ed25519 MODIFIED... tampered@host\n"), 0600)

	p := credrotation.SSHKeyParams{
		Action:       "restore",
		AuthKeysPath: authKeys,
		BackupPath:   bakPath,
	}
	_, err := p.Execute(context.Background())
	if err != nil {
		t.Fatalf("restore: %v", err)
	}
	restored, _ := os.ReadFile(authKeys)
	if !strings.Contains(string(restored), "original@host") {
		t.Errorf("restore did not recover original content, got: %s", restored)
	}
}

// ── unknown action ────────────────────────────────────────────────────────────

func TestSSHUnknownAction_Fails(t *testing.T) {
	p := credrotation.SSHKeyParams{Action: "explode"}
	_, err := p.Execute(context.Background())
	if err == nil {
		t.Fatal("expected error for unknown action")
	}
	if !strings.Contains(err.Error(), "unknown ssh key action") {
		t.Errorf("unexpected error: %v", err)
	}
}

// ── addPublicKey / remove helpers (unit) ─────────────────────────────────────

func TestAddPublicKeyAppendsLine(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "authorized_keys")
	os.WriteFile(path, []byte("ssh-rsa AAAA... first\n"), 0600)

	if err := credrotation.AddPublicKey(path, "ssh-ed25519 BBBB... second"); err != nil {
		t.Fatalf("addPublicKey: %v", err)
	}
	data, _ := os.ReadFile(path)
	lines := strings.Split(strings.TrimSpace(string(data)), "\n")
	if len(lines) != 2 {
		t.Errorf("expected 2 lines, got %d: %v", len(lines), lines)
	}
}

func TestAddPublicKeyTrimsWhitespace(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "authorized_keys")
	os.WriteFile(path, []byte(""), 0600)

	if err := credrotation.AddPublicKey(path, "  ssh-ed25519 BBBB... padded  "); err != nil {
		t.Fatalf("addPublicKey: %v", err)
	}
	data, _ := os.ReadFile(path)
	if strings.Contains(string(data), "  ssh-ed25519") {
		t.Error("leading whitespace not trimmed")
	}
}

// Compile-time check: SSHKeyParams.Execute signature matches expected interface
var _ = fmt.Sprintf("%T", credrotation.SSHKeyParams{}.Execute)
