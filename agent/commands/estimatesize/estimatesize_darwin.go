//go:build darwin

package estimatesize

import "fmt"

func executeOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("estimate_size not supported on macOS")
}
