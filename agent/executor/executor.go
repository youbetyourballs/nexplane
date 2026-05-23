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
	"nexplane-agent/commands/winharden"
	"nexplane-agent/commands/linuxharden"
	"nexplane-agent/commands/crossplatform"
	"nexplane-agent/commands/linuxupgrade"
	"nexplane-agent/commands/fleet"
	"nexplane-agent/commands/appdiscovery"
	"nexplane-agent/commands/dbadmin"
	"nexplane-agent/commands/containerizebuild"
	"nexplane-agent/commands/containerizeretire"
	"nexplane-agent/commands/deepdiscover"
	"nexplane-agent/commands/listpkgs"
	"nexplane-agent/commands/linuxpatch"
	"nexplane-agent/commands/winpatch"
	"nexplane-agent/commands/credrotation"
	"nexplane-agent/commands/macos"
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
	// Windows hardening (Spec 5c)
	"configure_laps":                 winharden.ConfigureLAPSExecute,
	"enable_credential_guard":        winharden.EnableCredentialGuardExecute,
	"enforce_powershell_clm":         winharden.EnforcePowerShellCLMExecute,
	"deploy_applocker_policy":        winharden.DeployAppLockerPolicyExecute,
	"harden_smb":                     winharden.HardenSMBExecute,
	"enable_bitlocker":               winharden.EnableBitLockerExecute,
	"configure_windows_firewall":     winharden.ConfigureWindowsFirewallExecute,
	"harden_tls_protocols":           winharden.HardenTLSProtocolsExecute,
	"harden_rdp":                     winharden.HardenRDPExecute,
	"configure_windows_audit_policy": winharden.ConfigureWindowsAuditPolicyExecute,
	"audit_scheduled_tasks":          winharden.AuditScheduledTasksExecute,
	"harden_registry":                winharden.HardenRegistryExecute,
	"wdac_audit":                     winharden.WDACauditExecute,
	"wdac_enforce":                   winharden.WDACenforceExecute,
	"asr_audit":                      winharden.ASRauditExecute,
	"asr_enforce":                    winharden.ASRenforceExecute,
	"sysmon_deploy":                  winharden.SysmonDeployExecute,
	"sysmon_fim":                     winharden.SysmonFIMExecute,
	"seccomp_learn":                  linuxharden.SeccompLearnExecute,
	"iptables_log_baseline":          linuxharden.IptablesLogBaselineExecute,
	"trivy_scan":                     linuxharden.TrivyScanExecute,
	"lynis_audit":                    linuxharden.LynisAuditExecute,
	"openscap_scan":                  linuxharden.OpenscapScanExecute,
	"authorized_keys_audit":          linuxharden.AuthorizedKeysAuditExecute,
	"sudoers_audit":                  linuxharden.SudoersAuditExecute,
	"suid_scan":                      linuxharden.SuidScanExecute,
	"ssl_cert_inspect":               linuxharden.SslCertInspectExecute,
	// Cross-platform (Spec 5d)
	"manage_tls_certificates":  crossplatform.ManageTLSCertificatesExecute,
	"configure_dns_resolver":   crossplatform.ConfigureDNSResolverExecute,
	"audit_software_inventory": crossplatform.AuditSoftwareInventoryExecute,
	// Linux upgrade (Spec 5e)
	"upgrade_linux_instance":   linuxupgrade.UpgradeLinuxInstanceExecute,
	// OS major version upgrade commands
	"preflight_os_upgrade": linuxupgrade.PreflightExecute,
	"execute_os_upgrade":   linuxupgrade.UpgradeLinuxInstanceExecute,
	"verify_os_upgrade":    linuxupgrade.VerifyExecute,
	// Fleet operations
	"restart_service":  fleet.RestartServiceExecute,
	"push_config_file": fleet.PushConfigFileExecute,
	"distribute_file":  fleet.DistributeFileExecute,
	"health_check":     fleet.HealthCheckExecute,
	// App discovery (containerize foundation)
	"discover_applications": appdiscovery.DiscoverApplicationsExecute,
	// Containerize build
	"containerize_build": containerizebuild.ContainerizeBuildExecute,
	// Containerize retire
	"containerize_retire": containerizeretire.ContainerizeRetireExecute,
	// Deep discover (containerization)
	"deep_discover": deepdiscover.Execute,
	// Database administration
	"provision_db_user":    dbadmin.ExecuteCommand,
	"deprovision_db_user":  dbadmin.ExecuteCommand,
	"db_permission_change": dbadmin.ExecuteCommand,
	"configure_db_audit":   dbadmin.ExecuteCommand,
	"db_connection_config": dbadmin.ExecuteCommand,
	// Software inventory (CIS Control 2)
	"list_installed_packages": listpkgs.Execute,
	// Linux patching (Spec 5f)
	"apply_linux_patches":      linuxpatch.ApplyLinuxPatchesExecute,
	"audit_linux_patch_status": linuxpatch.AuditLinuxPatchStatusExecute,
	// Windows patching (Spec 5f)
	"apply_windows_patches":      winpatch.ApplyWindowsPatchesExecute,
	"audit_windows_patch_status": winpatch.AuditWindowsPatchStatusExecute,
	// macOS hardening
	"filevault_status":       macos.FilevaultStatusExecute,
	"filevault_enable":       macos.FilevaultEnableExecute,
	"gatekeeper_status":      macos.GatekeeperStatusExecute,
	"gatekeeper_enable":      macos.GatekeeperEnableExecute,
	"gatekeeper_disable":     macos.GatekeeperDisableExecute,
	"softwareupdate_list":    macos.SoftwareupdateListExecute,
	"softwareupdate_install": macos.SoftwareupdateInstallExecute,
	"profiles_list":          macos.ProfilesListExecute,
	"launchctl_list":         macos.LaunchctlListExecute,
	"macos_sysinfo":          macos.MacosSysinfoExecute,
	"defaults_write":         macos.DefaultsWriteExecute,
	"santa_check":            macos.SantaCheckExecute,
	"profiles_install":       macos.ProfilesInstallExecute,
	"profiles_remove":        macos.ProfilesRemoveExecute,
	// Credential rotation
	"rotate_ssh_keys":        credrotation.SSHKeyExecute,
	"rotate_db_creds":        credrotation.DBRotateExecute,
	"rotate_api_key":         credrotation.APIKeyEnvExecute,
	"rotate_jwt_signing_key": credrotation.JWTKeyRotateExecute,
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
	// Windows hardening (Spec 5c)
	"configure_laps":                 winharden.ConfigureLAPSRollback,
	"enable_credential_guard":        winharden.EnableCredentialGuardRollback,
	"enforce_powershell_clm":         winharden.EnforcePowerShellCLMRollback,
	"deploy_applocker_policy":        winharden.DeployAppLockerPolicyRollback,
	"harden_smb":                     winharden.HardenSMBRollback,
	"enable_bitlocker":               winharden.EnableBitLockerRollback,
	"configure_windows_firewall":     winharden.ConfigureWindowsFirewallRollback,
	"harden_tls_protocols":           winharden.HardenTLSProtocolsRollback,
	"harden_rdp":                     winharden.HardenRDPRollback,
	"configure_windows_audit_policy": winharden.ConfigureWindowsAuditPolicyRollback,
	"harden_registry":                winharden.HardenRegistryRollback,
	"wdac_audit":                     winharden.WDACrollback,
	"wdac_enforce":                   winharden.WDACrollback,
	"asr_audit":                      winharden.ASRrollback,
	"asr_enforce":                    winharden.ASRrollback,
	"sysmon_deploy":                  winharden.SysmonRollback,
	"sysmon_fim":                     winharden.SysmonRollback,
	"seccomp_learn":                  linuxharden.SeccompLearnRollback,
	"iptables_log_baseline":          linuxharden.IptablesLogBaselineRollback,
	"trivy_scan":                     linuxharden.TrivyScanRollback,
	"lynis_audit":                    linuxharden.LynisAuditRollback,
	"openscap_scan":                  linuxharden.OpenscapScanRollback,
	"authorized_keys_audit":          linuxharden.AuthorizedKeysAuditRollback,
	"sudoers_audit":                  linuxharden.SudoersAuditRollback,
	"suid_scan":                      linuxharden.SuidScanRollback,
	"ssl_cert_inspect":               linuxharden.SslCertInspectRollback,
	"manage_tls_certificates": crossplatform.ManageTLSCertificatesRollback,
	"configure_dns_resolver":  crossplatform.ConfigureDNSResolverRollback,
	"upgrade_linux_instance":  linuxupgrade.UpgradeLinuxInstanceRollback,
	"execute_os_upgrade":      linuxupgrade.UpgradeLinuxInstanceRollback,
	"containerize_retire":     containerizeretire.ContainerizeRetireRollback,
	// Linux patching rollback
	"apply_linux_patches":   linuxpatch.ApplyLinuxPatchesRollback,
	// Windows patching rollback
	"apply_windows_patches": winpatch.ApplyWindowsPatchesRollback,
	// macOS rollbacks
	"filevault_enable":   macos.FilevaultEnableRollback,
	"defaults_write":     macos.DefaultsWriteRollback,
	"gatekeeper_enable":  macos.GatekeeperEnableRollback,
	"gatekeeper_disable": macos.GatekeeperDisableRollback,
	"profiles_install":   macos.ProfilesInstallRollback,
	"profiles_remove":    macos.ProfilesRemoveRollback,
	// Credential rotation rollback
	"rotate_ssh_keys":        credrotation.SSHKeyRollback,
	"rotate_db_creds":        credrotation.DBRotateRollback,
	"rotate_api_key":         credrotation.APIKeyEnvRollback,
	"rotate_jwt_signing_key": credrotation.JWTKeyRotateRollback,
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
