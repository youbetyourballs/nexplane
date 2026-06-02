//go:build darwin

package ebpf

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"
)

const (
	pfAnchorName = "nexplane_net"
	pfAnchorFile = "/etc/pf.anchors/nexplane_net"
	pfConfFile   = "/etc/pf.conf"
)

func ConfigureEbpfNetworkExecute(params map[string]any) (map[string]any, error) {
	// Snapshot current pf state
	snapshotCmd := exec.Command("pfctl", "-s", "all")
	snapshotOut, err := snapshotCmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("pfctl -s all failed: %w: %s", err, snapshotOut)
	}
	snapshot := string(snapshotOut)

	// Snapshot /etc/pf.conf if it exists
	var pfConfContents string
	pfConfData, err := os.ReadFile(pfConfFile)
	if err == nil {
		pfConfContents = string(pfConfData)
	}

	// Create anchor file with initial LOG rules
	anchorContent := "# Nexplane network policy anchor - audit mode\npass log all\n"
	if err := os.WriteFile(pfAnchorFile, []byte(anchorContent), 0644); err != nil {
		return nil, fmt.Errorf("failed to write anchor file %s: %w", pfAnchorFile, err)
	}

	// Ensure /etc/pf.conf references the anchor
	if !strings.Contains(pfConfContents, `anchor "nexplane_net"`) {
		appendContent := "\nanchor \"nexplane_net\"\nload anchor \"nexplane_net\" from \"/etc/pf.anchors/nexplane_net\"\n"
		f, err := os.OpenFile(pfConfFile, os.O_APPEND|os.O_WRONLY|os.O_CREATE, 0644)
		if err != nil {
			return nil, fmt.Errorf("failed to open %s for append: %w", pfConfFile, err)
		}
		_, writeErr := f.WriteString(appendContent)
		closeErr := f.Close()
		if writeErr != nil {
			return nil, fmt.Errorf("failed to append anchor to %s: %w", pfConfFile, writeErr)
		}
		if closeErr != nil {
			return nil, fmt.Errorf("failed to close %s: %w", pfConfFile, closeErr)
		}
	}

	// Enable pf if not already enabled
	enableOut, err := exec.Command("pfctl", "-e").CombinedOutput()
	if err != nil && !strings.Contains(string(enableOut), "already enabled") {
		return nil, fmt.Errorf("pfctl -e failed: %w: %s", err, enableOut)
	}

	// Reload pf rules
	reloadOut, err := exec.Command("pfctl", "-f", pfConfFile).CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("pfctl -f %s failed: %w: %s", pfConfFile, err, reloadOut)
	}

	return map[string]any{
		"snapshot":       snapshot,
		"pf_conf_backup": pfConfContents,
		"configured_at":  time.Now().UTC().Format(time.RFC3339),
		"anchor":         pfAnchorName,
	}, nil
}

func ConfigureEbpfNetworkRollback(params map[string]any) (map[string]any, error) {
	backup, _ := params["pf_conf_backup"].(string)

	if backup != "" {
		// Restore original pf.conf from backup
		if err := os.WriteFile(pfConfFile, []byte(backup), 0644); err != nil {
			return nil, fmt.Errorf("failed to restore %s: %w", pfConfFile, err)
		}
	} else {
		// Remove the anchor lines we added
		data, err := os.ReadFile(pfConfFile)
		if err == nil {
			lines := strings.Split(string(data), "\n")
			filtered := make([]string, 0, len(lines))
			for _, line := range lines {
				if strings.Contains(line, `anchor "nexplane_net"`) ||
					strings.Contains(line, `load anchor "nexplane_net"`) {
					continue
				}
				filtered = append(filtered, line)
			}
			os.WriteFile(pfConfFile, []byte(strings.Join(filtered, "\n")), 0644) //nolint:errcheck
		}
	}

	// Remove anchor file
	os.Remove(pfAnchorFile) //nolint:errcheck

	// Reload pf rules
	reloadOut, err := exec.Command("pfctl", "-f", pfConfFile).CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("pfctl -f %s failed during rollback: %w: %s", pfConfFile, err, reloadOut)
	}

	return map[string]any{"rolled_back": true}, nil
}
