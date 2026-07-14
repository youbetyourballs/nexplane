// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

// Package reference implements agent subcommands for scanning and updating
// host file references to migrating resources.
package reference

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// UpdateExecute is the executor.CommandFunc entry point for the "reference-update" agent job.
// Params: file_path, old_value, new_value, backup_dir (optional).
func UpdateExecute(params map[string]any) (map[string]any, error) {
	filePath, _ := params["file"].(string)
	if filePath == "" {
		filePath, _ = params["file_path"].(string)
	}
	oldValue, _ := params["old-value"].(string)
	if oldValue == "" {
		oldValue, _ = params["old_value"].(string)
	}
	newValue, _ := params["new-value"].(string)
	if newValue == "" {
		newValue, _ = params["new_value"].(string)
	}
	backupDir, _ := params["backup-dir"].(string)
	if backupDir == "" {
		backupDir, _ = params["backup_dir"].(string)
	}
	if backupDir == "" {
		backupDir = "/tmp/nexplane-backups"
	}

	if filePath == "" {
		return nil, fmt.Errorf("file_path is required")
	}
	if oldValue == "" {
		return nil, fmt.Errorf("old_value is required")
	}

	// Read the file.
	content, err := os.ReadFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("read file: %w", err)
	}
	contentStr := string(content)

	// Check old_value exists.
	if !strings.Contains(contentStr, oldValue) {
		return map[string]any{
			"status": "skipped",
			"reason": "old_value not found in file",
		}, nil
	}

	// Write backup.
	if err := os.MkdirAll(backupDir, 0o750); err != nil {
		return nil, fmt.Errorf("create backup dir: %w", err)
	}
	ts := time.Now().UTC().Format("20060102T150405Z")
	backupName := fmt.Sprintf("%s-%s%s", strings.TrimSuffix(filepath.Base(filePath), filepath.Ext(filePath)), ts, filepath.Ext(filePath))
	backupPath := filepath.Join(backupDir, backupName)
	if err := os.WriteFile(backupPath, content, 0o640); err != nil {
		return nil, fmt.Errorf("write backup: %w", err)
	}

	// Replace and write.
	replaced := strings.ReplaceAll(contentStr, oldValue, newValue)
	replacements := strings.Count(contentStr, oldValue)

	// Preserve original permissions.
	info, err := os.Stat(filePath)
	var perm os.FileMode = 0o640
	if err == nil {
		perm = info.Mode().Perm()
	}
	if err := os.WriteFile(filePath, []byte(replaced), perm); err != nil {
		return nil, fmt.Errorf("write file: %w", err)
	}

	return map[string]any{
		"status":      "updated",
		"backup_path": backupPath,
		"replacements": replacements,
	}, nil
}

// RestoreExecute is the executor.CommandFunc entry point for the "reference-restore" agent job.
// Params: backup_path (or backup-path), target_path (or target-path).
func RestoreExecute(params map[string]any) (map[string]any, error) {
	backupPath, _ := params["backup-path"].(string)
	if backupPath == "" {
		backupPath, _ = params["backup_path"].(string)
	}
	targetPath, _ := params["target-path"].(string)
	if targetPath == "" {
		targetPath, _ = params["target_path"].(string)
	}
	// Also accept base64-encoded old_content (alternative rollback path).
	oldContentB64, _ := params["old_content"].(string)

	if targetPath == "" {
		return nil, fmt.Errorf("target_path is required")
	}

	var data []byte
	if oldContentB64 != "" {
		decoded, err := base64.StdEncoding.DecodeString(oldContentB64)
		if err != nil {
			return nil, fmt.Errorf("decode old_content: %w", err)
		}
		data = decoded
	} else {
		if backupPath == "" {
			return nil, fmt.Errorf("backup_path or old_content is required")
		}
		var err error
		data, err = os.ReadFile(backupPath)
		if err != nil {
			return nil, fmt.Errorf("read backup: %w", err)
		}
	}

	// Preserve original permissions if file exists.
	info, err := os.Stat(targetPath)
	var perm os.FileMode = 0o640
	if err == nil {
		perm = info.Mode().Perm()
	}

	if err := os.WriteFile(targetPath, data, perm); err != nil {
		return nil, fmt.Errorf("restore file: %w", err)
	}

	return map[string]any{
		"status": "restored",
		"target": targetPath,
	}, nil
}

// UpdateCmd and RestoreCmd are thin wrappers so register.go can call Run-style
// functions directly (mirrors the pattern used by other reference commands).

// runUpdate parses CLI-style args and calls UpdateExecute.
func runUpdate(args []string) error {
	params := parseFlags(args)
	result, err := UpdateExecute(params)
	if err != nil {
		return err
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	return enc.Encode(result)
}

// runRestore parses CLI-style args and calls RestoreExecute.
func runRestore(args []string) error {
	params := parseFlags(args)
	result, err := RestoreExecute(params)
	if err != nil {
		return err
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	return enc.Encode(result)
}

// parseFlags turns ["--key", "value", ...] into a map.
func parseFlags(args []string) map[string]any {
	m := map[string]any{}
	for i := 0; i+1 < len(args); i++ {
		key := strings.TrimLeft(args[i], "-")
		m[key] = args[i+1]
		i++
	}
	return m
}

// RunUpdate is exported so register.go can call it.
func RunUpdate(args []string) error { return runUpdate(args) }

// RunRestore is exported so register.go can call it.
func RunRestore(args []string) error { return runRestore(args) }
