//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const moduleBlacklist = "/etc/modprobe.d/nexplane-blacklist.conf"

func blacklistExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("blacklist_kernel_modules requires root privileges")
	}
	modulesRaw, _ := params["modules"].([]any)
	modules := make([]string, 0, len(modulesRaw))
	for _, m := range modulesRaw {
		if s, ok := m.(string); ok {
			modules = append(modules, s)
		}
	}

	snapshot := ""
	if data, err := os.ReadFile(moduleBlacklist); err == nil {
		snapshot = string(data)
	}

	var sb strings.Builder
	sb.WriteString("# Nexplane kernel module blacklist — do not edit manually\n")
	for _, m := range modules {
		fmt.Fprintf(&sb, "blacklist %s\ninstall %s /bin/true\n", m, m)
	}
	if err := os.WriteFile(moduleBlacklist, []byte(sb.String()), 0644); err != nil {
		return nil, fmt.Errorf("writing blacklist: %w", err)
	}

	exec.Command("update-initramfs", "-u").Run() //nolint:errcheck

	return map[string]any{
		"modules_blacklisted": modules,
		"blacklist_path":      moduleBlacklist,
		"snapshot":            snapshot,
		"applied_at":          time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func blacklistRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	if snapshot == "" {
		if err := os.Remove(moduleBlacklist); err != nil && !os.IsNotExist(err) {
			return nil, fmt.Errorf("removing blacklist: %w", err)
		}
	} else {
		if err := os.WriteFile(moduleBlacklist, []byte(snapshot), 0644); err != nil {
			return nil, fmt.Errorf("restoring blacklist: %w", err)
		}
	}
	exec.Command("update-initramfs", "-u").Run() //nolint:errcheck
	return map[string]any{"rolled_back": true}, nil
}
