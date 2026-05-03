package fleet

import (
	"context"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
)

// RestartServiceExecute is the command entry point registered in executor.go.
func RestartServiceExecute(params map[string]any) (map[string]any, error) {
	return RestartServiceExecuteCtx(context.Background(), params), nil
}

// RestartServiceExecuteCtx is context-aware and used in tests.
func RestartServiceExecuteCtx(ctx context.Context, params map[string]any) map[string]any {
	svc, _ := params["service_name"].(string)
	if svc == "" {
		return map[string]any{"running": false, "error": "service_name is required"}
	}
	return restartServiceOS(ctx, svc)
}

// PushConfigFileExecute is the command entry point registered in executor.go.
func PushConfigFileExecute(params map[string]any) (map[string]any, error) {
	return pushConfigFileOS(params), nil
}

// DistributeFileExecute is the command entry point registered in executor.go.
func DistributeFileExecute(params map[string]any) (map[string]any, error) {
	filePath, _ := params["file_path"].(string)
	fileContent, _ := params["file_content"].(string)
	permissions, _ := params["permissions"].(string)
	postCommand, _ := params["post_command"].(string)

	content, err := base64.StdEncoding.DecodeString(fileContent)
	if err != nil {
		return map[string]any{"error": fmt.Sprintf("base64 decode: %v", err)}, nil
	}
	if err := os.MkdirAll(filepath.Dir(filePath), 0755); err != nil {
		return map[string]any{"error": fmt.Sprintf("mkdir: %v", err)}, nil
	}

	perm := os.FileMode(0644)
	if permissions != "" {
		var v uint64
		fmt.Sscanf(permissions, "%o", &v)
		if v > 0 {
			perm = os.FileMode(v)
		}
	}
	if err := os.WriteFile(filePath, content, perm); err != nil {
		return map[string]any{"error": fmt.Sprintf("write: %v", err)}, nil
	}

	if postCommand != "" {
		return runPostCommand(context.Background(), postCommand), nil
	}
	return map[string]any{}, nil
}

// HealthCheckExecute is the command entry point registered in executor.go.
func HealthCheckExecute(params map[string]any) (map[string]any, error) {
	var requiredServices []string
	if raw, ok := params["required_services"].([]any); ok {
		for _, r := range raw {
			if s, ok := r.(string); ok {
				requiredServices = append(requiredServices, s)
			}
		}
	}
	return healthCheckOS(context.Background(), requiredServices), nil
}

// writeFileAtomically decodes base64 content and writes it atomically.
func writeFileAtomically(path, b64content string, perm os.FileMode) error {
	content, err := base64.StdEncoding.DecodeString(b64content)
	if err != nil {
		return fmt.Errorf("base64 decode: %w", err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return fmt.Errorf("mkdir: %w", err)
	}
	tmp := path + ".nx_tmp"
	if err := os.WriteFile(tmp, content, perm); err != nil {
		return fmt.Errorf("write tmp: %w", err)
	}
	return os.Rename(tmp, path)
}
