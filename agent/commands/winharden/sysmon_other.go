//go:build !windows

package winharden

func sysmonDeployExecuteOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Sysmon is Windows-only"}, nil
}

func sysmonFIMExecuteOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Sysmon FIM is Windows-only"}, nil
}

func sysmonRollbackOS(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Sysmon is Windows-only"}, nil
}
