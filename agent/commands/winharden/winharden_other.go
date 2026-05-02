//go:build !windows

package winharden

import "fmt"

func lapsExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_laps requires Windows")
}
func lapsRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_laps requires Windows")
}
func credGuardExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("enable_credential_guard requires Windows")
}
func credGuardRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("enable_credential_guard requires Windows")
}
func psclmExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("enforce_powershell_clm requires Windows")
}
func psclmRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("enforce_powershell_clm requires Windows")
}
func applockerExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("deploy_applocker_policy requires Windows")
}
func applockerRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("deploy_applocker_policy requires Windows")
}
func smbExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_smb requires Windows")
}
func smbRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_smb requires Windows")
}
func bitlockerExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("enable_bitlocker requires Windows")
}
func bitlockerRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("enable_bitlocker requires Windows")
}
func winfirewallExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_windows_firewall requires Windows")
}
func winfirewallRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_windows_firewall requires Windows")
}
func tlsExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_tls_protocols requires Windows")
}
func tlsRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_tls_protocols requires Windows")
}
func rdpExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_rdp requires Windows")
}
func rdpRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_rdp requires Windows")
}
func auditpolExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_windows_audit_policy requires Windows")
}
func auditpolRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_windows_audit_policy requires Windows")
}
func auditTasksOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_scheduled_tasks requires Windows")
}
func registryExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_registry requires Windows")
}
func registryRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_registry requires Windows")
}
