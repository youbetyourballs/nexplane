//go:build linux

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

// execCommand is a hook for tests to replace exec.Command.
var execCommand = exec.Command

// execCommandForRun is the hook used by runCmd (and configureDNS).
var execCommandForRun = exec.Command

// Snapshot holds everything needed to restore a network interface.
type Snapshot struct {
	Interface        string       `json:"interface"`
	IPv4Addresses    []string     `json:"ip_v4_addresses"`
	IPv6Addresses    []string     `json:"ip_v6_addresses"`
	GatewayV4        string       `json:"gateway_v4"`
	GatewayV6        string       `json:"gateway_v6"`
	DNSServers       []string     `json:"dns_servers"`
	DNSSearchDomains []string     `json:"dns_search_domains"`
	Routes           []RouteEntry `json:"routes"`
	MTU              int          `json:"mtu"`
	NetworkManager   string       `json:"network_manager"`
	ConnectionName   string       `json:"connection_name"`
}

// RouteEntry is one line from `ip route show dev {iface}`.
type RouteEntry struct {
	Dst string `json:"dst"`
	Gw  string `json:"gw"`
	Dev string `json:"dev"`
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

	// --- New parameters ---
	method, _ := params["method"].(string)
	if method == "" {
		method = "auto"
	}
	commitTimerSecs := paramInt(params, "commit_timer_seconds", 30)
	if commitTimerSecs < 10 {
		commitTimerSecs = 10
	}
	if commitTimerSecs > 300 {
		commitTimerSecs = 300
	}
	probeIntervalSecs := paramInt(params, "probe_interval_seconds", 5)
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
	var dnsSearchDomains []string
	if raw, ok := params["dns_search_domains"].([]any); ok {
		for _, v := range raw {
			if s, ok := v.(string); ok {
				dnsSearchDomains = append(dnsSearchDomains, s)
			}
		}
	}

	// --- Capture snapshot before any change ---
	snap, err := captureSnapshot(iface)
	if err != nil {
		return nil, fmt.Errorf("capturing snapshot: %w", err)
	}
	snapBytes, _ := json.Marshal(snap)
	var snapMap map[string]any
	json.Unmarshal(snapBytes, &snapMap)

	nm := detectNetworkManager()

	// --- Auto method selection ---
	if method == "auto" {
		tsIP, tsActive := IsTailscaleActive()
		if tsActive && probeURL != "" && IsControlPlaneReachableViaTailscale(probeURL, 5*time.Second) {
			_ = tsIP
			method = "tailscale"
		} else {
			// Fall through: secondary_swap requires routability check (not implemented here — default to commit_timer).
			method = "commit_timer"
		}
	}

	// --- Dispatch ---
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
		if err := applyIPChange(nm, iface, mode, ipVersion, params); err != nil {
			return nil, err
		}
		if err := configureDNS(nm, iface, snap.ConnectionName, dnsServers, dnsSearchDomains); err != nil {
			return nil, err
		}
		result["applied"] = true
		result["tailscale_ip"] = tsIP

	case "secondary_swap":
		newCIDR, _ := params["new_ip_v4"].(string)
		if newCIDR == "" {
			return nil, fmt.Errorf("new_ip_v4 required for secondary_swap")
		}
		if err := AddSecondaryIP(iface, newCIDR); err != nil {
			return nil, fmt.Errorf("add secondary IP: %w", err)
		}
		// Remove old primary IPs.
		for _, old := range snap.IPv4Addresses {
			_ = RemoveSecondaryIP(iface, old)
		}
		if err := configureDNS(nm, iface, snap.ConnectionName, dnsServers, dnsSearchDomains); err != nil {
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
		if err := applyIPChange(nm, iface, mode, ipVersion, params); err != nil {
			return nil, err
		}
		if err := configureDNS(nm, iface, snap.ConnectionName, dnsServers, dnsSearchDomains); err != nil {
			return nil, err
		}
		result["applied"] = true
		result["commit_timer_started"] = true

	case "manual":
		if err := applyIPChange(nm, iface, mode, ipVersion, params); err != nil {
			return nil, err
		}
		result["applied"] = true
		result["requires_confirmation"] = true

	default:
		// Backward-compatible: apply change directly.
		if err := applyIPChange(nm, iface, mode, ipVersion, params); err != nil {
			return nil, err
		}
		if err := configureDNS(nm, iface, snap.ConnectionName, dnsServers, dnsSearchDomains); err != nil {
			return nil, err
		}
		result["applied"] = true
	}

	return result, nil
}

