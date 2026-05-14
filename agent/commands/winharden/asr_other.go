//go:build !windows

package winharden

func asrAuditExecuteOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"error": "ASR is Windows-only"}, nil
}

func asrEnforceExecuteOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"error": "ASR is Windows-only"}, nil
}

func asrRollbackOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"error": "ASR is Windows-only"}, nil
}
