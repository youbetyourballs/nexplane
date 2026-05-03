package credrotation

import (
	"context"
	"fmt"
	"os"
	"time"
)

// APIKeyEnvParams holds inputs for updating an API key in an agent-side env file.
type APIKeyEnvParams struct {
	Action     string // "update" | "restore"
	FilePath   string
	EnvVarName string
	NewAPIKey  string // injected via consumes
	BackupPath string // injected via consumes for rollback
}

// Execute backs up the env file then replaces the named env var's value.
func (p APIKeyEnvParams) Execute(ctx context.Context) (map[string]string, error) {
	switch p.Action {
	case "update":
		return updateEnvFile(p.FilePath, p.EnvVarName, p.NewAPIKey)
	case "restore":
		return nil, restoreEnvFile(p.FilePath, p.BackupPath)
	default:
		return nil, fmt.Errorf("unknown api key env action: %q", p.Action)
	}
}

// APIKeyEnvExecute is the CommandFunc-compatible entry point for executor.go.
func APIKeyEnvExecute(params map[string]any) (map[string]any, error) {
	p := apiKeyEnvParamsFromMap(params)
	out, err := p.Execute(context.Background())
	if err != nil {
		return nil, err
	}
	result := make(map[string]any, len(out))
	for k, v := range out {
		result[k] = v
	}
	return result, nil
}

// APIKeyEnvRollback is the rollback CommandFunc-compatible entry point.
func APIKeyEnvRollback(params map[string]any) (map[string]any, error) {
	return APIKeyEnvExecute(params)
}

func apiKeyEnvParamsFromMap(params map[string]any) APIKeyEnvParams {
	return APIKeyEnvParams{
		Action:     strParam(params, "action"),
		FilePath:   strParam(params, "file_path"),
		EnvVarName: strParam(params, "env_var_name"),
		NewAPIKey:  strParam(params, "new_api_key"),
		BackupPath: strParam(params, "env_file_backup_path"),
	}
}

func updateEnvFile(filePath, envVarName, newAPIKey string) (map[string]string, error) {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", filePath, err)
	}

	// Backup before mutating
	dest := fmt.Sprintf("%s.nexplane-bak-%s", filePath, time.Now().Format("20060102T150405"))
	if err := os.WriteFile(dest, data, 0600); err != nil {
		return nil, fmt.Errorf("write backup: %w", err)
	}

	updated := ReplaceEnvVarInContent(string(data), envVarName, newAPIKey)
	if err := os.WriteFile(filePath, []byte(updated), 0600); err != nil {
		return nil, fmt.Errorf("write %s: %w", filePath, err)
	}

	return map[string]string{"env_file_backup_path": dest}, nil
}

func restoreEnvFile(filePath, backupPath string) error {
	data, err := os.ReadFile(backupPath)
	if err != nil {
		return fmt.Errorf("read backup %s: %w", backupPath, err)
	}
	return os.WriteFile(filePath, data, 0600)
}
