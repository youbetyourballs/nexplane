package linuxpatch

import "fmt"

var validModes = map[string]bool{
	"security_only": true,
	"package":       true,
	"cve":           true,
}

// ApplyLinuxPatchesExecute applies security patches on the host.
func ApplyLinuxPatchesExecute(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	if !validModes[mode] {
		return nil, fmt.Errorf("mode must be security_only, package, or cve; got %q", mode)
	}
	if mode == "package" {
		if pkg, _ := params["package_name"].(string); pkg == "" {
			return nil, fmt.Errorf("package_name is required when mode=package")
		}
	}
	if mode == "cve" {
		if cve, _ := params["cve_id"].(string); cve == "" {
			return nil, fmt.Errorf("cve_id is required when mode=cve")
		}
	}
	return applyPatchesOS(params)
}

// ApplyLinuxPatchesRollback downgrades packages to the versions captured in the execute result.
func ApplyLinuxPatchesRollback(params map[string]any) (map[string]any, error) {
	return rollbackPatchesOS(params)
}

// AuditLinuxPatchStatusExecute returns the current patch state without changes.
func AuditLinuxPatchStatusExecute(params map[string]any) (map[string]any, error) {
	return auditPatchStatusOS(params)
}
