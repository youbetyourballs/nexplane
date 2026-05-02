package ossecurity

import "fmt"

var validSELinuxModes = map[string]bool{"enforcing": true, "permissive": true, "disabled": true}
var validAppArmorModes = map[string]bool{"enforce": true, "complain": true, "disable": true}
var validFirewallActions = map[string]bool{"add_rule": true, "remove_rule": true, "flush": true}

func ConfigureSELinuxExecute(params map[string]any) (map[string]any, error) {
	_, hasMode := params["mode"].(string)
	_, hasModule := params["policy_module_path"].(string)
	_, hasGenerate := params["generate_from_audit_log"].(bool)
	if !hasMode && !hasModule && !hasGenerate {
		return nil, fmt.Errorf("at least one of mode, policy_module_path, or generate_from_audit_log is required")
	}
	if mode, ok := params["mode"].(string); ok && mode != "" && !validSELinuxModes[mode] {
		return nil, fmt.Errorf("invalid mode %q: must be enforcing, permissive, or disabled", mode)
	}
	return selinuxExecuteOS(params)
}

func ConfigureSELinuxRollback(params map[string]any) (map[string]any, error) {
	return selinuxRollbackOS(params)
}

func ConfigureAppArmorExecute(params map[string]any) (map[string]any, error) {
	if mode, ok := params["mode"].(string); ok && mode != "" && !validAppArmorModes[mode] {
		return nil, fmt.Errorf("invalid mode %q: must be enforce, complain, or disable", mode)
	}
	return apparmorExecuteOS(params)
}

func ConfigureAppArmorRollback(params map[string]any) (map[string]any, error) {
	return apparmorRollbackOS(params)
}

func ConfigureSeccompExecute(params map[string]any) (map[string]any, error) {
	if svc, _ := params["service_name"].(string); svc == "" {
		return nil, fmt.Errorf("service_name is required")
	}
	if _, ok := params["profile"].(string); !ok {
		return nil, fmt.Errorf("profile (seccomp JSON) is required")
	}
	return seccompExecuteOS(params)
}

func ConfigureSeccompRollback(params map[string]any) (map[string]any, error) {
	return seccompRollbackOS(params)
}

func ApplySysctlHardeningExecute(params map[string]any) (map[string]any, error) {
	return sysctlExecuteOS(params)
}

func ApplySysctlHardeningRollback(params map[string]any) (map[string]any, error) {
	return sysctlRollbackOS(params)
}

func ConfigureHostFirewallExecute(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if !validFirewallActions[action] {
		return nil, fmt.Errorf("action must be add_rule, remove_rule, or flush, got %q", action)
	}
	return firewallExecuteOS(params)
}

func ConfigureHostFirewallRollback(params map[string]any) (map[string]any, error) {
	return firewallRollbackOS(params)
}

func BlacklistKernelModulesExecute(params map[string]any) (map[string]any, error) {
	modules, _ := params["modules"].([]any)
	if len(modules) == 0 {
		return nil, fmt.Errorf("modules list is required and must not be empty")
	}
	return blacklistExecuteOS(params)
}

func BlacklistKernelModulesRollback(params map[string]any) (map[string]any, error) {
	return blacklistRollbackOS(params)
}

func HardenMountOptionsExecute(params map[string]any) (map[string]any, error) {
	return mountExecuteOS(params)
}

func HardenMountOptionsRollback(params map[string]any) (map[string]any, error) {
	return mountRollbackOS(params)
}

func DeployAuditdRulesExecute(params map[string]any) (map[string]any, error) {
	profile, _ := params["profile"].(string)
	_, hasRules := params["rules"].(string)
	if profile == "" && !hasRules {
		return nil, fmt.Errorf("profile or rules is required")
	}
	return auditdExecuteOS(params)
}

func DeployAuditdRulesRollback(params map[string]any) (map[string]any, error) {
	return auditdRollbackOS(params)
}

func SetupFileIntegrityMonitoringExecute(params map[string]any) (map[string]any, error) {
	return fimExecuteOS(params)
}

func SetupFileIntegrityMonitoringRollback(params map[string]any) (map[string]any, error) {
	return fimRollbackOS(params)
}

func AuditOSSecurityPostureExecute(params map[string]any) (map[string]any, error) {
	return auditPostureOS(params)
}
