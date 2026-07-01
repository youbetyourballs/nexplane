package tunnel

import (
	"fmt"
	"net"
	"strconv"
	"strings"
)

// Rule represents one allowlist entry, e.g. "192.168.0.0/24:80-443".
type Rule struct {
	// For IP / CIDR matching
	cidr *net.IPNet
	ip   net.IP // bare IP (no mask)

	// For hostname matching (empty string means not a hostname rule)
	hostname string

	// Port matching
	portMin int // 0 means wildcard
	portMax int
	portAny bool
}

// ParseAllowlist parses a slice of "host:port" strings into Rules.
// host may be CIDR, bare IP, or hostname.
// port may be "N", "N-M", or "*".
func ParseAllowlist(entries []string) ([]Rule, error) {
	rules := make([]Rule, 0, len(entries))
	for _, e := range entries {
		idx := strings.LastIndex(e, ":")
		if idx < 0 {
			return nil, fmt.Errorf("allowlist entry missing colon: %q", e)
		}
		hostPart := e[:idx]
		portPart := e[idx+1:]

		rule := Rule{}

		// Parse host part
		if strings.Contains(hostPart, "/") {
			// CIDR
			_, network, err := net.ParseCIDR(hostPart)
			if err != nil {
				return nil, fmt.Errorf("invalid CIDR %q: %w", hostPart, err)
			}
			rule.cidr = network
		} else if ip := net.ParseIP(hostPart); ip != nil {
			rule.ip = ip
		} else {
			rule.hostname = strings.ToLower(hostPart)
		}

		// Parse port part
		if portPart == "*" {
			rule.portAny = true
		} else if strings.Contains(portPart, "-") {
			parts := strings.SplitN(portPart, "-", 2)
			min, err1 := strconv.Atoi(parts[0])
			max, err2 := strconv.Atoi(parts[1])
			if err1 != nil || err2 != nil || min < 1 || max > 65535 || min > max {
				return nil, fmt.Errorf("invalid port range %q", portPart)
			}
			rule.portMin = min
			rule.portMax = max
		} else {
			p, err := strconv.Atoi(portPart)
			if err != nil || p < 1 || p > 65535 {
				return nil, fmt.Errorf("invalid port %q", portPart)
			}
			rule.portMin = p
			rule.portMax = p
		}

		rules = append(rules, rule)
	}
	return rules, nil
}

// IsAllowed returns true if the given host:port is permitted by at least one rule.
func IsAllowed(rules []Rule, host string, port int) bool {
	dialIP := net.ParseIP(host)

	for _, r := range rules {
		// Port check
		if !r.portAny {
			if port < r.portMin || port > r.portMax {
				continue
			}
		}

		// Host check
		if r.cidr != nil {
			if dialIP == nil {
				continue
			}
			if r.cidr.Contains(dialIP) {
				return true
			}
		} else if r.ip != nil {
			if dialIP == nil {
				continue
			}
			if r.ip.Equal(dialIP) {
				return true
			}
		} else {
			// hostname rule — only matches non-IP dials
			if dialIP != nil {
				continue
			}
			if strings.EqualFold(r.hostname, host) {
				return true
			}
		}
	}
	return false
}
