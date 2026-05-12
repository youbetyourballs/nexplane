package listpkgs

// Execute lists installed packages on the current OS.
// It delegates to executeOS which is defined per build tag.
func Execute(params map[string]any) (map[string]any, error) {
	return executeOS(params)
}
