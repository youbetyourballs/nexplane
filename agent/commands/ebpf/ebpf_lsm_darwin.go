//go:build darwin

package ebpf

import (
	"fmt"
	"os"
	"os/exec"
	"time"
)

const (
	auditControlPath = "/etc/security/audit_control"
	auditControlContent = `dir:/var/audit
flags:lo,aa,ad,fd,fm,-all
minfree:5
naflags:lo,aa
policy:cnt,argv
filesz:2M
expire-after:10M
`
)

func ConfigureEbpfLsmExecute(params map[string]any) (map[string]any, error) {
	// Snapshot current audit_control
	originalContents := ""
	if data, err := os.ReadFile(auditControlPath); err == nil {
		originalContents = string(data)
	}

	// Write new audit_control
	if err := os.WriteFile(auditControlPath, []byte(auditControlContent), 0644); err != nil {
		return nil, fmt.Errorf("writing audit_control: %w", err)
	}

	// Reload BSM audit
	exec.Command("audit", "-s").Run() //nolint:errcheck

	return map[string]any{
		"snapshot":      originalContents,
		"configured_at": time.Now().UTC().Format(time.RFC3339),
		"audit_flags":   "lo,aa,ad,fd,fm,-all",
	}, nil
}

func ConfigureEbpfLsmRollback(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)

	if snapshot == "" {
		os.Remove(auditControlPath) //nolint:errcheck
	} else {
		if err := os.WriteFile(auditControlPath, []byte(snapshot), 0644); err != nil {
			return nil, fmt.Errorf("restoring audit_control: %w", err)
		}
	}

	exec.Command("audit", "-s").Run() //nolint:errcheck

	return map[string]any{"rolled_back": true}, nil
}
