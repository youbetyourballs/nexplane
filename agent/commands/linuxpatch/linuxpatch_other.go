//go:build !linux && !darwin

package linuxpatch

import "fmt"

func applyPatchesOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_linux_patches requires Linux")
}

func rollbackPatchesOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_linux_patches requires Linux")
}

func auditPatchStatusOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_linux_patch_status requires Linux")
}
