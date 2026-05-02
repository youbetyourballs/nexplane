//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

func mountExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("harden_mount_options requires root privileges")
	}

	fstabData, err := os.ReadFile("/etc/fstab")
	if err != nil {
		return nil, fmt.Errorf("reading /etc/fstab: %w", err)
	}
	snapshot := string(fstabData)

	targets := map[string][]string{
		"/tmp":     {"noexec", "nosuid", "nodev"},
		"/dev/shm": {"noexec", "nosuid", "nodev"},
		"/var/tmp": {"noexec", "nosuid", "nodev"},
	}
	if custom, ok := params["targets"].(map[string]any); ok {
		for mount, optsRaw := range custom {
			if opts, ok := optsRaw.([]any); ok {
				var optStrings []string
				for _, o := range opts {
					if s, ok := o.(string); ok {
						optStrings = append(optStrings, s)
					}
				}
				targets[mount] = optStrings
			}
		}
	}

	lines := strings.Split(snapshot, "\n")
	for i, line := range lines {
		if strings.HasPrefix(line, "#") || strings.TrimSpace(line) == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 4 {
			continue
		}
		mountPoint := fields[1]
		if requiredOpts, ok := targets[mountPoint]; ok {
			currentOpts := strings.Split(fields[3], ",")
			for _, req := range requiredOpts {
				found := false
				for _, cur := range currentOpts {
					if cur == req {
						found = true
						break
					}
				}
				if !found {
					currentOpts = append(currentOpts, req)
				}
			}
			fields[3] = strings.Join(currentOpts, ",")
			lines[i] = strings.Join(fields, "\t")
		}
	}

	newFstab := strings.Join(lines, "\n")
	if err := os.WriteFile("/etc/fstab", []byte(newFstab), 0644); err != nil {
		return nil, fmt.Errorf("writing /etc/fstab: %w", err)
	}
	for mount := range targets {
		exec.Command("mount", "-o", "remount", mount).Run() //nolint:errcheck
	}

	return map[string]any{
		"targets_hardened": targets,
		"snapshot":         snapshot,
		"applied_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func mountRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	if err := os.WriteFile("/etc/fstab", []byte(snapshot), 0644); err != nil {
		return nil, fmt.Errorf("restoring /etc/fstab: %w", err)
	}
	exec.Command("mount", "-a").Run() //nolint:errcheck
	return map[string]any{"rolled_back": true}, nil
}