// paramInt reads an int parameter from params, returning def if missing or wrong type.
func paramInt(params map[string]any, key string, def int) int {
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

	// Build rollback params from new snapshot fields.
	var ipv4 string
	if addrs, ok := snapshot["ip_v4_addresses"].([]any); ok && len(addrs) > 0 {
		ipv4, _ = addrs[0].(string)
	}
	var ipv6 string
	if addrs, ok := snapshot["ip_v6_addresses"].([]any); ok && len(addrs) > 0 {
		ipv6, _ = addrs[0].(string)
	}
	gw4, _ := snapshot["gateway_v4"].(string)
	gw6, _ := snapshot["gateway_v6"].(string)

	rollMode := "dhcp"
	if ipv4 != "" {
		rollMode = "static"
	}

	snapshotParams := map[string]any{
		"interface":      iface,
		"mode":           rollMode,
		"ip_version":     "both",
		"new_ip_v4":      ipv4,
		"new_ip_v6":      ipv6,
		"new_gateway_v4": gw4,
		"new_gateway_v6": gw6,
	}
	if err := applyIPChange(method, iface, rollMode, "both", snapshotParams); err != nil {
		return nil, err
	}

	if dnsServers, ok := snapshot["dns_servers"].([]any); ok && len(dnsServers) > 0 {
		servers := make([]string, 0, len(dnsServers))
		for _, s := range dnsServers {
			if str, ok := s.(string); ok {
				servers = append(servers, str)
			}
		}
		domains := []string{}
		if dd, ok := snapshot["dns_search_domains"].([]any); ok {
			for _, d := range dd {
				if str, ok := d.(string); ok {
					domains = append(domains, str)
				}
			}
		}
		nm, _ := snapshot["network_manager"].(string)
		_ = nm
		conName, _ := snapshot["connection_name"].(string)
		if iface == "" {
			if ifaceParam, ok := params["interface"].(string); ok {
				iface = ifaceParam
			}
		}
		if err := configureDNS(method, iface, conName, servers, domains); err != nil {
			log.Printf("Warning: DNS restore during rollback failed: %v", err)
		}
	}

	return map[string]any{"rolled_back": true}, nil
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

func captureSnapshot(iface string) (*Snapshot, error) {
	s := &Snapshot{Interface: iface}

	// --- IPv4 and IPv6 addresses ---
	addrOut, err := execCommand("ip", "-o", "addr", "show", iface).Output()
	if err == nil {
		for _, line := range strings.Split(strings.TrimSpace(string(addrOut)), "\n") {
			fields := strings.Fields(line)
			// fields: index iface inet|inet6 cidr ...
			if len(fields) < 4 {
				continue
			}
			family := fields[2]
			cidr := fields[3]
			switch family {
			case "inet":
				s.IPv4Addresses = append(s.IPv4Addresses, cidr)
			case "inet6":
				s.IPv6Addresses = append(s.IPv6Addresses, cidr)
			}
		}
	}

	// --- Routes ---
	routeOut, _ := execCommand("ip", "route", "show", "dev", iface).Output()
	for _, line := range strings.Split(strings.TrimSpace(string(routeOut)), "\n") {
		if line == "" {
			continue
		}
		fields := strings.Fields(line)
		entry := RouteEntry{Dst: fields[0], Dev: iface}
		for i, f := range fields {
			if f == "via" && i+1 < len(fields) {
				entry.Gw = fields[i+1]
			}
		}
		if entry.Dst == "default" {
			s.GatewayV4 = entry.Gw
		}
		s.Routes = append(s.Routes, entry)
	}

	// --- MTU ---
	linkOut, _ := execCommand("ip", "link", "show", iface).Output()
	for _, line := range strings.Split(string(linkOut), "\n") {
		if strings.Contains(line, "mtu") {
			fields := strings.Fields(line)
			for i, f := range fields {
				if f == "mtu" && i+1 < len(fields) {
					fmt.Sscanf(fields[i+1], "%d", &s.MTU)
				}
			}
		}
	}

	// --- Network manager type and connection name ---
	nm := detectNetworkManager()
	switch nm {
	case nmNetworkManager:
		s.NetworkManager = "NetworkManager"
		conOut, _ := execCommand("nmcli", "-t", "-f", "NAME,DEVICE", "con", "show", "--active").Output()
		for _, line := range strings.Split(string(conOut), "\n") {
			parts := strings.SplitN(line, ":", 2)
			if len(parts) == 2 && strings.TrimSpace(parts[1]) == iface {
				s.ConnectionName = strings.TrimSpace(parts[0])
			}
		}
	case nmSystemd:
		s.NetworkManager = "systemd-networkd"
	case nmDebian:
		s.NetworkManager = "interfaces"
	default:
		s.NetworkManager = "ifcfg"
	}

	// --- DNS: try resolvectl first, fall back to /etc/resolv.conf ---
	resOut, err := execCommand("resolvectl", "status", iface).Output()
	if err == nil {
		for _, line := range strings.Split(string(resOut), "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, "DNS Servers:") {
				raw := strings.TrimPrefix(line, "DNS Servers:")
				for _, srv := range strings.Fields(raw) {
					s.DNSServers = append(s.DNSServers, strings.TrimSpace(srv))
				}
			}
			if strings.HasPrefix(line, "DNS Domain:") {
				raw := strings.TrimPrefix(line, "DNS Domain:")
				for _, d := range strings.Fields(raw) {
					s.DNSSearchDomains = append(s.DNSSearchDomains, strings.TrimSpace(d))
				}
			}
		}
	} else {
		rcData, _ := os.ReadFile("/etc/resolv.conf")
		for _, line := range strings.Split(string(rcData), "\n") {
			line = strings.TrimSpace(line)
			if strings.HasPrefix(line, "nameserver ") {
				s.DNSServers = append(s.DNSServers, strings.TrimPrefix(line, "nameserver "))
			}
			if strings.HasPrefix(line, "search ") {
				for _, d := range strings.Fields(strings.TrimPrefix(line, "search ")) {
					s.DNSSearchDomains = append(s.DNSSearchDomains, d)
				}
			}
		}
	}

	return s, nil
}

