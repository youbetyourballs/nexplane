//go:build windows

package virtualize

func executeOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"virtualized": true, "note": "stub"}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	return map[string]any{"rolled_back": true, "note": "stub"}, nil
}
