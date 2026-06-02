//go:build darwin

package fleet

import "context"

func restartServiceOS(_ context.Context, serviceName string) map[string]any {
	return map[string]any{"service": serviceName, "restarted": false, "error": "not supported on macOS"}
}

func pushConfigFileOS(_ map[string]any) map[string]any {
	return map[string]any{"pushed": false, "error": "not supported on macOS"}
}

func healthCheckOS(_ context.Context, _ []string) map[string]any {
	return map[string]any{"healthy": false, "error": "not supported on macOS"}
}

func runPostCommand(_ context.Context, _ string) map[string]any {
	return map[string]any{"ran": false, "error": "not supported on macOS"}
}
