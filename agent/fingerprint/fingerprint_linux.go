//go:build linux

package fingerprint

import (
	"os"
	"strings"
)

func nativeID() (string, error) {
	data, err := os.ReadFile("/etc/machine-id")
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}
