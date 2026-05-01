//go:build linux

package changip

import (
	"fmt"
	"os"
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

	snapshot, err := captureSnapshot(iface)
	if err != nil {
		return nil, fmt.Errorf("capturing snapshot: %w", err)
	}

	method := detectNetworkManager()
	if err := applyIPChange(method, iface, mode, ipVersion, params); err != nil {
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
	iface, _ := params["interface"].(string)
	if iface == "" {
		iface, _ = snapshot["interface"].(string)
	}
	if iface == "" {
		return nil, fmt.Errorf("cannot determine interface for rollback")
	}

	method := detectNetworkManager()
	snapshotParams := map[string]any{
		"interface":      iface,
		"mode":           snapshotField(snapshot, "ipv4", "mode", "dhcp"),
		"ip_version":     "both",
		"new_ip_v4":      snapshotField(snapshot, "ipv4", "address", ""),
		"new_ip_v6":      snapshotField(snapshot, "ipv6", "address", ""),
		"new_gateway_v4": snapshotField(snapshot, "ipv4", "gateway", ""),
		"new_gateway_v6": snapshotField(snapshot, "ipv6", "gateway", ""),
	}
	return map[string]any{"rolled_back": true}, applyIPChange(method, iface, snapshotParams["mode"].(string), "both", snapshotParams)
}

type networkManager int

const (
	nmNetworkManager networkManager = iota
	nmSystemd
	nmDebian
	nmRHEL
)

func detectNetworkManager() networkManager {
	if _, err := exec.LookPath("nmcli"); err == nil {
		return nmNetworkManager
	}
	if _, err := os.Stat("/etc/systemd/network"); err == nil {
		return nmSystemd
	}
	if _, err := os.Stat("/etc/network/interfaces"); err == nil {
		return nmDebian
	}
	return nmRHEL
}

func captureSnapshot(iface string) (map[string]any, error) {
	out, _ := exec.Command("ip", "addr", "show", iface).Output()
	gwOut, _ := exec.Command("ip", "route", "show", "dev", iface).Output()
	return map[string]any{
		"interface": iface,
		"ip_output": strings.TrimSpace(string(out)),
		"gw_output": strings.TrimSpace(string(gwOut)),
	}, nil
}

func applyIPChange(method networkManager, iface, mode, ipVersion string, params map[string]any) error {
	switch method {
	case nmNetworkManager:
		return applyNmcli(iface, mode, ipVersion, params)
	case nmSystemd:
		return applySystemdNetworkd(iface, mode, ipVersion, params)
	case nmDebian:
		return applyDebianInterfaces(iface, mode, ipVersion, params)
	default:
		return applyRHELIfcfg(iface, mode, ipVersion, params)
	}
}

func applyNmcli(iface, mode, ipVersion string, params map[string]any) error {
	if mode == "dhcp" {
		if ipVersion == "4" || ipVersion == "both" {
			if err := runCmd("nmcli", "con", "mod", iface, "ipv4.method", "auto"); err != nil {
				return err
			}
		}
		if ipVersion == "6" || ipVersion == "both" {
			if err := runCmd("nmcli", "con", "mod", iface, "ipv6.method", "auto"); err != nil {
				return err
			}
		}
	} else {
		if v4, ok := params["new_ip_v4"].(string); ok && v4 != "" {
			args := []string{"con", "mod", iface, "ipv4.method", "manual", "ipv4.addresses", v4}
			if gw, ok := params["new_gateway_v4"].(string); ok && gw != "" {
				args = append(args, "ipv4.gateway", gw)
			}
			if err := runCmd("nmcli", args...); err != nil {
				return err
			}
		}
		if v6, ok := params["new_ip_v6"].(string); ok && v6 != "" {
			args := []string{"con", "mod", iface, "ipv6.method", "manual", "ipv6.addresses", v6}
			if gw, ok := params["new_gateway_v6"].(string); ok && gw != "" {
				args = append(args, "ipv6.gateway", gw)
			}
			if err := runCmd("nmcli", args...); err != nil {
				return err
			}
		}
	}
	return runCmd("nmcli", "con", "up", iface)
}

func applySystemdNetworkd(iface, mode, ipVersion string, params map[string]any) error {
	path := fmt.Sprintf("/etc/systemd/network/10-nexplane-%s.network", iface)
	content := fmt.Sprintf("[Match]\nName=%s\n\n[Network]\n", iface)
	if mode == "dhcp" {
		if ipVersion == "4" || ipVersion == "both" {
			content += "DHCP=ipv4\n"
		}
		if ipVersion == "6" || ipVersion == "both" {
			content += "DHCP=ipv6\n"
		}
	} else {
		if v4, ok := params["new_ip_v4"].(string); ok && v4 != "" {
			content += fmt.Sprintf("\n[Address]\nAddress=%s\n", v4)
			if gw, ok := params["new_gateway_v4"].(string); ok && gw != "" {
				content += fmt.Sprintf("\n[Route]\nGateway=%s\n", gw)
			}
		}
	}
	if err := os.WriteFile(path, []byte(content), 0644); err != nil {
		return fmt.Errorf("writing networkd config: %w", err)
	}
	return runCmd("networkctl", "reload")
}

func applyDebianInterfaces(iface, mode, ipVersion string, params map[string]any) error {
	_ = runCmd("ifdown", iface)
	if mode == "dhcp" {
		return runCmd("ifup", iface)
	}
	if v4, ok := params["new_ip_v4"].(string); ok && v4 != "" && (ipVersion == "4" || ipVersion == "both") {
		if err := runCmd("ip", "addr", "add", v4, "dev", iface); err != nil {
			return err
		}
	}
	if v6, ok := params["new_ip_v6"].(string); ok && v6 != "" && (ipVersion == "6" || ipVersion == "both") {
		if err := runCmd("ip", "-6", "addr", "add", v6, "dev", iface); err != nil {
			return err
		}
	}
	return runCmd("ifup", iface)
}

func applyRHELIfcfg(iface, mode, ipVersion string, params map[string]any) error {
	cfgPath := fmt.Sprintf("/etc/sysconfig/network-scripts/ifcfg-%s", iface)
	content := fmt.Sprintf("DEVICE=%s\nONBOOT=yes\n", iface)
	if mode == "dhcp" {
		content += "BOOTPROTO=dhcp\n"
	} else {
		content += "BOOTPROTO=static\n"
		if v4, ok := params["new_ip_v4"].(string); ok && v4 != "" && (ipVersion == "4" || ipVersion == "both") {
			parts := strings.SplitN(v4, "/", 2)
			content += fmt.Sprintf("IPADDR=%s\n", parts[0])
			if len(parts) > 1 {
				content += fmt.Sprintf("PREFIX=%s\n", parts[1])
			}
		}
		if gw, ok := params["new_gateway_v4"].(string); ok && gw != "" {
			content += fmt.Sprintf("GATEWAY=%s\n", gw)
		}
		if v6, ok := params["new_ip_v6"].(string); ok && v6 != "" && (ipVersion == "6" || ipVersion == "both") {
			parts := strings.SplitN(v6, "/", 2)
			content += fmt.Sprintf("IPV6ADDR=%s\n", parts[0])
			if len(parts) > 1 {
				content += fmt.Sprintf("IPV6PREFIX=%s\n", parts[1])
			}
			content += "IPV6INIT=yes\n"
		}
	}
	if err := os.WriteFile(cfgPath, []byte(content), 0644); err != nil {
		return fmt.Errorf("writing ifcfg: %w", err)
	}
	_ = runCmd("ifdown", iface)
	return runCmd("ifup", iface)
}

func runCmd(name string, args ...string) error {
	out, err := exec.Command(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %v: %w (output: %s)", name, args, err, out)
	}
	return nil
}

func snapshotField(snapshot map[string]any, section, field, def string) string {
	if s, ok := snapshot[section].(map[string]any); ok {
		if v, ok := s[field].(string); ok && v != "" {
			return v
		}
	}
	return def
}
