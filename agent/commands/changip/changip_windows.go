//go:build windows

package changip

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
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
	GatewayV4        string   `json:"gateway_v4"`
	DNSServers       []string `json:"dns_servers"`
	DNSSearchDomains []string `json:"dns_search_domains"`
	MTU              int      `json:"mtu"`
	NetworkManager   string   `json:"network_manager"`
}

func executeOS(params map[string]any) (map[string]any, error) {
	iface, _ := params["interface"].(string)
	if iface == "" {
		return nil, fmt.Errorf("interface is required")
	}

	// Derive mode from params (new_ip_v4 present = static, else dhcp).
	mode := "static"
	if v, ok := params["new_ip_v4"].(string); !ok || v == "" {
		mode = "dhcp"
	}

	method, _ := params["method"].(string)
	if method == "" {
		method = "auto"
	}

	commitTimerSecs := paramIntW(params, "commit_timer_seconds", 30)
	if commitTimerSecs < 10 {
		commitTimerSecs = 10
	}
	if commitTimerSecs > 300 {
		commitTimerSecs = 300
	}
	probeIntervalSecs := paramIntW(params, "probe_interval_seconds", 5)
	probeURL, _ := params["probe_url"].(string)
	if probeURL == "" {
		probeURL = os.Getenv("NP_CONTROL_PLANE")
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
	snapBytes, _ := json.Marshal(snap)
	var snapMap map[string]any
	json.Unmarshal(snapBytes, &snapMap)

	// Auto method selection: prefer Tailscale if active, else commit_timer.
	if method == "auto" {
		_, tsActive := IsTailscaleActive()
		if tsActive && probeURL != "" && IsControlPlaneReachableViaTailscale(probeURL, 5*time.Second) {
			method = "tailscale"
		} else {
			method = "commit_timer"
		}
	}

	result := map[string]any{
		"action":      "change_ip",
		"interface":   iface,
		"mode":        mode,
		"applied":     false,
		"snapshot":    snapMap,
		"applied_at":  time.Now().UTC().Format(time.RFC3339),
		"method_used": method,
	}

	switch method {
	case "tailscale":
		tsIP, _ := IsTailscaleActive()
		if err := applyIPChangeW(iface, mode, "4", params); err != nil {
			return nil, err
		}
		if err := configureDNSW(iface, dnsServers); err != nil {
			return nil, err
		}
		result["applied"] = true
		result["tailscale_ip"] = tsIP

	case "secondary_swap":
		newCIDR, _ := params["new_ip_v4"].(string)
		if newCIDR == "" {
			return nil, fmt.Errorf("new_ip_v4 required for secondary_swap")
		}
		parts := strings.SplitN(newCIDR, "/", 2)
		mask := cidrToMask(parts)
		if err := AddSecondaryIPW(iface, parts[0], mask); err != nil {
			return nil, fmt.Errorf("add secondary IP: %w", err)
		}
		for _, old := range snap.IPv4Addresses {
			_ = RemoveSecondaryIPW(iface, old)
		}
		if err := configureDNSW(iface, dnsServers); err != nil {
			return nil, err
		}
		result["applied"] = true
		result["secondary_ip_added"] = newCIDR

	case "commit_timer":
		pr := PendingRollback{
			JobID:             fmt.Sprintf("job-%d", time.Now().UnixNano()),
			ExpiresAt:         time.Now().Add(time.Duration(commitTimerSecs) * time.Second),
			RollbackParams:    snapMap,
			ProbeURL:          probeURL,
			ProbeIntervalSecs: probeIntervalSecs,
			ProbeTimeoutSecs:  3,
		}
		path := activePendingRollbackPath()
		_, err := startDeadManSwitch(pr, path, func() {
			_, _ = rollbackOS(map[string]any{"snapshot": snapMap, "interface": iface})
		})
		if err != nil {
			return nil, fmt.Errorf("start dead man's switch: %w", err)
		}
		if err := applyIPChangeW(iface, mode, "4", params); err != nil {
			return nil, err
		}
		if err := configureDNSW(iface, dnsServers); err != nil {
			return nil, err
		}
		result["applied"] = true
		result["commit_timer_started"] = true

	case "manual":
		if err := applyIPChangeW(iface, mode, "4", params); err != nil {
			return nil, err
		}
		result["applied"] = true
		result["requires_confirmation"] = true

	default:
		if err := applyIPChangeW(iface, mode, "4", params); err != nil {
			return nil, err
		}
		if err := configureDNSW(iface, dnsServers); err != nil {
			return nil, err
		}
		result["applied"] = true
	}

	return result, nil
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

	var ipv4 string
	if addrs, ok := snapshot["ip_v4_addresses"].([]any); ok && len(addrs) > 0 {
		ipv4, _ = addrs[0].(string)
	}
	gw4, _ := snapshot["gateway_v4"].(string)

	rollMode := "dhcp"
	if ipv4 != "" {
		rollMode = "static"
	}

	snapshotParams := map[string]any{
		"interface":      iface,
		"new_ip_v4":      ipv4,
		"new_gateway_v4": gw4,
	}
	if err := applyIPChangeW(iface, rollMode, "4", snapshotParams); err != nil {
		return nil, err
	}

	if dnsRaw, ok := snapshot["dns_servers"].([]any); ok && len(dnsRaw) > 0 {
		servers := make([]string, 0, len(dnsRaw))
		for _, s := range dnsRaw {
			if str, ok := s.(string); ok {
				servers = append(servers, str)
			}
		}
		if err := configureDNSW(iface, servers); err != nil {
			log.Printf("Warning: DNS restore during rollback failed: %v", err)
		}
	}

	return map[string]any{"rolled_back": true}, nil
}

func captureSnapshotW(iface string) (*SnapshotW, error) {
	s := &SnapshotW{Interface: iface, NetworkManager: "netsh"}

	// IPv4 addresses (IP only, not CIDR — Windows netsh doesn't show prefix length inline)
	addrOut, _ := exec.Command("netsh", "interface", "ipv4", "show", "addresses", "name="+iface).Output()
	for _, line := range strings.Split(string(addrOut), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "IP Address:") {
			ip := strings.TrimSpace(strings.TrimPrefix(line, "IP Address:"))
			if ip != "" {
				s.IPv4Addresses = append(s.IPv4Addresses, ip)
			}
		}
		if strings.HasPrefix(line, "Default Gateway:") {
			gw := strings.TrimSpace(strings.TrimPrefix(line, "Default Gateway:"))
			if gw != "" && s.GatewayV4 == "" {
				s.GatewayV4 = gw
			}
		}
	}

	// DNS servers
	dnsOut, _ := exec.Command("netsh", "interface", "ipv4", "show", "dnsservers", "name="+iface).Output()
	inDNS := false
	for _, line := range strings.Split(string(dnsOut), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "Statically Configured DNS Servers:") {
			srv := strings.TrimSpace(strings.TrimPrefix(line, "Statically Configured DNS Servers:"))
			if srv != "None" && srv != "" {
				s.DNSServers = append(s.DNSServers, srv)
			}
			inDNS = true
		} else if inDNS && line != "" && !strings.Contains(line, ":") {
			s.DNSServers = append(s.DNSServers, line)
		} else if strings.Contains(line, ":") {
			inDNS = false
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
	if err := runCmdW("netsh", "interface", "ipv4", "set", "dnsservers",
		"name="+iface, "static", servers[0], "primary"); err != nil {
		return fmt.Errorf("set primary DNS: %w", err)
	}
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
		if gw6, ok := params["new_gateway_v6"].(string); ok && gw6 != "" {
			runCmdW("netsh", "interface", "ipv6", "add", "route", "::/0", "interface="+iface, "nexthop="+gw6)
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

func paramIntW(params map[string]any, key string, def int) int {
	switch v := params[key].(type) {
	case int:
		return v
	case float64:
		return int(v)
	case int64:
		return int(v)
	}
	return def
}
