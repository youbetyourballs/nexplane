//go:build darwin

package ossecurity

import "fmt"

func auditPostureOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_os_security_posture: not yet implemented on darwin")
}
