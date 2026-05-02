//go:build linux

package ossecurity

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

var cisLevel1Sysctl = map[string]string{
	"net.ipv4.ip_forward":                       "0",
	"net.ipv4.conf.all.send_redirects":           "0",
	"net.ipv4.conf.default.send_redirects":       "0",
	"net.ipv4.conf.all.accept_redirects":         "0",
	"net.ipv4.conf.default.accept_redirects":     "0",
	"net.ipv4.conf.all.rp_filter":                "1",
	"net.ipv4.conf.default.rp_filter":            "1",
	"net.ipv4.tcp_syncookies":                    "1",
	"net.ipv4.icmp_echo_ignore_broadcasts":       "1",
	"net.ipv4.icmp_ignore_bogus_error_responses": "1",
	"net.ipv4.tcp_timestamps":                    "0",
	"kernel.randomize_va_space":                  "2",
	"kernel.dmesg_restrict":                      "1",
	"fs.suid_dumpable":                           "0",
}

const sysctlDropIn = "/etc/sysctl.d/99-nexplane-hardening.conf"

func sysctlExecuteOS(params map[string]any) (map[string]any, error) {
	if os.Getuid() != 0 {
		return nil, fmt.Errorf("apply_sysctl_hardening requires root privileges")
	}

	settings := make(map[string]string)
	for k, v := range cisLevel1Sysctl {
		settings[k] = v
	}
	if overrides, ok := params["settings"].(map[string]any); ok {
		for k, v := range overrides {
			settings[k] = fmt.Sprintf("%v", v)
		}
	}

	snapshot := ""
	if data, err := os.ReadFile(sysctlDropIn); err == nil {
		snapshot = string(data)
	}

	var sb strings.Builder
	sb.WriteString("# Nexplane sysctl hardening — do not edit manually\n")
	for k, v := range settings {
		fmt.Fprintf(&sb, "%s = %s\n", k, v)
	}
	if err := os.WriteFile(sysctlDropIn, []byte(sb.String()), 0644); err != nil {
		return nil, fmt.Errorf("writing sysctl drop-in: %w", err)
	}

	if out, err := exec.Command("sysctl", "--system").CombinedOutput(); err != nil {
		return nil, fmt.Errorf("sysctl --system failed: %s", out)
	}

	return map[string]any{
		"settings_applied": settings,
		"drop_in_path":     sysctlDropIn,
		"snapshot":         snapshot,
		"applied_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func sysctlRollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(string)
	if !ok {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	if snapshot == "" {
		if err := os.Remove(sysctlDropIn); err != nil && !os.IsNotExist(err) {
			return nil, fmt.Errorf("removing sysctl drop-in: %w", err)
		}
	} else {
		if err := os.WriteFile(sysctlDropIn, []byte(snapshot), 0644); err != nil {
			return nil, fmt.Errorf("restoring sysctl drop-in: %w", err)
		}
	}
	exec.Command("sysctl", "--system").Run() //nolint:errcheck
	return map[string]any{"rolled_back": true, "drop_in_path": sysctlDropIn}, nil
}

// insertManagedBlock inserts or replaces a nexplane-managed block in config file content.
func insertManagedBlock(content, block string) string {
	const beg = "# nexplane-managed-begin\n"
	const end = "# nexplane-managed-end"
	si := strings.Index(content, beg)
	ei := strings.Index(content, end)
	if si != -1 && ei != -1 {
		return content[:si] + beg + block + end + content[ei+len(end):]
	}
	if !strings.HasSuffix(content, "\n") {
		content += "\n"
	}
	return content + beg + block + end + "\n"
}
