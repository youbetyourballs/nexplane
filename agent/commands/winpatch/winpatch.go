package winpatch

import "fmt"

var validModes = map[string]bool{
	"security_only": true,
	"kb":            true,
}

// ApplyWindowsPatchesExecute installs Windows Updates on the host.
func ApplyWindowsPatchesExecute(params map[string]any) (map[string]any, error) {
	mode, _ := params["mode"].(string)
	if !validModes[mode] {
		return nil, fmt.Errorf("mode must be security_only or kb; got %q", mode)
	}
	if mode == "kb" {
		if kb, _ := params["kb_id"].(string); kb == "" {
			return nil, fmt.Errorf("kb_id is required when mode=kb")
		}
	}
	return applyPatchesOS(params)
}

// ApplyWindowsPatchesRollback uninstalls KBs installed during execute.
func ApplyWindowsPatchesRollback(params map[string]any) (map[string]any, error) {
	return rollbackPatchesOS(params)
}

// AuditWindowsPatchStatusExecute returns installed KBs, pending updates, and reboot-pending state.
func AuditWindowsPatchStatusExecute(params map[string]any) (map[string]any, error) {
	return auditPatchStatusOS(params)
}
