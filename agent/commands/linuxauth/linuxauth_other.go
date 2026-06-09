//go:build !linux && !darwin

package linuxauth

import "fmt"

func pamExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_pam requires Linux")
}
func pamRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_pam requires Linux")
}
func sshExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_ssh requires Linux")
}
func sshRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_ssh requires Linux")
}
func auditUsersOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_users_and_groups requires Linux")
}
func auditPrivescOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_privesc_vulnerabilities requires Linux")
}
func certsExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("manage_ca_certificates requires Linux")
}
func certsRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("manage_ca_certificates requires Linux")
}
func ntpExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_ntp requires Linux")
}
func ntpRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_ntp requires Linux")
}
