//go:build windows

package configsyslog

func executeOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"configured": true, "note": "stub"}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": true, "note": "stub"}, nil
}
