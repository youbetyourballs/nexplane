package linuxauth

import "fmt"

var validPAMProfiles = map[string]bool{
	"cis_level1": true,
	"cis_level2": true,
	"custom":     true,
}

func ConfigurePAMExecute(params map[string]any) (map[string]any, error) {
	if profile, ok := params["profile"].(string); ok && profile != "" {
		if !validPAMProfiles[profile] {
			return nil, fmt.Errorf("invalid profile %q: must be cis_level1, cis_level2, or custom", profile)
		}
	}
	return pamExecuteOS(params)
}

func ConfigurePAMRollback(params map[string]any) (map[string]any, error) {
	return pamRollbackOS(params)
}

func HardenSSHExecute(params map[string]any) (map[string]any, error) {
	return sshExecuteOS(params)
}

func HardenSSHRollback(params map[string]any) (map[string]any, error) {
	return sshRollbackOS(params)
}

func AuditUsersAndGroupsExecute(params map[string]any) (map[string]any, error) {
	return auditUsersOS(params)
}

func AuditPrivescVulnerabilitiesExecute(params map[string]any) (map[string]any, error) {
	return auditPrivescOS(params)
}

func ManageCACertificatesExecute(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if action != "install" && action != "remove" {
		return nil, fmt.Errorf("action must be 'install' or 'remove', got %q", action)
	}
	certName, _ := params["cert_name"].(string)
	if certName == "" {
		return nil, fmt.Errorf("cert_name is required")
	}
	if action == "install" {
		if cert, _ := params["certificate"].(string); cert == "" {
			return nil, fmt.Errorf("certificate PEM is required for install")
		}
	}
	return certsExecuteOS(params)
}

func ManageCACertificatesRollback(params map[string]any) (map[string]any, error) {
	return certsRollbackOS(params)
}

func ConfigureNTPExecute(params map[string]any) (map[string]any, error) {
	servers, _ := params["servers"].([]any)
	if len(servers) == 0 {
		return nil, fmt.Errorf("servers list is required and must not be empty")
	}
	return ntpExecuteOS(params)
}

func ConfigureNTPRollback(params map[string]any) (map[string]any, error) {
	return ntpRollbackOS(params)
}
