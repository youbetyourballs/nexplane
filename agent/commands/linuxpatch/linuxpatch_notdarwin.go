//go:build !darwin

package linuxpatch

import "fmt"

func ApplyMacPatchesExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_mac_patches requires macOS")
}

func ApplyMacPatchesRollback(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("apply_mac_patches requires macOS")
}

func AuditPatchesMacExecute(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_mac_patches requires macOS")
}

func AuditPatchesMacRollback(_ map[string]any) (map[string]any, error) {
	return nil, fmt.Errorf("audit_mac_patches requires macOS")
}
