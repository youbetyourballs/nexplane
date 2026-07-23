// Package winupgrade implements Windows Server in-place upgrade agent commands.
// On non-Windows platforms only the stubs in winupgrade_other.go are compiled.
package winupgrade

// Command names this package handles (registered in executor.go).
const (
	CmdPreflight   = "windows_preflight_os_upgrade"
	CmdVSSCreate   = "windows_vss_create_shadow"
	CmdStartUpgrade = "windows_start_os_upgrade"
	CmdVerify      = "windows_verify_os_upgrade"
	CmdVSSRestore  = "windows_vss_restore"
)

func strParam(params map[string]any, key string) string {
	v, _ := params[key].(string)
	return v
}

func boolParam(params map[string]any, key string) bool {
	v, _ := params[key].(bool)
	return v
}
