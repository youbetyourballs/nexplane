//go:build windows

package changip

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"time"
)

var execCommandForRunW = exec.Command

func runCmdW(name string, args ...string) error {
	out, err := execCommandForRunW(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %v: %w (output: %s)", name, args, err, out)
	}
	return nil
}

// SnapshotW holds pre-change state on Windows.
type SnapshotW struct {
	Interface        string   `json:"interface"`
	IPv4Addresses    []string `json:"ip_v4_addresses"`
	DNSServers       []string `json:"dns_servers"`
	DNSSearchDomains []string `json:"dns_search_domains"`
	MTU              int      `json:"mtu"`
	NetworkManager   string   `json:"network_manager"`
}

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

	var dnsServers []string
	if raw, ok := params["dns_servers"].([]any); ok {
		for _, v := range raw {
			if s, ok := v.(string); ok {
				dnsServers = append(dnsServers, s)
			}
		}
	}

	snap, err := captureSnapshotW(iface)
	if err != nil {
		return nil, fmt.Errorf("capturing snapshot: %w", err)
	}

	if err := applyIPChangeW(iface, mode, ipVersion, params); err != nil {
		return nil, err
	}

	if err := configureDNSW(iface, dnsServers); err != nil {
		return nil, err
	}

	snapBytes, _ := json.Marshal(snap)
	var snapMap map[string]any
	json.Unmarshal(snapBytes, &snapMap)

	return map[string]any{
		"action":     "change_ip",
		"interface":  iface,
		"mode":       mode,
		"applied":    true,
		"snapshot":   snapMap,
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

	// Build rollback params from new snapshot fields.
	var ipv4 string
	if addrs, ok := snapshot["ip_v4_addresses"].([]any); ok && len(addrs) > 0 {
		ipv4, _ = addrs[0].(string)
	}
	gw4 := snapshotField(snapshot, "ipv4", "gateway", "")

	rollMode := "dhcp"
	if ipv4 != "" {
		rollMode = "static"
	}

	snapshotParams := map[string]any{
		"interface":      iface,
		"mode":           rollMode,
		"ip_version":     "4",
		"new_ip_v4":      ipv4,
		"new_gateway_v4": gw4,
	}

	// Restore DNS servers.
	if dnsRaw, ok := snapshot["dns_servers"].([]any); ok {
		var dns []string
		for _, v := range dnsRaw {
			if s, ok := v.(string); ok {
				dns = append(dns, s)
			}
		}
		if len(dns) > 0 {
			_ = configureDNSW(iface, dns)
		}
	}

	return map[string]any{"rolled_back": true}, applyIPChangeW(iface, rollMode, "4", snapshotParams)
}

func captureSnapshotW(iface string) (*SnapshotW, error) {
	s := &SnapshotW{Interface: iface, NetworkManager: "netsh"}

	// IPv4 addresses
	addrOut, _ := exec.Command("netsh", "interface", "ipv4", "show", "addresses", "name="+iface).Output()
	for _, line := range strings.Split(string(addrOut), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "IP Address:") {
			ip := strings.TrimSpace(strings.TrimPrefix(line, "IP Address:"))
			s.IPv4Addresses = append(s.IPv4Addresses, ip)
		}
	}

	// DNS servers
	dnsOut, _ := exec.Command("netsh", "interface", "ipv4", "show", "dnsservers", "name="+iface).Output()
	for _, line := range strings.Split(string(dnsOut), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "Statically Configured DNS Servers:") {
			srv := strings.TrimSpace(strings.TrimPrefix(line, "Statically Configured DNS Servers:"))
			if srv != "None" && srv != "" {
				s.DNSServers = append(s.DNSServers, srv)
			}
		} else if strings.HasPrefix(line, "Register with which suffix:") {
			// ignore
		} else if len(line) > 0 && !strings.Contains(line, ":") {
			// continuation DNS line
			s.DNSServers = append(s.DNSServers, line)
		}
	}

	// MTU
	mtuOut, _ := exec.Command("netsh", "interface", "ipv4", "show", "subinterfaces", iface).Output()
	for _, line := range strings.Split(string(mtuOut), "\n") {
		fields := strings.Fields(line)
		if len(fields) >= 1 {
			var mtu int
			if n, _ := fmt.Sscanf(fields[0], "%d", &mtu); n == 1 && mtu > 0 {
				s.MTU = mtu
				break
			}
		}
	}

	return s, nil
}

// configureDNSW sets DNS servers for a Windows interface via netsh.
func configureDNSW(iface string, servers []string) error {
	if len(servers) == 0 {
		return nil
	}
	// Set primary DNS as static.
	if err := runCmdW("netsh", "interface", "ipv4", "set", "dnsservers",
		"name="+iface, "static", servers[0], "primary"); err != nil {
		return fmt.Errorf("set primary DNS: %w", err)
	}
	// Add additional servers at increasing indexes.
	for i, srv := range servers[1:] {
		idx := fmt.Sprintf("index=%d", i+2)
		if err := runCmdW("netsh", "interface", "ipv4", "add", "dnsservers",
			"name="+iface, srv, idx); err != nil {
			return fmt.Errorf("add DNS server %s: %w", srv, err)
		}
	}
	return nil
}

// AddSecondaryIPW adds an additional IPv4 address to a Windows interface.
// ip is dotted-decimal, mask is dotted-decimal subnet mask.
func AddSecondaryIPW(iface, ip, mask string) error {
	return runCmdW("netsh", "interface", "ipv4", "add", "address",
		"name="+iface, ip, mask)
}

// RemoveSecondaryIPW removes a specific IPv4 address from a Windows interface.
func RemoveSecondaryIPW(iface, ip string) error {
	return runCmdW("netsh", "interface", "ipv4", "delete", "address",
		"name="+iface, ip)
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
