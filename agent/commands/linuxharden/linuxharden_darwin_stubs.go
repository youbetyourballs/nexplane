//go:build darwin

package linuxharden

func trivyScanExecute(_ map[string]any) (map[string]any, error) {
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
func sslCertInspectExecute(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "ssl_cert_inspect requires Linux (uses ss)"}, nil
}
func sslCertInspectRollback(_ map[string]any) (map[string]any, error) {
	return map[string]any{"error": "Linux-only"}, nil
}
