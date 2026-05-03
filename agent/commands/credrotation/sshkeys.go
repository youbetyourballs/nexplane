package credrotation

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"strings"
	"time"
)

// SSHKeyParams holds inputs for SSH authorized_keys rotation actions.
type SSHKeyParams struct {
	Action            string // "backup" | "remove_old" | "add_new" | "restore"
	AuthKeysPath      string // absolute path to authorized_keys — caller resolves per username
	OldKeyFingerprint string // SHA256 fingerprint (without "SHA256:" prefix)
	NewPublicKey      string // full public key line to append
	BackupPath        string // injected via consumes for restore
}

// Execute dispatches to the correct sub-action.
func (p SSHKeyParams) Execute(ctx context.Context) (map[string]string, error) {
	switch p.Action {
	case "backup":
		return backupAuthorizedKeys(p.AuthKeysPath)
	case "remove_old":
		return nil, removeKeyByFingerprint(ctx, p.AuthKeysPath, p.OldKeyFingerprint)
	case "add_new":
		return nil, AddPublicKey(p.AuthKeysPath, p.NewPublicKey)
	case "restore":
		return nil, RestoreAuthorizedKeys(p.AuthKeysPath, p.BackupPath)
	default:
		return nil, fmt.Errorf("unknown ssh key action: %q", p.Action)
	}
}

// SSHKeyExecute is the CommandFunc-compatible entry point for executor.go.
func SSHKeyExecute(params map[string]any) (map[string]any, error) {
	p := sshKeyParamsFromMap(params)
	out, err := p.Execute(context.Background())
	if err != nil {
		return nil, err
	}
	result := make(map[string]any, len(out))
	for k, v := range out {
		result[k] = v
	}
	return result, nil
}

// SSHKeyRollback is the rollback CommandFunc-compatible entry point.
func SSHKeyRollback(params map[string]any) (map[string]any, error) {
	return SSHKeyExecute(params)
}

func sshKeyParamsFromMap(params map[string]any) SSHKeyParams {
	return SSHKeyParams{
		Action:            strParam(params, "action"),
		AuthKeysPath:      strParam(params, "auth_keys_path"),
		OldKeyFingerprint: strParam(params, "old_key_fingerprint"),
		NewPublicKey:      strParam(params, "new_public_key"),
		BackupPath:        strParam(params, "authorized_keys_backup_path"),
	}
}

// ── implementation ────────────────────────────────────────────────────────────

func backupAuthorizedKeys(src string) (map[string]string, error) {
	dest := fmt.Sprintf("%s.nexplane-bak-%s", src, time.Now().Format("20060102T150405"))
	data, err := os.ReadFile(src)
	if err != nil {
		return nil, fmt.Errorf("backup authorized_keys: %w", err)
	}
	if err := os.WriteFile(dest, data, 0600); err != nil {
		return nil, fmt.Errorf("write backup: %w", err)
	}
	return map[string]string{"authorized_keys_backup_path": dest}, nil
}

func removeKeyByFingerprint(ctx context.Context, path, fingerprint string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()

	var kept []string
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		fp, err := sshKeyFingerprint(ctx, line)
		if err != nil || fp != fingerprint {
			kept = append(kept, line)
		}
	}
	if err := scanner.Err(); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(strings.Join(kept, "\n")+"\n"), 0600)
}

// AddPublicKey appends pubKey as a new line to the authorized_keys file.
// Exported for testing.
func AddPublicKey(path, pubKey string) error {
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY|os.O_CREATE, 0600)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = fmt.Fprintf(f, "%s\n", strings.TrimSpace(pubKey))
	return err
}

// RestoreAuthorizedKeys overwrites the live authorized_keys with the backup.
// Exported for testing.
func RestoreAuthorizedKeys(original, backupPath string) error {
	data, err := os.ReadFile(backupPath)
	if err != nil {
		return fmt.Errorf("read backup %s: %w", backupPath, err)
	}
	return os.WriteFile(original, data, 0600)
}
