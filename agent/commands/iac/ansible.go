package iac

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// AnsibleParams matches the ansible_playbook change type parameters.
type AnsibleParams struct {
	PlaybookPath         string         `json:"playbook_path"`
	Inventory            string         `json:"inventory"`
	ExtraVars            map[string]any `json:"extra_vars"`
	Limit                string         `json:"limit"`
	CheckMode            bool           `json:"check_mode"`
	DryRun               bool           `json:"dry_run"`
	RollbackPlaybookPath string         `json:"rollback_playbook_path"`
	ChangeID             string         `json:"change_id"`
}

// AnsibleCheck runs ansible-playbook --check --diff, returns output for blast radius.
func AnsibleCheck(ctx context.Context, p AnsibleParams) (string, error) {
	args, cleanup, err := buildAnsibleArgs(p, p.PlaybookPath)
	if cleanup != nil {
		defer cleanup()
	}
	if err != nil {
		return "", err
	}
	args = append(args, "--check", "--diff")
	return captureCmd(ctx, ansibleDir(p.PlaybookPath), "ansible-playbook", args...)
}

// AnsibleRun executes the playbook (or the rollback playbook if rollback=true).
func AnsibleRun(ctx context.Context, p AnsibleParams, rollback bool) (string, error) {
	playbook := p.PlaybookPath
	if rollback {
		if p.RollbackPlaybookPath == "" {
			return "", fmt.Errorf("no rollback_playbook_path configured for this change")
		}
		playbook = p.RollbackPlaybookPath
	}
	args, cleanup, err := buildAnsibleArgs(p, playbook)
	if cleanup != nil {
		defer cleanup()
	}
	if err != nil {
		return "", err
	}
	return captureCmd(ctx, ansibleDir(playbook), "ansible-playbook", args...)
}

// ansibleDir returns the directory for the playbook, falling back to "." if the
// directory does not exist (e.g. in tests using fake paths).
func ansibleDir(playbookPath string) string {
	dir := filepath.Dir(playbookPath)
	if _, err := os.Stat(dir); err != nil {
		return "."
	}
	return dir
}

func buildAnsibleArgs(p AnsibleParams, playbook string) ([]string, func(), error) {
	args := []string{playbook}
	var cleanup func()

	// Inventory: write inline content to a temp file if it does not exist as a path.
	if _, err := os.Stat(p.Inventory); os.IsNotExist(err) {
		f, err := os.CreateTemp("", "nexplane-inventory-"+p.ChangeID+"-*.ini")
		if err != nil {
			return nil, nil, fmt.Errorf("writing inventory: %w", err)
		}
		if _, err := f.WriteString(p.Inventory); err != nil {
			return nil, nil, err
		}
		f.Close()
		cleanup = func() { os.Remove(f.Name()) }
		args = append(args, "-i", f.Name())
	} else {
		args = append(args, "-i", p.Inventory)
	}

	if len(p.ExtraVars) > 0 {
		evJSON, _ := json.Marshal(p.ExtraVars)
		args = append(args, "--extra-vars", string(evJSON))
	}
	if p.Limit != "" {
		args = append(args, "--limit", p.Limit)
	}
	return args, cleanup, nil
}
