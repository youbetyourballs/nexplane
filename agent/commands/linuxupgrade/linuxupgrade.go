package linuxupgrade

import "fmt"

var validPaths = map[string]bool{"inplace": true, "containerize": true}
var validUpgradeTypes = map[string]bool{"security": true, "packages": true, "dist": true}
var validSnapshotMethods = map[string]bool{"cloud": true, "dd": true}

func UpgradeLinuxInstanceExecute(params map[string]any) (map[string]any, error) {
	path, _ := params["path"].(string)
	if !validPaths[path] {
		return nil, fmt.Errorf("path must be inplace or containerize, got %q", path)
	}
	if upgradeType, ok := params["upgrade_type"].(string); ok && upgradeType != "" && !validUpgradeTypes[upgradeType] {
		return nil, fmt.Errorf("upgrade_type must be security, packages, or dist, got %q", upgradeType)
	}
	if snapshotMethod, ok := params["snapshot_method"].(string); ok && snapshotMethod != "" {
		if !validSnapshotMethods[snapshotMethod] {
			return nil, fmt.Errorf("snapshot_method must be cloud or dd, got %q", snapshotMethod)
		}
		if snapshotMethod == "dd" {
			if target, _ := params["snapshot_target_path"].(string); target == "" {
				return nil, fmt.Errorf("snapshot_target_path is required when snapshot_method=dd")
			}
		}
	}
	if path == "containerize" {
		if reg, _ := params["container_registry"].(string); reg == "" {
			return nil, fmt.Errorf("container_registry is required for path=containerize")
		}
		if target, _ := params["target_os"].(string); target == "" {
			return nil, fmt.Errorf("target_os is required for path=containerize")
		}
	}
	return upgradeExecuteOS(params)
}

func UpgradeLinuxInstanceRollback(params map[string]any) (map[string]any, error) {
	return upgradeRollbackOS(params)
}
