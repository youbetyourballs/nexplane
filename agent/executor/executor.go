package executor

import (
	"fmt"

	"nexplane-agent/commands/changip"
	"nexplane-agent/commands/configsyslog"
	"nexplane-agent/commands/estimatesize"
	"nexplane-agent/commands/ebpf"
	"nexplane-agent/commands/linuxauth"
	"nexplane-agent/commands/ossecurity"
	"nexplane-agent/commands/uploadimage"
	"nexplane-agent/commands/virtualize"
)

// Result is the outcome of a command execution.
type Result struct {
	Status string
	Data   map[string]any
	Error  string
}

// CommandFunc is the signature every command must implement.
type CommandFunc func(params map[string]any) (map[string]any, error)

var commands = map[string]CommandFunc{
	"estimate_image_size":      estimatesize.Execute,
	"change_ip":                changip.Execute,
	"configure_syslog":         configsyslog.Execute,
	"virtualize_for_migration": virtualize.Execute,
	"upload_image":             uploadimage.Execute,
	"configure_pam":                 linuxauth.ConfigurePAMExecute,
	"harden_ssh":                    linuxauth.HardenSSHExecute,
	"audit_users_and_groups":        linuxauth.AuditUsersAndGroupsExecute,
	"audit_privesc_vulnerabilities": linuxauth.AuditPrivescVulnerabilitiesExecute,
	"manage_ca_certificates":        linuxauth.ManageCACertificatesExecute,
	"configure_ntp":                 linuxauth.ConfigureNTPExecute,
	// OS security (Spec 5a)
	"configure_selinux":               ossecurity.ConfigureSELinuxExecute,
	"configure_apparmor":              ossecurity.ConfigureAppArmorExecute,
	"configure_seccomp":               ossecurity.ConfigureSeccompExecute,
	"apply_sysctl_hardening":          ossecurity.ApplySysctlHardeningExecute,
	"configure_host_firewall":         ossecurity.ConfigureHostFirewallExecute,
	"blacklist_kernel_modules":        ossecurity.BlacklistKernelModulesExecute,
	"harden_mount_options":            ossecurity.HardenMountOptionsExecute,
	"deploy_auditd_rules":             ossecurity.DeployAuditdRulesExecute,
	"setup_file_integrity_monitoring": ossecurity.SetupFileIntegrityMonitoringExecute,
	"audit_os_security_posture":       ossecurity.AuditOSSecurityPostureExecute,
	// eBPF (Spec 5a)
	"deploy_ebpf_policy":              ebpf.DeployEBPFPolicyExecute,
	"configure_ebpf_security_policy":  ebpf.ConfigureEBPFSecurityPolicyExecute,
	"audit_ebpf_posture":              ebpf.AuditEBPFPostureExecute,
}

var rollbacks = map[string]CommandFunc{
	"change_ip":                changip.Rollback,
	"configure_syslog":         configsyslog.Rollback,
	"virtualize_for_migration": virtualize.Rollback,
	"upload_image":             uploadimage.Rollback,
	"configure_pam":          linuxauth.ConfigurePAMRollback,
	"harden_ssh":             linuxauth.HardenSSHRollback,
	"manage_ca_certificates": linuxauth.ManageCACertificatesRollback,
	"configure_ntp":          linuxauth.ConfigureNTPRollback,
	// OS security (Spec 5a)
	"configure_selinux":               ossecurity.ConfigureSELinuxRollback,
	"configure_apparmor":              ossecurity.ConfigureAppArmorRollback,
	"configure_seccomp":               ossecurity.ConfigureSeccompRollback,
	"apply_sysctl_hardening":          ossecurity.ApplySysctlHardeningRollback,
	"configure_host_firewall":         ossecurity.ConfigureHostFirewallRollback,
	"blacklist_kernel_modules":        ossecurity.BlacklistKernelModulesRollback,
	"harden_mount_options":            ossecurity.HardenMountOptionsRollback,
	"deploy_auditd_rules":             ossecurity.DeployAuditdRulesRollback,
	"setup_file_integrity_monitoring": ossecurity.SetupFileIntegrityMonitoringRollback,
	// eBPF (Spec 5a)
	"deploy_ebpf_policy":              ebpf.DeployEBPFPolicyRollback,
	"configure_ebpf_security_policy":  ebpf.ConfigureEBPFSecurityPolicyRollback,
}

// Dispatch routes a command to its implementation.
// If rollback is true, previousResult is merged into params for context.
func Dispatch(command string, params map[string]any, rollback bool, previousResult map[string]any) Result {
	var fn CommandFunc
	var ok bool

	if rollback {
		fn, ok = rollbacks[command]
		if !ok {
			return Result{Status: "failed", Error: fmt.Sprintf("no rollback defined for command %q", command)}
		}
		merged := make(map[string]any, len(params)+len(previousResult))
		for k, v := range previousResult {
			merged[k] = v
		}
		for k, v := range params {
			merged[k] = v
		}
		params = merged
	} else {
		fn, ok = commands[command]
		if !ok {
			return Result{Status: "failed", Error: fmt.Sprintf("unknown command %q", command)}
		}
	}

	data, err := fn(params)
	if err != nil {
		return Result{Status: "failed", Data: data, Error: err.Error()}
	}
	return Result{Status: "completed", Data: data}
}
