package fingerprint

import (
	"crypto/sha256"
	"fmt"
	"net"
	"sort"
	"strings"
)

func macAddressHash() (string, error) {
	ifaces, err := net.Interfaces()
	if err != nil {
		return "", err
	}

	var macs []string
	for _, iface := range ifaces {
		if iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		if len(iface.HardwareAddr) == 0 {
			continue
		}
		macs = append(macs, iface.HardwareAddr.String())
	}

	if len(macs) == 0 {
		return "", fmt.Errorf("no non-loopback interfaces with MAC addresses found")
	}

	sort.Strings(macs)
	combined := strings.Join(macs, "|")
	hash := sha256.Sum256([]byte(combined))
	return fmt.Sprintf("%x", hash[:16]), nil
}

func sanitize(s string) string {
	return strings.Map(func(r rune) rune {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') ||
			(r >= '0' && r <= '9') || r == '-' || r == '_' {
			return r
		}
		return '-'
	}, strings.TrimSpace(s))
}
