//go:build linux

package configsyslog

func executeOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"configured": true, "note": "stub — implement in Task 6"}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": true, "note": "stub"}, nil
}
