//go:build !linux && !windows && !darwin

package compliance

import "fmt"

func auditCISComplianceOS(level int, osFamily string) (map[string]any, error) {
	return nil, fmt.Errorf("audit_cis_compliance is only supported on Linux (requested os_family=%s)", osFamily)
}

func collectEvidenceOS(params map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("collect_evidence is not supported on this platform")
}
