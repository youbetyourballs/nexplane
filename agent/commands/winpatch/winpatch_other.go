//go:build !windows

package winpatch

import "fmt"

func applyPatchesOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_windows_patches requires Windows")
}

func rollbackPatchesOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_windows_patches requires Windows")
}

func auditPatchStatusOS(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_windows_patch_status requires Windows")
}
