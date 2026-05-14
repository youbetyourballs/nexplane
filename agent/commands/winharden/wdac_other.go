//go:build !windows

package winharden

func wdacAuditExecuteOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"error": "WDAC is Windows-only"}, nil
}

func wdacEnforceExecuteOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"error": "WDAC is Windows-only"}, nil
}

func wdacRollbackOS(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "WDAC is Windows-only"}, nil
}