// configureDNS configures DNS servers and search domains for an interface.
// conName is the NetworkManager connection name (used only when nm == nmNetworkManager).
func configureDNS(nm networkManager, iface, conName string, servers, searchDomains []string) error {
	if len(servers) == 0 {
		return nil
	}
	switch nm {
	case nmNetworkManager:
		con := conName
		if con == "" {
			con = iface
		}
		dnsVal := strings.Join(servers, " ")
		if err := runCmd("nmcli", "con", "mod", con, "ipv4.dns", dnsVal); err != nil {
			return fmt.Errorf("nmcli ipv4.dns: %w", err)
		}
		if err := runCmd("nmcli", "con", "mod", con, "ipv6.dns", dnsVal); err != nil {
			return fmt.Errorf("nmcli ipv6.dns: %w", err)
		}
		if len(searchDomains) > 0 {
			if err := runCmd("nmcli", "con", "mod", con, "ipv4.dns-search", strings.Join(searchDomains, " ")); err != nil {
				return fmt.Errorf("nmcli ipv4.dns-search: %w", err)
			}
		}
		return runCmd("nmcli", "con", "up", con)

	case nmSystemd:
		args := append([]string{"dns", iface}, servers...)
		if err := runCmd("resolvectl", args...); err != nil {
			return fmt.Errorf("resolvectl dns: %w", err)
		}
		if len(searchDomains) > 0 {
			domArgs := append([]string{"domain", iface}, searchDomains...)
			if err := runCmd("resolvectl", domArgs...); err != nil {
				return fmt.Errorf("resolvectl domain: %w", err)
			}
		}
		return nil

	default:
		// /etc/resolv.conf fallback: preserve non-nameserver lines, rewrite nameserver lines.
		rcData, _ := os.ReadFile("/etc/resolv.conf")
		var kept []string
		for _, line := range strings.Split(string(rcData), "\n") {
			if !strings.HasPrefix(strings.TrimSpace(line), "nameserver") &&
				!strings.HasPrefix(strings.TrimSpace(line), "search") {
				kept = append(kept, line)
			}
		}
		for _, srv := range servers {
			kept = append(kept, "nameserver "+srv)
		}
		if len(searchDomains) > 0 {
			kept = append(kept, "search "+strings.Join(searchDomains, " "))
		}
		return os.WriteFile("/etc/resolv.conf", []byte(strings.Join(kept, "\n")+"\n"), 0644)
	}
}

// AddSecondaryIP adds an additional IP to an interface without removing existing ones.
// cidr must be in CIDR notation, e.g. "10.0.0.200/24".
func AddSecondaryIP(iface, cidr string) error {
	return runCmd("ip", "addr", "add", cidr, "dev", iface)
}

// RemoveSecondaryIP removes a specific IP from an interface.
// cidr must be in CIDR notation, e.g. "10.0.0.200/24".
func RemoveSecondaryIP(iface, cidr string) error {
	return runCmd("ip", "addr", "del", cidr, "dev", iface)
}

// GetInterfaceAddresses returns all current IPs (IPv4 and IPv6) on an interface
// in CIDR notation.
func GetInterfaceAddresses(iface string) ([]string, error) {
	out, err := execCommand("ip", "-o", "addr", "show", iface).Output()
	if err != nil {
		return nil, fmt.Errorf("ip addr show %s: %w", iface, err)
	}
	var addrs []string
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 4 {
			continue
		}
		family := fields[2]
		if family == "inet" || family == "inet6" {
			addrs = append(addrs, fields[3])
		}
	}
	return addrs, nil
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
	out, err := execCommandForRun(name, args...).CombinedOutput()
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
