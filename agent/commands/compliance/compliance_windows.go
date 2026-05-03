//go:build windows

package compliance

import (
	"fmt"
	"os"
	"time"
)

func auditCISComplianceOS(level int, osFamily string) (map[string]any, error) {
	return nil, fmt.Errorf("audit_cis_compliance is not supported on Windows")
}

func collectEvidenceOS(params map[string]any) (map[string]any, error) {
	hostname, _ := os.Hostname()
	return map[string]any{
		"framework":    params["framework"],
		"control_id":  params["control_id"],
		"hostname":    hostname,
		"collected_at": time.Now().UTC().Format(time.RFC3339),
		"artifacts":   []map[string]any{},
		"note":        "Windows evidence collection not yet implemented",
	}, nil
}
