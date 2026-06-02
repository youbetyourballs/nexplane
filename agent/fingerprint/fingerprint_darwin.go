//go:build darwin

package fingerprint

import (
	"os/exec"
	"strings"
)

func nativeID() (string, error) {
	out, err := exec.Command("ioreg", "-rd1", "-c", "IOPlatformExpertDevice").Output()
	if err != nil {
		return macAddressHash()
	}
	for _, line := range strings.Split(string(out), "\n") {
		if strings.Contains(line, "IOPlatformUUID") {
			parts := strings.Split(line, "\"")
			if len(parts) >= 4 {
				uuid := strings.TrimSpace(parts[len(parts)-2])
				if uuid != "" {
					return sanitize(uuid), nil
				}
			}
		}
	}
	return macAddressHash()
}
