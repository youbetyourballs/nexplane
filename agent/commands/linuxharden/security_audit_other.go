//go:build !linux

package linuxharden

func trivyScanExecute(params map[string]any) (map[string]any, error) {
	return map[string]any{"error": "trivy_scan is Linux-only"}, nil
}
func trivyScanRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Linux-only"}, nil
}
func lynisAuditExecute(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "lynis_audit is Linux-only"}, nil
}
func lynisAuditRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Linux-only"}, nil
}
func openscapScanExecute(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "openscap_scan is Linux-only"}, nil
}
func openscapScanRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Linux-only"}, nil
}
func authorizedKeysAuditExecute(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Linux-only"}, nil
}
func authorizedKeysAuditRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Linux-only"}, nil
}
func sudoersAuditExecute(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Linux-only"}, nil
}
func sudoersAuditRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Linux-only"}, nil
}
func suidScanExecute(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Linux-only"}, nil
}
func suidScanRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Linux-only"}, nil
}
func sslCertInspectExecute(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Linux-only"}, nil
}
func sslCertInspectRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Linux-only"}, nil
}
