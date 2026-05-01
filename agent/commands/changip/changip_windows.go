//go:build windows

package changip

import (
	"fmt"
	"os/exec"
	"strings"
	"time"
)

func executeOS(params map[string]any) (map[string]any, error) {
	iface, _ := params["interface"].(string)
	mode, _ := params["mode"].(string)
	ipVersion, _ := params["ip_version"].(string)

	if iface == "" {
		return nil, fmt.Errorf("interface is required")
	}
	if mode != "static" && mode != "dhcp" {
		return nil, fmt.Errorf("mode must be 'static' or 'dhcp', got %q", mode)
	}
	if ipVersion == "" {
		ipVersion = "4"
	}

	snapshot, err := captureSnapshotW(iface)
	if err != nil {
		return nil, fmt.Errorf("capturing snapshot: %w", err)
	}

	if err := applyIPChangeW(iface, mode, ipVersion, params); err != nil {
		return nil, err
	}

	return map[string]any{
		"action":     "change_ip",
		"interface":  iface,
		"mode":       mode,
		"applied":    true,
		"snapshot":   snapshot,
		"applied_at": time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	snapshot, ok := params["snapshot"].(map[string]any)
	if !ok || snapshot == nil {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	iface, _ := snapshot["interface"].(string)
	if iface == "" {
		return nil, fmt.Errorf("cannot determine interface for rollback")
	}
	snapshotParams := map[string]any{
		"interface":      iface,
		"mode":           snapshotField(snapshot, "ipv4", "mode", "dhcp"),
		"ip_version":     "both",
		"new_ip_v4":      snapshotField(snapshot, "ipv4", "address", ""),
		"new_ip_v6":      snapshotField(snapshot, "ipv6", "address", ""),
		"new_gateway_v4": snapshotField(snapshot, "ipv4", "gateway", ""),
		"new_gateway_v6": snapshotField(snapshot, "ipv6", "gateway", ""),
	}
	return map[string]any{"rolled_back": true}, applyIPChangeW(iface, snapshotParams["mode"].(string), "both", snapshotParams)
}

func captureSnapshotW(iface string) (map[string]any, error) {
	out, _ := exec.Command("netsh", "interface", "ip", "show", "addresses", iface).Output()
	return map[string]any{
		"interface": iface,
		"ip_output": strings.TrimSpace(string(out)),
	}, nil
}

func applyIPChangeW(iface, mode, ipVersion string, params map[string]any) error {
	if mode == "dhcp" {
		if ipVersion == "4" || ipVersion == "both" {
			if err := runCmdW("netsh", "interface", "ipv4", "set", "address", "name="+iface, "dhcp"); err != nil {
				return err
			}
		}
		if ipVersion == "6" || ipVersion == "both" {
			runCmdW("netsh", "interface", "ipv6", "set", "address", "interface="+iface, "dhcp")
		}
		return nil
	}
	if v4, ok := params["new_ip_v4"].(string); ok && v4 != "" {
		parts := strings.SplitN(v4, "/", 2)
		ip := parts[0]
		mask := cidrToMask(parts)
		gw, _ := params["new_gateway_v4"].(string)
		args := []string{"interface", "ipv4", "set", "address", "name=" + iface, "static", ip, mask}
		if gw != "" {
			args = append(args, gw)
		}
		if err := runCmdW("netsh", args...); err != nil {
			return err
		}
	}
	if v6, ok := params["new_ip_v6"].(string); ok && v6 != "" {
		if err := runCmdW("netsh", "interface", "ipv6", "add", "address", "interface="+iface, "address="+v6); err != nil {
			return err
		}
		if gw, ok := params["new_gateway_v6"].(string); ok && gw != "" {
			runCmdW("netsh", "interface", "ipv6", "add", "route", "::/0", "interface="+iface, "nexthop="+gw)
		}
	}
	return nil
}

func runCmdW(name string, args ...string) error {
	out, err := exec.Command(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %v: %w (output: %s)", name, args, err, out)
	}
	return nil
}

func cidrToMask(parts []string) string {
	masks := map[string]string{
		"8": "255.0.0.0", "16": "255.255.0.0", "24": "255.255.255.0",
		"25": "255.255.255.128", "26": "255.255.255.192", "27": "255.255.255.224",
		"28": "255.255.255.240", "29": "255.255.255.248", "30": "255.255.255.252",
		"32": "255.255.255.255",
	}
	if len(parts) > 1 {
		if m, ok := masks[parts[1]]; ok {
			return m
		}
	}
	return "255.255.255.0"
}

func snapshotField(snapshot map[string]any, section, field, def string) string {
	if s, ok := snapshot[section].(map[string]any); ok {
		if v, ok := s[field].(string); ok && v != "" {
			return v
		}
	}
	return def
}
