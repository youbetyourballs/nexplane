//go:build linux

package virtualize

func executeOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"virtualized": true, "note": "stub — implement in Task 7"}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": true, "note": "stub"}, nil
}
