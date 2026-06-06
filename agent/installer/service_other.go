//go:build !linux && !darwin

package installer

import "fmt"

func installService(binaryPath, controlPlane, secret string) error {
	return fmt.Errorf("service installation not supported on this platform")
}
