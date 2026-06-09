//go:build darwin

package ossecurity

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

var execCommandMount = exec.Command

var sipProtectedPaths = []string{"/System", "/usr", "/bin", "/sbin", "/private/var/db"}

func mountExecuteOS(params map[string]any) (map[string]any, error) {
	path, _ := params["path"].(string)
	if path == "" {
		return nil, fmt.Errorf("path is required")
	}
	optionsRaw, _ := params["options"].([]any)
	options := make([]string, 0, len(optionsRaw))
	for _, o := range optionsRaw {
		if s, ok := o.(string); ok {
			options = append(options, s)
		}
	}

	for _, protected := range sipProtectedPaths {
		if strings.HasPrefix(path, protected) {
			return map[string]any{
				"skipped": true,
				"reason":  fmt.Sprintf("SIP protects %q; remounting is not possible without disabling SIP", path),
				"path":    path,
			}, nil
		}
	}

	snapOut, _ := execCommandMount("mount").Output()
	snapshot := ""
	for _, line := range strings.Split(string(snapOut), "\n") {
		if strings.Contains(line, path+" ") || strings.HasSuffix(line, " "+path) {
			snapshot = strings.TrimSpace(line)
		}
	}

	args := []string{"-u", "-o", strings.Join(options, ","), path}
	if out, err := execCommandMount("mount", args...).CombinedOutput(); err != nil {
		return nil, fmt.Errorf("mount -u -o: %s: %w", out, err)
	}

	return map[string]any{
		"path":       path,
		"options":    options,
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func mountRollbackOS(params map[string]any) (map[string]any, error) {
	path, _ := params["path"].(string)
	snapshot, _ := params["snapshot"].(string)
	if path == "" {
		return nil, fmt.Errorf("path is required for rollback")
	}
	if snapshot == "" {
		return map[string]any{"rolled_back": false, "reason": "no snapshot to restore"}, nil
	}
	origOpts := ""
	if start := strings.Index(snapshot, "("); start != -1 {
		if end := strings.Index(snapshot, ")"); end != -1 {
			origOpts = snapshot[start+1 : end]
		}
	}
	if origOpts != "" {
		execCommandMount("mount", "-u", "-o", origOpts, path).Run()
	}
	return map[string]any{"rolled_back": true}, nil
}
