package credrotation

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"os"
	"strings"
	"time"
)

// DBRotateParams holds all inputs resolved from the change step at dispatch time.
type DBRotateParams struct {
	Action            string   // "generate" | "update_db_user" | "backup_config" | "update_config" | "restart" | "restore_config"
	DBHost            string
	DBPort            int
	DBEngine          string // "postgres" | "mysql"
	DBUsername        string
	ConfigPaths       []string
	ServiceName       string
	HealthCheckURL    string
	NewPassword       string   // injected via consumes
	ConfigBackupPaths []string // injected via consumes for rollback
}

// Execute dispatches to the correct sub-action.
func (p DBRotateParams) Execute(ctx context.Context) (map[string]string, error) {
	switch p.Action {
	case "generate":
		return generatePassword()
	case "update_db_user":
		return nil, updateDBUserPassword(ctx, p)
	case "backup_config":
		return backupConfigFiles(p.ConfigPaths)
	case "update_config":
		return nil, updateConfigFiles(p.ConfigPaths, p.NewPassword, p.DBUsername)
	case "restart":
		return nil, restartService(ctx, p.ServiceName)
	case "restore_config":
		return nil, restoreConfigFiles(p.ConfigBackupPaths)
	default:
		return nil, fmt.Errorf("unknown db rotation action: %q", p.Action)
	}
}

// DBRotateExecute is the CommandFunc-compatible entry point for executor.go.
func DBRotateExecute(params map[string]any) (map[string]any, error) {
	p := dbParamsFromMap(params)
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

// DBRotateRollback is the rollback CommandFunc-compatible entry point.
func DBRotateRollback(params map[string]any) (map[string]any, error) {
	// For rollback, action is set by the rollback_command/rollback_params in the step
	return DBRotateExecute(params)
}

func dbParamsFromMap(params map[string]any) DBRotateParams {
	configPaths, _ := params["config_paths"].([]string)
	if raw, ok := params["config_paths"].([]any); ok && configPaths == nil {
		for _, v := range raw {
			if s, ok := v.(string); ok {
				configPaths = append(configPaths, s)
			}
		}
	}
	configBackupPaths, _ := params["config_backup_paths"].([]string)
	if raw, ok := params["config_backup_paths"].([]any); ok && configBackupPaths == nil {
		for _, v := range raw {
			if s, ok := v.(string); ok {
				configBackupPaths = append(configBackupPaths, s)
			}
		}
	}
	// config_backup_paths may arrive as comma-joined string from StepOutputs
	if joined, ok := params["config_backup_paths"].(string); ok && len(configBackupPaths) == 0 {
		configBackupPaths = strings.Split(joined, ",")
	}

	port := 5432
	if p, ok := params["db_port"].(int); ok {
		port = p
	} else if p, ok := params["db_port"].(float64); ok {
		port = int(p)
	}

	return DBRotateParams{
		Action:            strParam(params, "action"),
		DBHost:            strParam(params, "db_host"),
		DBPort:            port,
		DBEngine:          strParam(params, "db_engine"),
		DBUsername:        strParam(params, "db_username"),
		ConfigPaths:       configPaths,
		ServiceName:       strParam(params, "service_name"),
		HealthCheckURL:    strParam(params, "health_check_url"),
		NewPassword:       strParam(params, "new_password"),
		ConfigBackupPaths: configBackupPaths,
	}
}

func strParam(params map[string]any, key string) string {
	v, _ := params[key].(string)
	return v
}

// ── implementation ────────────────────────────────────────────────────────────

func generatePassword() (map[string]string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return nil, fmt.Errorf("generate password: %w", err)
	}
	pw := base64.URLEncoding.EncodeToString(b)[:40]
	return map[string]string{"new_password": pw}, nil
}

func backupConfigFiles(paths []string) (map[string]string, error) {
	backups := make([]string, 0, len(paths))
	for _, p := range paths {
		dest := fmt.Sprintf("%s.nexplane-bak-%s", p, time.Now().Format("20060102T150405"))
		data, err := os.ReadFile(p)
		if err != nil {
			return nil, fmt.Errorf("backup %s: %w", p, err)
		}
		if err := os.WriteFile(dest, data, 0600); err != nil {
			return nil, fmt.Errorf("write backup %s: %w", dest, err)
		}
		backups = append(backups, dest)
	}
	return map[string]string{"config_backup_paths": strings.Join(backups, ",")}, nil
}

func updateConfigFiles(paths []string, newPassword, username string) error {
	for _, p := range paths {
		data, err := os.ReadFile(p)
		if err != nil {
			return fmt.Errorf("read %s: %w", p, err)
		}
		updated := ReplaceCredentialInContent(string(data), username, newPassword)
		if err := os.WriteFile(p, []byte(updated), 0600); err != nil {
			return fmt.Errorf("write %s: %w", p, err)
		}
	}
	return nil
}

func restoreConfigFiles(backupPaths []string) error {
	for _, bak := range backupPaths {
		bak = strings.TrimSpace(bak)
		// Strip the ".nexplane-bak-YYYYMMDDTHHMMSS" suffix to get the original path
		idx := strings.LastIndex(bak, ".nexplane-bak-")
		if idx == -1 {
			return fmt.Errorf("backup path does not contain .nexplane-bak- marker: %s", bak)
		}
		original := bak[:idx]
		data, err := os.ReadFile(bak)
		if err != nil {
			return fmt.Errorf("read backup %s: %w", bak, err)
		}
		if err := os.WriteFile(original, data, 0600); err != nil {
			return fmt.Errorf("restore %s: %w", original, err)
		}
	}
	return nil
}
