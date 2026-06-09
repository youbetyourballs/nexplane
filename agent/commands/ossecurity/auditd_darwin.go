//go:build darwin

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"time"
)

var execCommandAuditd = exec.Command
var bsmAuditControl = "/etc/security/audit_control"

var bsmProfiles = map[string]string{
	"cis_level1": "dir:/var/audit\nflags:lo,aa\nminfree:5\nnaflags:lo\npolicy:cnt,argv\nfilesz:2M\nexpire-after:10M\n",
	"cis_level2": "dir:/var/audit\nflags:lo,aa,ex,pc\nminfree:5\nnaflags:lo\npolicy:cnt,argv,arge\nfilesz:5M\nexpire-after:50M\n",
}

func auditdExecuteOS(params map[string]any) (map[string]any, error) {
	existing, _ := os.ReadFile(bsmAuditControl)
	snapshot := string(existing)

	rulesContent := ""
	if profile, ok := params["profile"].(string); ok && profile != "" {
		content, ok := bsmProfiles[profile]
		if !ok {
			return nil, fmt.Errorf("unknown profile %q: must be cis_level1 or cis_level2", profile)
		}
		rulesContent = content
	} else if custom, ok := params["rules"].(string); ok && custom != "" {
		rulesContent = custom
	} else {
		return nil, fmt.Errorf("profile or rules is required")
	}

	if err := os.WriteFile(bsmAuditControl, []byte(rulesContent), 0644); err != nil {
		return nil, fmt.Errorf("writing audit_control: %w", err)
	}

	execCommandAuditd("audit", "-s").CombinedOutput()

	return map[string]any{
		"rules_path": bsmAuditControl,
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func auditdRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, _ := params["snapshot"].(string)
	if snapshot == "" {
		os.Remove(bsmAuditControl)
		return map[string]any{"rolled_back": true}, nil
	}
	if err := os.WriteFile(bsmAuditControl, []byte(snapshot), 0644); err != nil {
		return nil, fmt.Errorf("restoring audit_control: %w", err)
	}
	execCommandAuditd("audit", "-s").Run()
	return map[string]any{"rolled_back": true}, nil
}
