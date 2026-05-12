package linuxupgrade

// PreflightExecute checks readiness for a major OS upgrade.
// Returns current OS, target OS, running services, estimated duration, and warnings.
func PreflightExecute(params map[string]any) (map[string]any, error) {
	return osUpgradePreflightOS(params)
}

// VerifyExecute checks that the OS upgrade completed successfully.
// Verifies the new OS version and that pre-upgrade services are running.
func VerifyExecute(params map[string]any) (map[string]any, error) {
	return osUpgradeVerifyOS(params)
}
