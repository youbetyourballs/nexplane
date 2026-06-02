//go:build darwin

package linuxpatch

import "fmt"

func applyPatchesOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("use apply_mac_patches for macOS")
}

func rollbackPatchesOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("use apply_mac_patches rollback for macOS")
}

func auditPatchStatusOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("use audit_mac_patches for macOS")
}
