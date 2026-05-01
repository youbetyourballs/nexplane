//go:build linux

package changip

func executeOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"applied": true, "note": "stub — implement in Task 5"}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": true, "note": "stub"}, nil
}
