//go:build darwin

package changip

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

// execCommand is a hook for tests to replace exec.Command.
var execCommand = exec.Command

// DarwinSnapshot holds the pre-change network state for rollback.
type DarwinSnapshot struct {
	Interface   string   `json:"interface"`
	ServiceName string   `json:"service_name"`
	Mode        string   `json:"mode"` // "auto" or "manual"
	IPv4        string   `json:"ipv4"`
	Subnet      string   `json:"subnet"`
	Gateway     string   `json:"gateway"`
	DNS         []string `json:"dns"`
}

func executeOS(params map[string]any) (map[string]any, error) {
	iface, _ := params["interface"].(string)
	if iface == "" {
		iface = "en0"
	}
	mode, _ := params["mode"].(string)
	if mode == "" {
		mode = "auto"
	}

	snap, err := captureDarwinSnapshot(iface)
	if err != nil {
		return nil, fmt.Errorf("snapshot: %w", err)
	}

	switch mode {
	case "auto", "dhcp":
		if err := applyDHCP(snap.ServiceName); err != nil {
			return nil, err
		}
	case "manual":
		ip, _ := params["ip_address"].(string)
		subnet, _ := params["subnet_mask"].(string)
		gw, _ := params["gateway"].(string)
		if ip == "" {
			return nil, fmt.Errorf("ip_address required for manual mode")
		}
		if subnet == "" {
			subnet = "255.255.255.0"
		}
		if err := applyDarwinIPChange(snap.ServiceName, ip, subnet, gw); err != nil {
			return nil, err
		}
		if dnsRaw, ok := params["dns_servers"].([]any); ok {
			dns := make([]string, 0, len(dnsRaw))
			for _, d := range dnsRaw {
				if s, ok := d.(string); ok {
					dns = append(dns, s)
				}
			}
			if len(dns) > 0 {
				if err := applyDarwinDNS(snap.ServiceName, dns); err != nil {
					return nil, err
				}
			}
		}
	case "tailscale":
		// Tailscale manages its own interface (utun*); nothing to configure via networksetup
	default:
		return nil, fmt.Errorf("unsupported mode %q: must be auto, manual, or tailscale", mode)
	}

	snapJSON, _ := json.Marshal(snap)
	return map[string]any{
		"interface":    iface,
		"service_name": snap.ServiceName,
		"mode":         mode,
		"snapshot":     string(snapJSON),
		"applied_at":   time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	snapStr, ok := params["snapshot"].(string)
	if !ok || snapStr == "" {
		return nil, fmt.Errorf("snapshot is required for rollback")
	}
	var snap DarwinSnapshot
	if err := json.Unmarshal([]byte(snapStr), &snap); err != nil {
		return nil, fmt.Errorf("parse snapshot: %w", err)
	}
	if snap.ServiceName == "" {
		svc, err := serviceForInterface(snap.Interface)
		if err != nil {
			return nil, fmt.Errorf("serviceForInterface: %w", err)
		}
		snap.ServiceName = svc
	}

	switch snap.Mode {
	case "auto", "dhcp", "":
		if err := applyDHCP(snap.ServiceName); err != nil {
			return nil, err
		}
	default:
		if err := applyDarwinIPChange(snap.ServiceName, snap.IPv4, snap.Subnet, snap.Gateway); err != nil {
			return nil, err
		}
	}
	if len(snap.DNS) > 0 {
		if err := applyDarwinDNS(snap.ServiceName, snap.DNS); err != nil {
			return nil, err
		}
	}
	return map[string]any{"rolled_back": true, "interface": snap.Interface}, nil
}

func serviceForInterface(iface string) (string, error) {
	out, err := runNS("-listallhardwareports")
	if err != nil {
		return "", err
	}
	lines := strings.Split(out, "\n")
	var lastName string
	for _, l := range lines {
		l = strings.TrimSpace(l)
		if strings.HasPrefix(l, "Hardware Port:") {
			lastName = strings.TrimSpace(strings.TrimPrefix(l, "Hardware Port:"))
		}
		if strings.HasPrefix(l, "Device:") {
			dev := strings.TrimSpace(strings.TrimPrefix(l, "Device:"))
			if dev == iface {
				return lastName, nil
			}
		}
	}
	return iface, nil
}

func captureDarwinSnapshot(iface string) (*DarwinSnapshot, error) {
	svc, err := serviceForInterface(iface)
	if err != nil {
		return nil, err
	}
	snap := &DarwinSnapshot{Interface: iface, ServiceName: svc, Mode: "manual"}

	out, _ := runNS("-getinfo", svc)
	if strings.Contains(out, "DHCP") {
		snap.Mode = "auto"
	}
	for _, line := range strings.Split(out, "\n") {
		line = strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(line, "IP address:"):
			snap.IPv4 = strings.TrimSpace(strings.TrimPrefix(line, "IP address:"))
		case strings.HasPrefix(line, "Subnet mask:"):
			snap.Subnet = strings.TrimSpace(strings.TrimPrefix(line, "Subnet mask:"))
		case strings.HasPrefix(line, "Router:"):
			snap.Gateway = strings.TrimSpace(strings.TrimPrefix(line, "Router:"))
		}
	}

	dnsOut, _ := runNS("-getdnsservers", svc)
	for _, l := range strings.Split(dnsOut, "\n") {
		l = strings.TrimSpace(l)
		if l != "" && !strings.Contains(l, "There aren't") {
			snap.DNS = append(snap.DNS, l)
		}
	}
	return snap, nil
}

func applyDHCP(svc string) error {
	_, err := runNS("-setdhcp", svc)
	return err
}

func applyDarwinIPChange(svc, ip, subnet, gw string) error {
	_, err := runNS("-setmanual", svc, ip, subnet, gw)
	return err
}

func applyDarwinDNS(svc string, dns []string) error {
	args := append([]string{"-setdnsservers", svc}, dns...)
	_, err := runNS(args...)
	return err
}

func runNS(args ...string) (string, error) {
	cmd := execCommand("networksetup", args...)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return "", fmt.Errorf("networksetup %v: %w (output: %s)", args, err, out)
	}
	return string(out), nil
}
