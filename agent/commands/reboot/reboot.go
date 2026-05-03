package reboot

// Execute is the entry point for "graceful_reboot".
func Execute(params map[string]any) (map[string]any, error) {
	return executeOS(params)
}

// Rollback for graceful_reboot is a no-op — a reboot cannot be undone.
func Rollback(params map[string]any) (map[string]any, error) {
	return map[string]any{
		"rolled_back": false,
		"reason":      "a reboot cannot be automatically rolled back",
	}, nil
}

// VerifyPostRebootExecute is the entry point for "verify_post_reboot".
func VerifyPostRebootExecute(params map[string]any) (map[string]any, error) {
	return verifyPostRebootOS(params)
}
