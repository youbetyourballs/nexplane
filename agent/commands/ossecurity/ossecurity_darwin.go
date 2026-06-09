//go:build darwin

package ossecurity

import "fmt"

func selinuxExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_selinux requires Linux")
}
func selinuxRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_selinux requires Linux")
}
func apparmorExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_apparmor requires Linux")
}
func apparmorRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_apparmor requires Linux")
}
func seccompExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_seccomp requires Linux")
}
func seccompRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("configure_seccomp requires Linux")
}
func sysctlExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_sysctl_hardening requires Linux")
}
func sysctlRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_sysctl_hardening requires Linux")
}
func blacklistExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("blacklist_kernel_modules requires Linux")
}
func blacklistRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("blacklist_kernel_modules requires Linux")
}
func mountExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_mount_options requires Linux")
}
func mountRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("harden_mount_options requires Linux")
}
func auditdExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("deploy_auditd_rules requires Linux")
}
func auditdRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("deploy_auditd_rules requires Linux")
}
func fimExecuteOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("setup_file_integrity_monitoring requires Linux")
}
func fimRollbackOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("setup_file_integrity_monitoring requires Linux")
}
func auditPostureOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_os_security_posture requires Linux")
}
