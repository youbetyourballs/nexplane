package winharden

import "fmt"

func ConfigureLAPSExecute(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if action != "enable" && action != "disable" {
		return nil, fmt.Errorf("action must be 'enable' or 'disable', got %q", action)
	}
	return lapsExecuteOS(params)
}

func ConfigureLAPSRollback(params map[string]any) (map[string]any, error) {
	return lapsRollbackOS(params)
}

func EnableCredentialGuardExecute(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if action != "enable" && action != "disable" {
		return nil, fmt.Errorf("action must be 'enable' or 'disable', got %q", action)
	}
	return credGuardExecuteOS(params)
}

func EnableCredentialGuardRollback(params map[string]any) (map[string]any, error) {
	return credGuardRollbackOS(params)
}

func EnforcePowerShellCLMExecute(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if action != "enable" && action != "disable" {
		return nil, fmt.Errorf("action must be 'enable' or 'disable', got %q", action)
	}
	return psclmExecuteOS(params)
}

func EnforcePowerShellCLMRollback(params map[string]any) (map[string]any, error) {
	return psclmRollbackOS(params)
}

func DeployAppLockerPolicyExecute(params map[string]any) (map[string]any, error) {
	if _, ok := params["policy"].(string); !ok {
		return nil, fmt.Errorf("policy (AppLocker XML) is required")
	}
	return applockerExecuteOS(params)
}

func DeployAppLockerPolicyRollback(params map[string]any) (map[string]any, error) {
	return applockerRollbackOS(params)
}

func HardenSMBExecute(params map[string]any) (map[string]any, error) {
	return smbExecuteOS(params)
}

func HardenSMBRollback(params map[string]any) (map[string]any, error) {
	return smbRollbackOS(params)
}

func EnableBitLockerExecute(params map[string]any) (map[string]any, error) {
	protector, _ := params["protector"].(string)
	if protector == "" {
		protector = "tpm"
	}
	valid := map[string]bool{"tpm": true, "tpm_pin": true, "recovery_key_only": true}
	if !valid[protector] {
		return nil, fmt.Errorf("protector must be tpm, tpm_pin, or recovery_key_only, got %q", protector)
	}
	if protector == "tpm_pin" {
		if pin, _ := params["pin"].(string); pin == "" {
			return nil, fmt.Errorf("pin is required when protector=tpm_pin")
		}
	}
	return bitlockerExecuteOS(params)
}

func EnableBitLockerRollback(params map[string]any) (map[string]any, error) {
	return bitlockerRollbackOS(params)
}

func ConfigureWindowsFirewallExecute(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	valid := map[string]bool{"add_rule": true, "remove_rule": true, "set_default_action": true}
	if !valid[action] {
		return nil, fmt.Errorf("action must be add_rule, remove_rule, or set_default_action, got %q", action)
	}
	return winfirewallExecuteOS(params)
}

func ConfigureWindowsFirewallRollback(params map[string]any) (map[string]any, error) {
	return winfirewallRollbackOS(params)
}

func HardenTLSProtocolsExecute(params map[string]any) (map[string]any, error) {
	return tlsExecuteOS(params)
}

func HardenTLSProtocolsRollback(params map[string]any) (map[string]any, error) {
	return tlsRollbackOS(params)
}

func HardenRDPExecute(params map[string]any) (map[string]any, error) {
	return rdpExecuteOS(params)
}

func HardenRDPRollback(params map[string]any) (map[string]any, error) {
	return rdpRollbackOS(params)
}

func ConfigureWindowsAuditPolicyExecute(params map[string]any) (map[string]any, error) {
	if profile, ok := params["profile"].(string); ok && profile != "" {
		valid := map[string]bool{"cis_level1": true, "cis_level2": true, "stig": true, "custom": true}
		if !valid[profile] {
			return nil, fmt.Errorf("profile must be cis_level1, cis_level2, stig, or custom, got %q", profile)
		}
	}
	return auditpolExecuteOS(params)
}

func ConfigureWindowsAuditPolicyRollback(params map[string]any) (map[string]any, error) {
	return auditpolRollbackOS(params)
}

func AuditScheduledTasksExecute(params map[string]any) (map[string]any, error) {
	return auditTasksOS(params)
}

func HardenRegistryExecute(params map[string]any) (map[string]any, error) {
	return registryExecuteOS(params)
}

func HardenRegistryRollback(params map[string]any) (map[string]any, error) {
	return registryRollbackOS(params)
}

func WDACauditExecute(params map[string]any) (map[string]any, error) {
	return wdacAuditExecuteOS(params)
}

func WDACenforceExecute(params map[string]any) (map[string]any, error) {
	return wdacEnforceExecuteOS(params)
}

func WDACrollback(params map[string]any) (map[string]any, error) {
	return wdacRollbackOS(params)
}

func ASRauditExecute(params map[string]any) (map[string]any, error)   { return asrAuditExecuteOS(params) }
func ASRenforceExecute(params map[string]any) (map[string]any, error) { return asrEnforceExecuteOS(params) }
func ASRrollback(params map[string]any) (map[string]any, error)       { return asrRollbackOS(params) }
