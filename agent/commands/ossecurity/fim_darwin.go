//go:build darwin

package ossecurity

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"
)

var darwinFIMSnapshotPath = "/var/lib/nexplane/fim-snapshot.json"

func fimExecuteOS(params map[string]any) (map[string]any, error) {
	action, _ := params["action"].(string)
	if action == "" {
		action = "init"
	}

	watchPathsRaw, _ := params["watch_paths"].([]any)
	watchPaths := []string{"/etc", "/usr/local/bin", "/Library/LaunchDaemons"}
	if len(watchPathsRaw) > 0 {
		watchPaths = nil
		for _, p := range watchPathsRaw {
			if s, ok := p.(string); ok {
				watchPaths = append(watchPaths, s)
			}
		}
	}

	switch action {
	case "init":
		return darwinFIMInit(watchPaths)
	case "check":
		return darwinFIMCheck(watchPaths)
	default:
		return nil, fmt.Errorf("unknown action %q: must be init or check", action)
	}
}

func darwinFIMInit(watchPaths []string) (map[string]any, error) {
	snapshot := map[string]string{}
	for _, root := range watchPaths {
		filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
			if err != nil || info == nil || info.IsDir() {
				return nil
			}
			if h, err := sha256File(path); err == nil {
				snapshot[path] = h
			}
			return nil
		})
	}

	os.MkdirAll(filepath.Dir(darwinFIMSnapshotPath), 0755)

	data, err := json.Marshal(snapshot)
	if err != nil {
		return nil, fmt.Errorf("marshaling snapshot: %w", err)
	}
	if err := os.WriteFile(darwinFIMSnapshotPath, data, 0600); err != nil {
		return nil, fmt.Errorf("writing snapshot: %w", err)
	}

	return map[string]any{
		"tool":          "sha256-walk",
		"action":        "init",
		"snapshot_path": darwinFIMSnapshotPath,
		"file_count":    len(snapshot),
		"applied_at":    time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func darwinFIMCheck(watchPaths []string) (map[string]any, error) {
	data, err := os.ReadFile(darwinFIMSnapshotPath)
	if err != nil {
		return nil, fmt.Errorf("reading snapshot (run init first): %w", err)
	}
	var baseline map[string]string
	if err := json.Unmarshal(data, &baseline); err != nil {
		return nil, fmt.Errorf("parsing snapshot: %w", err)
	}

	type violation struct {
		Path     string `json:"path"`
		Expected string `json:"expected"`
		Actual   string `json:"actual"`
	}
	var violations []violation

	current := map[string]string{}
	for _, root := range watchPaths {
		filepath.Walk(root, func(path string, info os.FileInfo, err error) error {
			if err != nil || info == nil || info.IsDir() {
				return nil
			}
			if h, err := sha256File(path); err == nil {
				current[path] = h
			}
			return nil
		})
	}

	for path, expected := range baseline {
		actual, exists := current[path]
		if !exists {
			violations = append(violations, violation{path, expected, "DELETED"})
		} else if actual != expected {
			violations = append(violations, violation{path, expected, actual})
		}
	}
	for path := range current {
		if _, exists := baseline[path]; !exists {
			violations = append(violations, violation{path, "NOT_IN_BASELINE", current[path]})
		}
	}

	return map[string]any{
		"tool":       "sha256-walk",
		"action":     "check",
		"violations": len(violations) > 0,
		"changes":    violations,
		"checked_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func fimRollbackOS(_ map[string]any) (map[string]any, error) {
	return map[string]any{
		"rolled_back": false,
		"reason":      "file integrity monitoring is read-only; no state to restore",
	}, nil
}

func sha256File(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
